from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from time import monotonic, time_ns
from typing import AsyncIterator, Callable, Literal
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import Field

from pipecheckkk_ai.agent import IntegratedAgent
from pipecheckkk_ai.cloud_publisher import CloudTelemetryPublisher
from pipecheckkk_ai.frame_stream_publisher import CloudFrameStreamPublisher
from pipecheckkk_ai.command.bridge import CommandBridge
from pipecheckkk_ai.command.edge_client import HttpEdgeClient
from pipecheckkk_ai.control import DriveController
from pipecheckkk_ai.hermes_relay import HermesInspectionRelay
from pipecheckkk_ai.decision.qwen2_5_vl_3b_instruct_awq.adapter import (
    Qwen25Vl3BAwqProvider,
)
from pipecheckkk_ai.local_planner import LocalFreeSpacePlanner
from pipecheckkk_ai.navigation import (
    NavigationRouter,
    NavigationStatus,
    NavigationVersion,
)
from pipecheckkk_ai.model_registry import get_model_spec, model_files_present
from pipecheckkk_ai.models import (
    ActiveModelStatus,
    AgentDecision,
    AgentEvent,
    ApiModel,
    CommandBridgeStatus,
    DriveObservation,
    DriveStatus,
    ImuTelemetry,
    MissionState,
    MissionSnapshot,
    ModelRuntimeStatus,
    MotorOutput,
    ProgressAnnouncement,
    ProgressEventType,
    TelemetryObservation,
    utc_now,
)
from pipecheckkk_ai.perception.base import PerceptionProvider
from pipecheckkk_ai.perception.registry import create_perception_provider
from pipecheckkk_ai.progress_summary import (
    DEFAULT_PERIODIC_PROGRESS_INTERVAL_S,
    PeriodicProgressTrigger,
    ProgressAnnouncementProvider,
    ProgressAnnouncementService,
    ProgressEvent,
    ProgressEventDetector,
)
from pipecheckkk_ai.vision.yolo_detector import YoloDetector
from pipecheckkk_ai.vision.yolo_overlay import render_yolo_overlay
from pipecheckkk_ai.yolo_history import YoloCaptureStore


LOGGER = logging.getLogger(__name__)


def _yolo_detection_event_id(detections: list[dict[str, object]]) -> str | None:
    spatial_keys: list[tuple[str, int, int, int, int]] = []
    for detection in detections:
        bbox = detection.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(str(value)) for value in bbox)
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        spatial_keys.append(
            (
                str(detection.get("label") or "OTHER").lower(),
                round(((x1 + x2) / 2) / 128),
                round(((y1 + y2) / 2) / 128),
                round((x2 - x1) / 128),
                round((y2 - y1) / 128),
            )
        )
    if not spatial_keys:
        return None
    canonical = json.dumps(sorted(spatial_keys), separators=(",", ":"), ensure_ascii=True)
    return f"yolo-{hashlib.sha256(canonical.encode('ascii')).hexdigest()[:20]}"


def _automatic_yolo_db_publish_allowed(step: object) -> bool:
    """Allow automatic defect detection only before the 180-degree return phase."""
    return not isinstance(step, int) or isinstance(step, bool) or step <= 19


def _sanitize_dashboard_log_text(value: object) -> str:
    text = str(value or "")
    for source, replacement in (
        ("Discord Hermes VL", "AIagent"),
        ("Hermes VL", "AIagent"),
        ("헤르메스VL", "AIagent"),
        ("Discord VL", "AIagent"),
        ("기록1", ""),
        ("inspection-record-001.json", ""),
    ):
        text = text.replace(source, replacement)
    return " ".join(text.split())


def _cloud_robot_log_payload(
    *, category: str, level: str, message: str, timestamp: str
) -> dict[str, object] | None:
    """Map only VL image analysis into the Cloud robot-log surface."""
    if category != "VL_DECISION":
        return None
    analysis = message.split("\n대처 동작 :", 1)[0].strip()
    return {
        "command_source": "이미지 분석",
        "agent_state": level,
        "note": analysis,
        "logged_at": timestamp,
    }


def _normalize_cloud_event_cursor(
    *,
    uploaded_cursor: int,
    current_cursor: int,
    buffered_event_count: int,
    identity_changed: bool,
) -> int:
    """Align a publisher cursor with a resettable, bounded per-mission event buffer."""
    buffer_start = max(0, current_cursor - buffered_event_count)
    if identity_changed or uploaded_cursor < buffer_start or uploaded_cursor > current_cursor:
        return buffer_start
    return uploaded_cursor


def _short_stage_status(action: str) -> str:
    compact = "".join(action.split())
    if "회피" in compact:
        return "장애물 회피 중"
    if "복구" in compact:
        return "경로 복구 중"
    if "360" in compact or "주변" in compact or "탐색" in compact:
        return "주변 탐색 중"
    if "왼쪽" in compact and "회전" in compact:
        return "왼쪽 회전 중"
    if "오른쪽" in compact and "회전" in compact:
        return "오른쪽 회전 중"
    if "전진" in compact:
        return "전진 중"
    if "정지" in compact or "중단" in compact:
        return "안전 정지"
    return "점검 진행 중"


def _parse_front_distance(raw_distance: object) -> float | None:
    if raw_distance is None:
        return None
    if not isinstance(raw_distance, (int, float)) or isinstance(raw_distance, bool):
        raise RuntimeError("invalid ultrasonic distance from Pi Edge")
    distance_m = float(raw_distance)
    if not math.isfinite(distance_m) or distance_m < 0.0:
        raise RuntimeError("invalid ultrasonic distance from Pi Edge")
    return distance_m


def _validate_v2_imu_status(imu_status: dict[str, object]) -> None:
    expected = {
        "yawStepDeg": 10.0,
    }
    for key, required in expected.items():
        value = imu_status.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) != required
        ):
            raise RuntimeError("Pi Edge violates the fixed V2 IMU contract")
    if (
        imu_status.get("pitchStopDeg") is not None
        or imu_status.get("warningPitchDeg") is not None
        or imu_status.get("rollStopDeg") is not None
    ):
        raise RuntimeError("Pi Edge violates the monitor-only attitude V2 IMU contract")
    if imu_status.get("yawLimitDeg") is not None:
        raise RuntimeError("Pi Edge violates the unlimited-yaw V2 IMU contract")


class StartMissionRequest(ApiModel):
    area: str


class HermesStartRequest(ApiModel):
    area: str = Field(min_length=1, max_length=200)
    session_id: int = Field(gt=0)
    operation_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class HermesDecisionRequest(ApiModel):
    analysis: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=500)
    step: int = Field(ge=0, le=1000)


class HermesCompleteRequest(ApiModel):
    operation_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    message: str = Field(default="기록1 점검을 완료했습니다.", min_length=1, max_length=500)


class BlockedMissionRequest(ApiModel):
    reason: str
    retry_count: int


class NavigationStatusResponse(ApiModel):
    active_version: NavigationVersion = NavigationVersion.V2
    label: str = "V2 Depth + Pi IMU"


class DefectProgressRequest(ApiModel):
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    mission_id: str = Field(min_length=1, max_length=64, pattern=r"^mission-[A-Za-z0-9]+$")
    defect_type: Literal[
        "CRACK",
        "DEBRIS",
        "ROOT",
        "BLOCKAGE",
        "SURFACE_DAMAGE",
        "OTHER",
    ]
    confidence: float = Field(ge=0, le=1)
    location_m: float = Field(ge=0, le=10000)


class UnavailableProgressProvider:
    loaded = False

    def generate_progress_announcement(self, **_kwargs):
        raise RuntimeError("Qwen progress provider is not configured")


def navigation_status_response(status: NavigationStatus) -> NavigationStatusResponse:
    return NavigationStatusResponse(active_version=status.active_version, label=status.label)


def fetch_snapshot(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise ValueError("snapshot URL must use http or https")
    with urlopen(url, timeout=3) as response:  # noqa: S310 - operator camera URL
        image = response.read(5_000_001)
    if not image or len(image) > 5_000_000:
        raise ValueError("snapshot is empty or exceeds 5 MB")
    return image


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        queue = self.subscribe_now()
        try:
            yield queue
        finally:
            self.unsubscribe(queue)

    def subscribe_now(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, payload: dict[str, object]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def _dump(model: ApiModel) -> dict[str, object]:
    return model.model_dump(mode="json", by_alias=True)


async def stream_events(
    websocket: WebSocket,
    agent: IntegratedAgent,
    hub: EventHub,
    progress_service: ProgressAnnouncementService | None = None,
) -> None:
    await websocket.accept()
    mission_snapshot = agent.snapshot.model_copy(deep=True)
    progress_snapshot = (
        progress_service.current.model_copy(deep=True)
        if progress_service is not None
        else None
    )
    queue = hub.subscribe_now()
    try:
        await websocket.send_json(
            {"type": "mission.snapshot", "mission": _dump(mission_snapshot)}
        )
        if progress_snapshot is not None:
            await websocket.send_json(
                {"type": "progress.summary", "summary": _dump(progress_snapshot)}
            )
        while True:
            event_task = asyncio.create_task(queue.get())
            receive_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {event_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if receive_task in done:
                message = receive_task.result()
                if message["type"] == "websocket.disconnect":
                    return
            if event_task in done:
                await websocket.send_json(event_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        hub.unsubscribe(queue)


def create_app(
    agent: IntegratedAgent | None = None,
    *,
    perception_provider: PerceptionProvider | None = None,
    snapshot_fetcher: Callable[[str], bytes] | None = None,
    command_bridge: CommandBridge | None = None,
    auto_drive_loop: bool = False,
    preload_perception: bool = False,
    preload_progress: bool = False,
    navigation_router: NavigationRouter | None = None,
    progress_provider: ProgressAnnouncementProvider | None = None,
    drive_loop_interval_s: float = 0.10,
    drive_snapshot_url: str = "http://192.168.100.246:8081/snapshot.jpg",
    hermes_relay: HermesInspectionRelay | None = None,
    vl_snapshot_dir: Path | None = None,
    yolo_detector: YoloDetector | None = None,
    yolo_capture_store: YoloCaptureStore | None = None,
    cloud_publisher: CloudTelemetryPublisher | None = None,
) -> FastAPI:
    mission_agent = agent or IntegratedAgent()
    active_perception = perception_provider or create_perception_provider()
    active_snapshot_fetcher = snapshot_fetcher or fetch_snapshot
    drive_controller = DriveController()
    local_planner = LocalFreeSpacePlanner()
    active_navigation = navigation_router or NavigationRouter(base=local_planner)
    drive_status = DriveStatus(
        loop_mode="continuous" if auto_drive_loop and command_bridge is not None else "manual-step",
        control_mode="real-motor" if command_bridge is not None else "simulation-no-motor",
        decision_model=active_navigation.decision_model,
    )
    mission_agent.snapshot.control_mode = drive_status.control_mode
    hub = EventHub()
    active_progress_provider = progress_provider or UnavailableProgressProvider()
    active_hermes_relay = hermes_relay or HermesInspectionRelay()
    active_vl_snapshot_dir = vl_snapshot_dir or Path(
        os.environ.get(
            "PIPECHECKKK_VL_SNAPSHOT_DIR",
            str(Path.home() / ".local/share/pipecheckkk/vl-inspections"),
        )
    )
    active_yolo_detector = yolo_detector or YoloDetector(
        model_path=os.environ.get(
            "PIPECHECKKK_YOLO_MODEL_PATH",
            "/home/ssafy/Downloads/yolo26n-seg/best.pt",
        ),
        class_names={0: "damage", 1: "defect"},
    )
    active_yolo_capture_store = yolo_capture_store or YoloCaptureStore(
        Path(
            os.environ.get(
                "PIPECHECKKK_YOLO_CAPTURE_DIR",
                str(Path.home() / ".local/share/pipecheckkk/yolo-captures"),
            )
        )
    )
    cloud_api_url = os.environ.get("PIPECHECKKK_CLOUD_API_URL", "").strip()
    cloud_publish_key = os.environ.get("PIPECHECKKK_CLOUD_PUBLISH_KEY", "")
    active_cloud_publisher = cloud_publisher or (
        CloudTelemetryPublisher(
            base_url=cloud_api_url,
            publish_key=cloud_publish_key,
        ) if cloud_api_url and cloud_publish_key else None
    )
    active_frame_stream_publisher = (
        CloudFrameStreamPublisher(
            base_url=cloud_api_url,
        ) if cloud_api_url else None
    )

    async def publish_progress(announcement: ProgressAnnouncement) -> None:
        hub.publish({"type": "progress.summary", "summary": _dump(announcement)})

    progress_service = ProgressAnnouncementService(
        provider=active_progress_provider,
        publisher=publish_progress,
    )
    progress_detector = ProgressEventDetector()
    periodic_progress_trigger = PeriodicProgressTrigger(
        interval_s=DEFAULT_PERIODIC_PROGRESS_INTERVAL_S
    )
    app = FastAPI(title="PipeCheckkk Integrated Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://i15c102.p.ssafy.io",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.agent = mission_agent
    app.state.event_hub = hub
    app.state.drive_controller = drive_controller
    app.state.local_planner = local_planner
    app.state.navigation_router = active_navigation
    app.state.perception_provider = active_perception
    app.state.command_bridge = command_bridge
    app.state.hermes_relay = active_hermes_relay
    app.state.yolo_detector = active_yolo_detector
    app.state.yolo_capture_store = active_yolo_capture_store
    app.state.autonomy_task = None
    app.state.heartbeat_task = None
    app.state.perception_loaded = False
    app.state.progress_service = progress_service
    app.state.progress_detector = progress_detector
    app.state.progress_tasks = {}
    app.state.progress_defect_ids = set()
    app.state.progress_defect_last_accepted = 0.0
    app.state.cloud_publish_task = None
    app.state.frame_stream_publish_task = None
    app.state.defect_publish_task = None
    app.state.blockage_publish_tasks = set()
    app.state.blockage_publish_keys = set()
    app.state.latest_source_frame = None
    app.state.latest_source_frame_identity = None
    app.state.inspection_step = 0
    app.state.inspection_context = {
        "area": None,
        "session_id": None,
        "operation_id": None,
    }
    app.state.latest_progress_facts = {
        "situation": "관로 점검 상태 확인 중",
        "decidedAction": "상태 모니터링",
    }

    def cancel_pending_defect_publish() -> None:
        defect_task = app.state.defect_publish_task
        app.state.defect_publish_task = None
        if defect_task is not None and not defect_task.done():
            defect_task.cancel()

    def schedule_blockage_publish(
        *,
        session_id: int,
        operation_id: str,
    ) -> None:
        """Send BLOCKAGE evidence without coupling failure to robot control."""
        if active_cloud_publisher is None:
            return
        publisher = active_cloud_publisher
        publish_key = (session_id, operation_id, 20)
        if publish_key in app.state.blockage_publish_keys:
            return
        app.state.blockage_publish_keys.add(publish_key)
        cached_frame = (
            app.state.latest_source_frame
            if app.state.latest_source_frame_identity == (session_id, operation_id)
            else None
        )

        async def publish() -> None:
            try:
                frame = cached_frame
                if not isinstance(frame, bytes) or not frame:
                    frame = await asyncio.to_thread(
                        active_snapshot_fetcher,
                        drive_snapshot_url,
                    )
                await publisher.upload_blockage(
                    frame,
                    session_id=session_id,
                    operation_id=operation_id,
                    event_id=f"operator-blockage-step20-{operation_id}",
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.warning(
                    "BLOCKAGE evidence publish unavailable; motion continues: %s",
                    type(error).__name__,
                )

        task = asyncio.create_task(publish())
        app.state.blockage_publish_tasks.add(task)
        task.add_done_callback(app.state.blockage_publish_tasks.discard)

    def clear_inspection_context() -> None:
        cancel_pending_defect_publish()
        app.state.inspection_step = 0
        app.state.latest_source_frame = None
        app.state.latest_source_frame_identity = None
        app.state.inspection_context = {
            "area": None,
            "session_id": None,
            "operation_id": None,
        }
        app.state.latest_progress_facts = {
            "situation": "관로 점검 상태 확인 중",
            "decidedAction": "상태 모니터링",
        }
    app.state.progress_trusted_hosts = {
        host.strip()
        for host in os.environ.get(
            "PIPECHECKKK_PROGRESS_TRUSTED_HOSTS",
            "127.0.0.1,::1,testclient",
        ).split(",")
        if host.strip()
    }
    yolo_inference_lock = asyncio.Lock()

    async def produce_yolo_frame() -> tuple[bytes, bytes, list[dict[str, object]]]:
        async with yolo_inference_lock:
            status = active_yolo_detector.get_status()
            if not status.available:
                raise RuntimeError(status.message)
            if not status.loaded:
                await asyncio.to_thread(active_yolo_detector.load)
            frame_bytes = await asyncio.to_thread(
                active_snapshot_fetcher,
                drive_snapshot_url,
            )
            detections = await asyncio.to_thread(
                active_yolo_detector.infer_to_temp,
                frame_bytes,
                f"cloud-frame-{time_ns()}",
            )
            rendered = await asyncio.to_thread(
                render_yolo_overlay,
                frame_bytes,
                detections,
            )
            return (
                frame_bytes,
                rendered,
                [detection.model_dump(mode="json") for detection in detections],
            )

    async def live_drive_snapshot() -> DriveStatus:
        snapshot = drive_status.model_copy(deep=True)
        if command_bridge is None:
            return snapshot
        try:
            health_payload = await asyncio.to_thread(command_bridge.health)
        except Exception:
            return snapshot

        raw_motor = health_payload.get("motorOutput")
        if isinstance(raw_motor, dict):
            left = raw_motor.get("left")
            right = raw_motor.get("right")
            if (
                isinstance(left, (int, float))
                and not isinstance(left, bool)
                and isinstance(right, (int, float))
                and not isinstance(right, bool)
                and math.isfinite(float(left))
                and math.isfinite(float(right))
                and abs(float(left)) <= 1.0
                and abs(float(right)) <= 1.0
            ):
                snapshot.motor_output = MotorOutput(left=float(left), right=float(right))

        raw_imu = health_payload.get("imu")
        if isinstance(raw_imu, dict):
            pitch = raw_imu.get("pitchDeg")
            roll = raw_imu.get("rollDeg")
            yaw = raw_imu.get("yawDeg")
            if (
                isinstance(pitch, (int, float))
                and not isinstance(pitch, bool)
                and isinstance(roll, (int, float))
                and not isinstance(roll, bool)
                and isinstance(yaw, (int, float))
                and not isinstance(yaw, bool)
                and math.isfinite(float(pitch))
                and math.isfinite(float(roll))
                and math.isfinite(float(yaw))
            ):
                snapshot.imu = ImuTelemetry(
                    pitch_deg=float(pitch),
                    roll_deg=float(roll),
                    yaw_deg=float(yaw),
                )
        return snapshot

    async def run_cloud_publish_loop() -> None:
        assert active_cloud_publisher is not None or active_frame_stream_publisher is not None
        # 기본 0.1초(10fps). 예전 기본값 0.2초는 「작업 + 0.2초」로 돌던 시절의
        # 값이라 어차피 지켜지지 않았다(실측 주기 345ms). 이제 아래 마감시각 방식이라
        # 작업이 오래 걸리면 그만큼만 느려지고, 빠르면 이 값까지 올라간다 — 즉 줄여도
        # 손해가 없다. 하한 0.05 는 0 을 막는 안전장치다(0 이면 루프가 CPU 를 태운다).
        interval_s = max(
            0.05,
            float(os.environ.get("PIPECHECKKK_CLOUD_PUBLISH_INTERVAL_S", "0.1")),
        )
        agent_id = os.environ.get("PIPECHECKKK_AGENT_ID", "laptop-4070")

        # 프레임 루프가 실제로 도는 주기를 정하는 세 가지.
        #
        # 화면은 WebSocket(`active_frame_stream_publisher.submit`)이 담당한다. 그런데
        # 그 아래 HTTP 업로드 두 개가 `await` 로 남아 있어, 프레임마다 TCP+TLS 를 새로
        # 맺고 그것이 끝날 때까지 다음 프레임 캡처가 멈춰 있었다. `urlopen` 은 연결을
        # 재사용하지 않는다. 실측 주기 345ms(2.9fps) 의 대부분이 여기였다.
        #
        # 두 업로드를 없애지는 않는다. `live-frame` 은 WebSocket 이 끊겼을 때 프론트가
        # 되돌아가는 폴백이고, `yolo-frame` 은 검사별 근거 보관이다. 다만 매 프레임
        # 올릴 이유가 없어 주기를 낮추고, 루프를 붙잡지 않도록 뒤로 뺀다.
        FALLBACK_UPLOAD_PERIOD_S = 1.0     # 폴백 <img> 는 이 정도면 충분히 최신이다
        SCOPED_UPLOAD_PERIOD_S = 2.0       # 근거 보관은 더 드물어도 된다
        TELEMETRY_PERIOD_S = 1.0           # 프론트가 2초마다 읽는다
        last_fallback_upload = 0.0
        last_scoped_upload = 0.0
        last_telemetry_publish = 0.0
        side_uploads: dict[str, asyncio.Task | None] = {
            "fallback": None,
            "scoped": None,
        }

        def spawn_side_upload(slot: str, coroutine) -> bool:
            """업로드를 뒤에서 돌린다. 앞의 것이 안 끝났으면 이번 것은 버린다.

            기다리지 않는 것이 요점이다. 버려도 되는 이유는 둘 다 "최신 한 장"만
            의미가 있어서다 — 밀린 프레임을 굳이 따라잡을 필요가 없다.
            """
            side_upload = side_uploads[slot]
            if side_upload is not None and not side_upload.done():
                coroutine.close()      # 안 기다린 코루틴 경고를 막는다
                return False
            task = asyncio.create_task(coroutine)

            def report_result(done: asyncio.Task) -> None:
                if done.cancelled():
                    return
                error = done.exception()
                if error is not None:
                    LOGGER.warning("EC2 보조 업로드 실패: %s", type(error).__name__)

            task.add_done_callback(report_result)
            side_uploads[slot] = task
            return True

        # 다음 주기의 마감시각. `sleep(interval)` 을 작업 **뒤에** 두면 실제 주기가
        # 「작업시간 + interval」이 된다. 마감시각 기준으로 재우면 작업이 빨라진 만큼
        # 주기가 줄어 설정값(기본 0.1초 = 10fps)에 실제로 가까워진다.
        next_deadline = monotonic()

        last_qwen_revision: object = None
        event_cursor = 0
        active_event_identity: tuple[int, str] | None = None
        frame_error_active = False
        telemetry_error_active = False
        published_detection_signatures: dict[tuple[int, str, str], float] = {}
        active_detection_identity: tuple[int, str] | None = None
        candidate_detection_signature: str | None = None
        candidate_detection_count = 0
        detection_publish_signature: tuple[int, str, str] | None = None
        while True:
            detection_task = app.state.defect_publish_task
            if detection_task is not None and detection_task.done():
                app.state.defect_publish_task = None
                try:
                    detection_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as error:
                    LOGGER.warning("EC2 defect publish unavailable: %s", type(error).__name__)
                else:
                    if detection_publish_signature is not None:
                        published_detection_signatures[detection_publish_signature] = monotonic()
                detection_publish_signature = None
            context = dict(app.state.inspection_context)
            session_id = context.get("session_id")
            operation_id = context.get("operation_id")
            inspection_identity = (
                (session_id, operation_id)
                if isinstance(session_id, int) and session_id > 0
                and isinstance(operation_id, str) and operation_id
                else None
            )
            mission_snapshot = mission_agent.snapshot
            hermes_status = active_hermes_relay.status()
            periodic_progress_active = (
                inspection_identity is not None
                and (
                    mission_snapshot.state is MissionState.RUNNING
                    or hermes_status.state == "RUNNING"
                )
            )
            if periodic_progress_trigger.due(
                now=monotonic(),
                active=periodic_progress_active,
            ):
                assert inspection_identity is not None
                latest_progress_facts = dict(app.state.latest_progress_facts)
                schedule_progress_event(
                    ProgressEvent(
                        event_type=ProgressEventType.ROBOT_ACTION,
                        facts={
                            "situation": (
                                latest_progress_facts.get("situation")
                                or mission_snapshot.agent_reason
                                or hermes_status.message
                                or "관로 점검 상태 확인 중"
                            ),
                            "decidedAction": (
                                latest_progress_facts.get("decidedAction")
                                or mission_snapshot.agent_action
                                or "상태 모니터링"
                            ),
                        },
                    ),
                    mission_snapshot,
                    mission_id=str(inspection_identity[1]),
                    mission_state=MissionState.RUNNING,
                    publish_pending=False,
                    preserve_on_error=True,
                    only_if_idle=True,
                )
            source_frame: bytes | None = None
            rendered: bytes | None = None
            detections: list[dict[str, object]] = []
            frame_had_error = False
            try:
                if _automatic_yolo_db_publish_allowed(app.state.inspection_step):
                    source_frame, rendered, detections = await produce_yolo_frame()
                else:
                    # Step 20 starts the 180-degree turn and return journey. Keep the
                    # operator's live camera available, but do not run YOLO, create
                    # detections, or publish defect records during the return phase.
                    source_frame = await asyncio.to_thread(
                        active_snapshot_fetcher,
                        drive_snapshot_url,
                    )
                    rendered = source_frame
                    detections = []
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not frame_error_active:
                    LOGGER.warning("YOLO inference unavailable: %s", type(error).__name__)
                frame_had_error = True

            if source_frame is not None and inspection_identity is not None:
                latest_context = dict(app.state.inspection_context)
                latest_identity = (
                    latest_context.get("session_id"),
                    latest_context.get("operation_id"),
                )
                if latest_identity == inspection_identity:
                    app.state.latest_source_frame = source_frame
                    app.state.latest_source_frame_identity = inspection_identity

            if rendered is not None:
                if active_frame_stream_publisher is not None:
                    active_frame_stream_publisher.submit(rendered)
                captured_at = monotonic()
                if active_cloud_publisher is not None:
                    # WebSocket이 화면을 담당할 때 legacy HTTP fallback 업로드는 낮은
                    # 주기로 background 처리해 다음 frame 생성을 막지 않는다.
                    if active_frame_stream_publisher is None:
                        await active_cloud_publisher.upload_live_frame(rendered)
                    elif captured_at - last_fallback_upload >= FALLBACK_UPLOAD_PERIOD_S:
                        if spawn_side_upload(
                            "fallback",
                            active_cloud_publisher.upload_live_frame(rendered),
                        ):
                            last_fallback_upload = captured_at

                    context = dict(app.state.inspection_context)
                    current_session_id = context.get("session_id")
                    current_operation_id = context.get("operation_id")
                    current_identity = (
                        (current_session_id, current_operation_id)
                        if isinstance(current_session_id, int) and current_session_id > 0
                        and isinstance(current_operation_id, str) and current_operation_id
                        else None
                    )
                    if inspection_identity is not None and current_identity == inspection_identity:
                        if captured_at - last_scoped_upload >= SCOPED_UPLOAD_PERIOD_S:
                            if spawn_side_upload(
                                "scoped",
                                active_cloud_publisher.upload_frame(
                                    rendered,
                                    session_id=inspection_identity[0],
                                    operation_id=inspection_identity[1],
                                ),
                            ):
                                last_scoped_upload = captured_at

                        if inspection_identity != active_detection_identity:
                            published_detection_signatures.clear()
                            active_detection_identity = inspection_identity
                            candidate_detection_signature = None
                            candidate_detection_count = 0
                        association_id = _yolo_detection_event_id(detections)
                        if association_id == candidate_detection_signature:
                            candidate_detection_count += 1
                        else:
                            candidate_detection_signature = association_id
                            candidate_detection_count = 1 if association_id is not None else 0
                        signature_key = (
                            inspection_identity[0], inspection_identity[1], association_id
                        ) if association_id is not None else None
                        last_published_at = (
                            published_detection_signatures.get(signature_key)
                            if signature_key is not None else None
                        )
                        if (
                            source_frame is not None
                            and _automatic_yolo_db_publish_allowed(app.state.inspection_step)
                            and signature_key is not None
                            and candidate_detection_count >= 2
                            and (last_published_at is None or monotonic() - last_published_at >= 30.0)
                            and app.state.defect_publish_task is None
                        ):
                            mission_payload = _dump(mission_agent.snapshot)
                            approx_position = mission_payload.get("distanceM", mission_payload.get("distance_m"))
                            backend_event_id = f"{association_id}-{time_ns()}"
                            app.state.defect_publish_task = asyncio.create_task(
                                active_cloud_publisher.upload_detections(
                                    rendered,
                                    session_id=inspection_identity[0],
                                    operation_id=inspection_identity[1],
                                    event_id=backend_event_id,
                                    detections=detections,
                                    approx_position_m=(
                                        float(approx_position)
                                        if isinstance(approx_position, (int, float))
                                        and not isinstance(approx_position, bool)
                                        else None
                                    ),
                                )
                            )
                            detection_publish_signature = signature_key
            frame_error_active = frame_had_error

            # 텔레메트리·주행로그는 대시보드 상태이지 영상이 아니다. 프레임마다 보내면
            # 그 HTTP PUT(연결 재사용 없음) 이 루프를 붙잡아 프레임 주기를 정해 버린다.
            # 프론트는 이 값을 2초마다 읽으므로 1초에 한 번이면 넉넉하다.
            if (
                active_cloud_publisher is None
                or monotonic() - last_telemetry_publish < TELEMETRY_PERIOD_S
            ):
                next_deadline += interval_s
                now = monotonic()
                if next_deadline < now:
                    next_deadline = now
                await asyncio.sleep(next_deadline - now)
                continue
            last_telemetry_publish = monotonic()

            try:
                hermes_payload = asdict(active_hermes_relay.status())
                qwen_payload = _dump(progress_service.current)
                live_drive = await live_drive_snapshot()
                await active_cloud_publisher.upload_telemetry(
                    {
                        "agent_id": agent_id,
                        "session_id": context.get("session_id"),
                        "operation_id": context.get("operation_id"),
                        "hermes": hermes_payload,
                        "mission": _dump(mission_agent.snapshot),
                        "qwen_summary": qwen_payload,
                        "drive": _dump(live_drive),
                    }
                )

                session_id = context.get("session_id")
                if isinstance(session_id, int):
                    qwen_revision = qwen_payload.get("revision")
                    if qwen_revision != last_qwen_revision:
                        await active_cloud_publisher.upload_log(
                            session_id,
                            {
                                "command_source": "Qwen progress",
                                "agent_state": str(qwen_payload.get("status") or "idle"),
                                "note": str(qwen_payload.get("message") or "진행 요약 대기"),
                            },
                        )
                        last_qwen_revision = qwen_revision

                    events = mission_agent.events
                    current_event_cursor = mission_agent.event_cursor
                    operation_id = context.get("operation_id")
                    event_identity = (
                        (session_id, operation_id)
                        if isinstance(operation_id, str) and operation_id
                        else None
                    )
                    event_cursor = _normalize_cloud_event_cursor(
                        uploaded_cursor=event_cursor,
                        current_cursor=current_event_cursor,
                        buffered_event_count=len(events),
                        identity_changed=event_identity != active_event_identity,
                    )
                    active_event_identity = event_identity
                    buffer_start = max(0, current_event_cursor - len(events))
                    pending_events = events[event_cursor - buffer_start :]
                    for event in pending_events:
                        payload = _cloud_robot_log_payload(
                            category=event.category,
                            level=event.level,
                            message=event.message,
                            timestamp=event.timestamp.isoformat(),
                        )
                        if payload is not None:
                            await active_cloud_publisher.upload_log(session_id, payload)
                        event_cursor += 1
                telemetry_error_active = False
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not telemetry_error_active:
                    LOGGER.warning("EC2 telemetry publish unavailable: %s", type(error).__name__)
                    telemetry_error_active = True
            # 작업이 마감시각을 이미 넘겼으면 쉬지 않고 바로 다음 프레임으로 간다.
            next_deadline += interval_s
            now = monotonic()
            if next_deadline < now:
                next_deadline = now      # 크게 밀렸으면 따라잡기를 포기하고 현재로 맞춘다
            await asyncio.sleep(next_deadline - now)

    @app.on_event("startup")
    async def preload_active_models() -> None:
        if preload_perception:
            load_perception = getattr(active_perception, "load", None)
            if callable(load_perception):
                await asyncio.to_thread(load_perception)
            app.state.perception_loaded = True
        if preload_progress:
            load_progress = getattr(active_progress_provider, "load", None)
            if callable(load_progress):
                await asyncio.to_thread(load_progress)
        if active_cloud_publisher is not None or active_frame_stream_publisher is not None:
            app.state.cloud_publish_task = asyncio.create_task(run_cloud_publish_loop())
        if active_frame_stream_publisher is not None:
            app.state.frame_stream_publish_task = asyncio.create_task(
                active_frame_stream_publisher.run()
            )

    def publish_new_events(start_index: int, snapshot: MissionSnapshot) -> None:
        added_count = mission_agent.event_cursor - start_index
        new_events = mission_agent.events[-added_count:] if added_count > 0 else []
        for event in new_events:
            hub.publish({"type": "agent.event", "event": _dump(event)})
        hub.publish({"type": "mission.snapshot", "mission": _dump(snapshot)})

    def schedule_progress_event(
        event,
        snapshot: MissionSnapshot,
        *,
        mission_id: str | None = None,
        mission_state: MissionState | None = None,
        publish_pending: bool = False,
        preserve_on_error: bool = False,
        only_if_idle: bool = False,
    ) -> bool:
        generation_token = progress_service.generation_token
        current_epoch_tasks = sum(
            1
            for task, token in app.state.progress_tasks.items()
            if token == generation_token and not task.done()
        )
        if (only_if_idle and current_epoch_tasks > 0) or current_epoch_tasks >= 4:
            return False
        task = asyncio.create_task(
            progress_service.generate(
                event,
                mission_id=snapshot.mission_id if mission_id is None else mission_id,
                mission_state=snapshot.state if mission_state is None else mission_state,
                generation_token=generation_token,
                publish_pending=publish_pending,
                preserve_on_error=preserve_on_error,
            )
        )
        app.state.progress_tasks[task] = generation_token
        task.add_done_callback(app.state.progress_tasks.pop)
        return True

    def observe_progress_drive(perception, plan) -> None:
        event = progress_detector.observe_drive(
            path_blocked_probability=perception.path_blocked_probability,
            safe_for_control=perception.safe_for_control,
            plan_action=plan.action,
            steering=plan.steering,
        )
        if event is not None:
            schedule_progress_event(event, mission_agent.snapshot)

    async def analyze_frame(image_bytes: bytes):
        """Run the V2-authoritative Depth Anything perception provider."""
        return await asyncio.to_thread(active_perception.analyze, image_bytes)

    async def run_heartbeat_loop() -> None:
        assert command_bridge is not None
        while mission_agent.snapshot.state.value == "RUNNING" and command_bridge.status.armed:
            try:
                await asyncio.to_thread(command_bridge.heartbeat)
            except RuntimeError as error:
                if mission_agent.snapshot.state.value == "RUNNING":
                    event_index = mission_agent.event_cursor
                    failed = mission_agent.fail_system(f"Edge heartbeat 실패: {error}")
                    await progress_service.invalidate(
                        mission_id=failed.mission_id,
                        mission_state=failed.state,
                    )
                    publish_new_events(event_index, failed)
                break
            await asyncio.sleep(0.20)

    async def run_autonomy_loop() -> None:
        assert command_bridge is not None
        perception_failure_count = 0
        while mission_agent.snapshot.state.value == "RUNNING":
            try:
                health_payload = await asyncio.to_thread(command_bridge.health)
                raw_distance = health_payload.get("frontDistanceM")
                distance_m = _parse_front_distance(raw_distance)
                imu_status: dict[str, object] | None = None
                if active_navigation.active_version is NavigationVersion.V2:
                    raw_imu = health_payload.get("imu")
                    if not isinstance(raw_imu, dict):
                        raise RuntimeError("Pi Edge did not return V2 IMU status")
                    imu_status = raw_imu
                    _validate_v2_imu_status(imu_status)
                    imu_phase = str(imu_status.get("phase", "UNAVAILABLE"))
                    hub.publish(
                        {
                            "type": "imu.status",
                            "missionId": mission_agent.snapshot.mission_id,
                            "imu": imu_status,
                        }
                    )
                    if imu_phase == "PIVOT_YAW":
                        mission_agent.snapshot.agent_action = "V2_YAW_STEP_WAIT"
                        mission_agent.snapshot.agent_reason = (
                            f"Pi가 yaw {imu_status.get('yawTargetDeg')}° target까지 "
                            "50 Hz로 제자리회전 중입니다."
                        )
                        hub.publish(
                            {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                        )
                        await asyncio.sleep(drive_loop_interval_s)
                        continue
                    if imu_phase in {"PITCH_LIMIT_REACHED", "ROLL_LIMIT_REACHED"}:
                        trigger = str(
                            imu_status.get("advanceLimitTrigger")
                            or ("ROLL" if imu_phase == "ROLL_LIMIT_REACHED" else "PITCH")
                        )
                        trigger_data = {
                            "trigger": trigger,
                            "pitchDeg": imu_status.get("pitchDeg"),
                            "rollDeg": imu_status.get("rollDeg"),
                            "pitchStopDeg": imu_status.get("pitchStopDeg"),
                            "rollStopDeg": imu_status.get("rollStopDeg"),
                        }
                        mission_agent.record_event(
                            "DECISION",
                            f"V2 경로 복귀 시작: {trigger}",
                            trigger_data,
                        )
                        imu_status = await asyncio.to_thread(command_bridge.start_imu_recovery)
                        mission_agent.snapshot.agent_action = "V2_LOCAL_RECOVERY"
                        limit_reason = "roll ±45°" if trigger == "ROLL" else "pitch ±35°"
                        mission_agent.snapshot.agent_reason = (
                            f"{limit_reason} 도달로 Pi yaw→roll→yaw 복귀 시작"
                        )
                        hub.publish(
                            {
                                "type": "imu.status",
                                "missionId": mission_agent.snapshot.mission_id,
                                "imu": imu_status,
                            }
                        )
                        await asyncio.sleep(drive_loop_interval_s)
                        continue
                    if imu_phase in {"PRIMARY_YAW", "ROLL_RECOVERY", "FINAL_YAW"}:
                        mission_agent.snapshot.agent_action = "V2_LOCAL_RECOVERY"
                        mission_agent.snapshot.agent_reason = f"Pi 50 Hz 자세 복귀 진행 중: {imu_phase}"
                        hub.publish(
                            {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                        )
                        await asyncio.sleep(drive_loop_interval_s)
                        continue
                    if imu_phase == "COMPLETED":
                        imu_status = await asyncio.to_thread(
                            command_bridge.recalibrate_imu_after_recovery,
                            100,
                        )
                        active_navigation.resume_v2_after_recovery()
                        mission_agent.snapshot.agent_action = "V2_NORMAL_CRUISE_RESUMED"
                        mission_agent.snapshot.agent_reason = (
                            "yaw→roll→yaw 원래 경로 복귀와 IMU 재기준 설정을 완료해 "
                            "정상 탐사주행을 재개합니다."
                        )
                        hub.publish(
                            {
                                "type": "imu.status",
                                "missionId": mission_agent.snapshot.mission_id,
                                "imu": imu_status,
                            }
                        )
                        hub.publish(
                            {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                        )
                        await asyncio.sleep(drive_loop_interval_s)
                        continue
                    if imu_phase in {"ERROR", "UNAVAILABLE", "UNCALIBRATED"}:
                        raise RuntimeError(f"V2 Pi IMU is not safe for control: {imu_phase}")
                    obstacle_preempted = active_navigation.should_preempt_v2_obstacle(distance_m)

                    if obstacle_preempted:
                        await asyncio.to_thread(
                            command_bridge.hold_stop,
                            "V2_ULTRASONIC_5CM_PREEMPT",
                        )
                        mission_agent.snapshot.agent_action = "V2_OBSTACLE_PRESTOP"
                        mission_agent.snapshot.agent_reason = (
                            f"전방 초음파 {distance_m:.3f}m가 0.05m 이하라 "
                            "Depth 분석 전에 먼저 정지합니다."
                        )
                        hub.publish(
                            {
                                "type": "mission.snapshot",
                                "mission": _dump(mission_agent.snapshot),
                            }
                        )
                    raw_yaw = imu_status.get("yawDeg")
                    yaw_deg = (
                        float(raw_yaw)
                        if isinstance(raw_yaw, (int, float)) and not isinstance(raw_yaw, bool)
                        else None
                    )
                    if (
                        not obstacle_preempted
                        and active_navigation.should_trigger_v2_yaw_drift_recovery(
                            yaw_deg,
                            imu_phase=imu_phase,
                        )
                    ):
                        await asyncio.to_thread(
                            command_bridge.hold_stop,
                            "V2_NORMAL_YAW_DRIFT_15DEG",
                        )
                        active_navigation.begin_v2_yaw_drift_recovery()
                        mission_agent.record_event(
                            "DECISION",
                            "V2 정상주행 yaw 편차 경로 복귀 시작",
                            {
                                "yawDeg": yaw_deg,
                                "yawDriftTriggerDeg": 15.0,
                                "reference": "MISSION_START_CALIBRATION",
                            },
                        )
                        imu_status = await asyncio.to_thread(command_bridge.start_imu_recovery)
                        mission_agent.snapshot.agent_action = "V2_YAW_DRIFT_RECOVERY"
                        mission_agent.snapshot.agent_reason = (
                            f"Mission 시작 기준 yaw에서 {yaw_deg:.2f}° 편차가 발생해 "
                            "yaw→roll→yaw 경로 복귀를 시작합니다."
                        )
                        hub.publish(
                            {
                                "type": "imu.status",
                                "missionId": mission_agent.snapshot.mission_id,
                                "imu": imu_status,
                            }
                        )
                        hub.publish(
                            {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                        )
                        await asyncio.sleep(drive_loop_interval_s)

                        continue
                try:
                    image_bytes = await asyncio.to_thread(
                        active_snapshot_fetcher,
                        drive_snapshot_url,

                    )
                    perception = await analyze_frame(image_bytes)
                except Exception as error:
                    perception_failure_count += 1
                    await asyncio.to_thread(
                        command_bridge.hold_stop,
                        "TRANSIENT_PERCEPTION_FAILURE",
                    )
                    hub.publish(
                        {
                            "type": "perception.retry",
                            "attempt": perception_failure_count,
                            "maxAttempts": 3,
                            "error": str(error),
                        }
                    )
                    if perception_failure_count < 3:
                        await asyncio.sleep(drive_loop_interval_s)
                        continue
                    raise RuntimeError(
                        "카메라/Depth 인식이 3회 연속 실패했습니다: "
                        f"{error}"
                    ) from error
                perception_failure_count = 0
                observation = DriveObservation(
                    center_error=perception.center_error,
                    perception_confidence=perception.confidence,
                    frame_age_s=0.0,
                    ultrasonic_distance_m=distance_m,
                    perception_model=perception.model_name,
                )
                if active_navigation.active_version is NavigationVersion.V2:
                    plan = active_navigation.plan(
                        perception,
                        ultrasonic_distance_m=distance_m,
                        imu_status=imu_status,
                    )
                else:
                    plan = active_navigation.plan(
                        perception,
                        ultrasonic_distance_m=distance_m,
                    )
                mission_agent.snapshot.agent_action = plan.action
                mission_agent.snapshot.agent_reason = plan.reason
                if plan.action.startswith("V2_YAW_STEP_") or plan.action in {
                    "V2_YAW_ASSESS",
                    "V2_DIRECTION_ASSESS",
                }:
                    if plan.action == "V2_DIRECTION_ASSESS":
                        await asyncio.to_thread(
                            command_bridge.hold_stop,
                            "V2_DIRECTION_ASSESS",
                        )
                    if plan.action in {"V2_YAW_STEP_LEFT", "V2_YAW_STEP_RIGHT"}:
                        direction = "LEFT" if plan.action.endswith("LEFT") else "RIGHT"
                        imu_status = await asyncio.to_thread(
                            command_bridge.start_imu_yaw_step,
                            direction,
                        )
                    drive_status.perception_model = perception.model_name
                    drive_status.decision_model = active_navigation.decision_model
                    drive_status.latest_observation = observation.model_copy(deep=True)
                    drive_status.latest_perception = perception.model_copy(deep=True)
                    drive_status.latest_plan = plan.model_copy(deep=True)
                    drive_status.latest_command = None
                    observe_progress_drive(perception, plan)
                    mission_id = mission_agent.snapshot.mission_id
                    hub.publish(
                        {
                            "type": "perception.result",
                            "missionId": mission_id,
                            "perception": _dump(perception),
                            "observation": _dump(observation),
                        }
                    )
                    hub.publish(
                        {"type": "planner.decision", "missionId": mission_id, "plan": _dump(plan)}
                    )
                    if imu_status is not None:
                        hub.publish(
                            {"type": "imu.status", "missionId": mission_id, "imu": imu_status}
                        )
                    hub.publish(
                        {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                    )
                    await asyncio.sleep(drive_loop_interval_s)
                    continue
                command = drive_controller.compute_local_plan(
                    observation,
                    plan,
                    decision_model=active_navigation.decision_model,
                )
                if (
                    plan.action == "V2_PITCH_ADVANCE"
                    and imu_status is not None
                    and imu_status.get("phase") in {"READY", "YAW_STEP_REACHED"}
                ):
                    imu_status = await asyncio.to_thread(command_bridge.start_imu_pitch_guard)
                    hub.publish(
                        {
                            "type": "imu.status",
                            "missionId": mission_agent.snapshot.mission_id,
                            "imu": imu_status,
                        }
                    )
                command.control_mode = "real-motor"
                ack = await asyncio.to_thread(command_bridge.dispatch, command)
                drive_status.perception_model = perception.model_name
                drive_status.decision_model = active_navigation.decision_model
                drive_status.latest_observation = observation.model_copy(deep=True)
                drive_status.latest_perception = perception.model_copy(deep=True)
                drive_status.latest_plan = plan.model_copy(deep=True)
                drive_status.latest_command = command.model_copy(deep=True)
                observe_progress_drive(perception, plan)
                mission_id = mission_agent.snapshot.mission_id
                hub.publish(
                    {
                        "type": "perception.result",
                        "missionId": mission_id,
                        "perception": _dump(perception),
                        "observation": _dump(observation),
                    }
                )
                hub.publish(
                    {"type": "planner.decision", "missionId": mission_id, "plan": _dump(plan)}
                )
                hub.publish(
                    {"type": "drive.command", "missionId": mission_id, "command": _dump(command)}
                )
                hub.publish({"type": "edge.ack", "ack": _dump(ack)})
                hub.publish(
                    {"type": "mission.snapshot", "mission": _dump(mission_agent.snapshot)}
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.exception("Autonomy loop failed; stopping and disarming the motor edge")
                try:
                    await asyncio.to_thread(command_bridge.stop_and_disarm, "AUTONOMY_LOOP_FAILED")
                except Exception:
                    pass
                if mission_agent.snapshot.state.value == "RUNNING":
                    event_index = mission_agent.event_cursor
                    failed = mission_agent.fail_system(f"자동주행 루프 실패: {error}")
                    await progress_service.invalidate(
                        mission_id=failed.mission_id,
                        mission_state=failed.state,
                    )
                    publish_new_events(event_index, failed)
                break
            await asyncio.sleep(drive_loop_interval_s)

    async def stop_autonomy_loop() -> None:
        tasks = [app.state.autonomy_task, app.state.heartbeat_task]
        app.state.autonomy_task = None
        app.state.heartbeat_task = None
        for task in tasks:
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @app.on_event("shutdown")
    async def shutdown_autonomy() -> None:
        frame_stream_task = app.state.frame_stream_publish_task
        app.state.frame_stream_publish_task = None
        if frame_stream_task is not None:
            frame_stream_task.cancel()
            try:
                await frame_stream_task
            except asyncio.CancelledError:
                pass
        cloud_task = app.state.cloud_publish_task
        app.state.cloud_publish_task = None
        if cloud_task is not None:
            cloud_task.cancel()
            try:
                await cloud_task
            except asyncio.CancelledError:
                pass
        defect_task = app.state.defect_publish_task
        app.state.defect_publish_task = None
        if defect_task is not None:
            defect_task.cancel()
            try:
                await defect_task
            except asyncio.CancelledError:
                pass
        await active_hermes_relay.stop()
        await stop_autonomy_loop()
        if command_bridge is not None and command_bridge.status.armed:
            try:
                await asyncio.to_thread(command_bridge.stop_and_disarm, "SERVER_SHUTDOWN")
            except Exception:
                pass


    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "pipecheckkk-ai",
            "controlMode": "real-motor" if command_bridge else "simulation-no-motor",
        }


    @app.get("/api/hermes/status")
    async def hermes_status() -> dict[str, object]:
        return asdict(active_hermes_relay.status())

    @app.post("/api/hermes/start", status_code=202)
    async def start_hermes_vl(
        request: HermesStartRequest,
    ) -> dict[str, object]:
        if active_hermes_relay.status().state == "RUNNING":
            raise HTTPException(status_code=409, detail="Hermes inspection already running")
        app.state.latest_source_frame = None
        app.state.latest_source_frame_identity = None
        if command_bridge is not None:
            try:
                if command_bridge.status.armed:
                    await asyncio.to_thread(
                        command_bridge.hold_stop,
                        "HERMES_VL_AWAITING_APPROVAL",
                    )
                await asyncio.to_thread(
                    command_bridge.stop_and_disarm,
                    "HERMES_VL_AWAITING_APPROVAL",
                )
            except Exception as error:
                raise HTTPException(
                    status_code=503,
                    detail="motor edge could not be made safe before VL analysis",
                ) from error
        try:
            frame = await asyncio.to_thread(active_snapshot_fetcher, drive_snapshot_url)
            if not frame:
                raise RuntimeError("camera returned an empty snapshot")
            await asyncio.to_thread(active_vl_snapshot_dir.mkdir, parents=True, exist_ok=True)
            image_path = active_vl_snapshot_dir / f"inspection-{time_ns()}.jpg"
            await asyncio.to_thread(image_path.write_bytes, frame)
            edge_status = (
                await asyncio.to_thread(command_bridge.health)
                if command_bridge is not None
                else {
                    "ok": False,
                    "armed": False,
                    "controlMode": "simulation-no-motor",
                    "reason": "EDGE_NOT_CONFIGURED",
                }
            )
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="failed to capture VL inspection context",
            ) from error
        cancel_pending_defect_publish()
        app.state.inspection_step = 0
        app.state.inspection_context = request.model_dump()
        app.state.latest_progress_facts = {
            "situation": "관로 점검 시작",
            "decidedAction": "상태 모니터링",
        }
        try:
            status = await active_hermes_relay.start(
                {
                    "image_path": str(image_path.resolve()),
                    "edge_status": edge_status,
                }
            )
            mission_agent.start_mission(request.area)
            app.state.latest_source_frame = frame
            app.state.latest_source_frame_identity = (
                request.session_id,
                request.operation_id,
            )
        except RuntimeError as error:
            clear_inspection_context()
            raise HTTPException(status_code=409, detail=str(error)) from error
        return asdict(status)

    @app.post("/api/hermes/stop")
    async def stop_hermes_vl() -> dict[str, object]:
        relay_status = await active_hermes_relay.stop()
        await stop_autonomy_loop()
        if command_bridge is not None:
            if command_bridge.status.armed:
                await asyncio.to_thread(
                    command_bridge.hold_stop,
                    "HERMES_DASHBOARD_STOP",
                )
            await asyncio.to_thread(
                command_bridge.stop_and_disarm,
                "HERMES_DASHBOARD_STOP",
            )
        if mission_agent.snapshot.state is MissionState.RUNNING:
            mission_agent.complete_mission()
        if mission_agent.snapshot.state is not MissionState.IDLE:
            mission_agent.reset()
        clear_inspection_context()
        return asdict(relay_status)

    @app.post("/api/hermes/complete")
    async def complete_hermes_vl(request: HermesCompleteRequest) -> dict[str, object]:
        if app.state.inspection_context.get("operation_id") != request.operation_id:
            raise HTTPException(status_code=409, detail="inspection operation does not match")
        try:
            relay_status = await active_hermes_relay.complete(request.message)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if relay_status.state == "IDLE":
            if mission_agent.snapshot.state is MissionState.RUNNING:
                mission_agent.complete_mission()
            if mission_agent.snapshot.state is not MissionState.IDLE:
                mission_agent.reset()
        elif mission_agent.snapshot.state is MissionState.RUNNING:
            mission_agent.snapshot.state = MissionState.ERROR
            mission_agent.snapshot.reason_code = "REPLAY_STOP_UNVERIFIED"
            mission_agent.snapshot.message = (
                "Replay ended without verified STOP/DISARM confirmation."
            )
            mission_agent.snapshot.agent_action = "AWAIT_OPERATOR_VERIFICATION"
            mission_agent.snapshot.agent_reason = request.message
            mission_agent.snapshot.ended_at = utc_now()
        clear_inspection_context()
        await progress_service.reset()
        return asdict(relay_status)

    @app.post("/api/hermes/stage", status_code=202)
    async def publish_hermes_stage(
        request: HermesDecisionRequest,
    ) -> dict[str, object]:
        context = dict(app.state.inspection_context)
        session_id = context.get("session_id")
        operation_id = context.get("operation_id")
        if not isinstance(session_id, int) or session_id <= 0 or not isinstance(
            operation_id, str
        ) or not operation_id:
            raise HTTPException(status_code=409, detail="no active inspection identity")
        app.state.inspection_step = request.step
        if not _automatic_yolo_db_publish_allowed(request.step):
            cancel_pending_defect_publish()
        if request.step == 20 and "BLOCKAGE" in request.action.upper():
            schedule_blockage_publish(
                session_id=session_id,
                operation_id=operation_id,
            )
        message = f"이미지 분석 : {request.analysis}\n대처 동작 : {request.action}"
        event_index = mission_agent.event_cursor
        mission_agent.record_event(
            "VL_DECISION",
            message,
            {"step": request.step, "operationId": operation_id},
        )
        publish_new_events(event_index, mission_agent.snapshot)
        app.state.latest_progress_facts = {
            "situation": request.analysis,
            "decidedAction": request.action,
        }
        return {
            "accepted": True,
            "sessionId": session_id,
            "operationId": operation_id,
            "revision": progress_service.current.revision,
        }

    @app.get("/api/yolo/status")
    async def yolo_status() -> dict[str, object]:
        status = active_yolo_detector.get_status()
        if not status.available:
            return {
                "available": False,
                "loaded": False,
                "message": status.message,
                "detections": [],
                "latencyMs": None,
                "frameWidth": None,
                "frameHeight": None,
            }
        started_at = monotonic()
        try:
            if not status.loaded:
                await asyncio.to_thread(active_yolo_detector.load)
            frame_bytes = await asyncio.to_thread(
                active_snapshot_fetcher,
                drive_snapshot_url,
            )
            detections = await asyncio.to_thread(
                active_yolo_detector.infer_to_temp,
                frame_bytes,
                f"dashboard-{int(started_at * 1000)}",
            )
        except Exception as error:
            return {
                "available": True,
                "loaded": active_yolo_detector.get_status().loaded,
                "message": f"YOLO error: {type(error).__name__}",
                "detections": [],
                "latencyMs": (monotonic() - started_at) * 1000,
                "frameWidth": None,
                "frameHeight": None,
            }
        detection_payload = [
            detection.model_dump(mode="json") for detection in detections
        ]
        frame_width = getattr(active_yolo_detector, "last_frame_width", None)
        frame_height = getattr(active_yolo_detector, "last_frame_height", None)
        if detection_payload:
            await asyncio.to_thread(
                active_yolo_capture_store.add,
                frame_bytes,
                detection_payload,
                frame_width=frame_width,
                frame_height=frame_height,
            )
        return {
            "available": True,
            "loaded": True,
            "message": "YOLO segmentation ready",
            "detections": detection_payload,
            "latencyMs": (monotonic() - started_at) * 1000,
            "frameWidth": frame_width,
            "frameHeight": frame_height,
        }

    @app.get("/api/vision/yolo-frame.jpg")
    async def yolo_annotated_frame() -> Response:
        try:
            _, rendered, _ = await produce_yolo_frame()
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=f"YOLO annotated frame unavailable: {type(error).__name__}",
            ) from error
        return Response(
            content=rendered,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/yolo/history")
    async def yolo_history(limit: int = 20) -> dict[str, object]:
        return {
            "items": await asyncio.to_thread(active_yolo_capture_store.history, limit)
        }

    @app.get("/api/yolo/captures/{capture_id}.jpg")
    async def yolo_capture(capture_id: str) -> FileResponse:
        image_path = await asyncio.to_thread(
            active_yolo_capture_store.image_path,
            capture_id,
        )
        if image_path is None:
            raise HTTPException(status_code=404, detail="YOLO capture not found")
        return FileResponse(image_path, media_type="image/jpeg")

    @app.get("/api/mission", response_model=MissionSnapshot)
    async def mission() -> MissionSnapshot:
        return mission_agent.snapshot.model_copy(deep=True)

    @app.get("/api/logs", response_model=list[AgentEvent])
    async def logs() -> list[AgentEvent]:
        return [event.model_copy(deep=True) for event in mission_agent.events]

    @app.get("/api/progress-summary", response_model=ProgressAnnouncement)
    async def progress_summary() -> ProgressAnnouncement:
        return progress_service.current.model_copy(deep=True)

    @app.post("/api/progress-summary/defect", response_model=None)
    async def report_progress_defect(
        http_request: Request,
        request: DefectProgressRequest,
    ) -> JSONResponse:
        client_host = http_request.client.host if http_request.client is not None else ""
        if client_host not in app.state.progress_trusted_hosts:
            raise HTTPException(status_code=403, detail="신뢰된 local Dashboard 요청만 허용됩니다.")
        snapshot = mission_agent.snapshot
        if snapshot.state is not MissionState.RUNNING or snapshot.mission_id is None:
            raise HTTPException(
                status_code=409,
                detail="진행 중인 임무에서 확정된 결함만 보고할 수 있습니다.",
            )
        if request.mission_id != snapshot.mission_id:
            raise HTTPException(status_code=409, detail="현재 임무와 결함 event의 임무가 다릅니다.")
        if request.event_id in app.state.progress_defect_ids:
            return JSONResponse(status_code=200, content={"accepted": False})
        now = monotonic()
        if now - app.state.progress_defect_last_accepted < 1.0:
            raise HTTPException(status_code=429, detail="결함 안내 요청이 너무 빠릅니다.")
        event = progress_detector.report_defect(
            defect_type=request.defect_type,
            confidence=request.confidence,
            location_m=request.location_m,
        )
        if not schedule_progress_event(event, snapshot):
            raise HTTPException(status_code=429, detail="Qwen 안내 작업 대기열이 가득 찼습니다.")
        app.state.progress_defect_ids.add(request.event_id)
        app.state.progress_defect_last_accepted = now
        return JSONResponse(status_code=202, content={"accepted": True})

    @app.get("/api/models", response_model=ActiveModelStatus)
    async def models() -> ActiveModelStatus:
        perception = get_model_spec("depth-anything-v2-small")
        decision = get_model_spec("qwen2.5-vl-3b-instruct-awq")
        return ActiveModelStatus(
            perception=ModelRuntimeStatus(
                name=perception.name,
                role=perception.role.value,
                repo_id=perception.repo_id,
                runtime=perception.runtime,
                downloaded=model_files_present(perception.name),
                loaded=app.state.perception_loaded,
                note="단안 상대 깊이로 좌·중·우 자유공간과 장애물 위험을 계산합니다.",
            ),
            decision=ModelRuntimeStatus(
                name=decision.name,
                role=decision.role.value,
                repo_id=decision.repo_id,
                runtime=decision.runtime,
                downloaded=model_files_present(decision.name),
                loaded=bool(getattr(active_progress_provider, "loaded", False)),
                note="모호한 장애물 이벤트에서만 lazy-load하는 4-bit AWQ 모델입니다.",
            ),
        )

    @app.get("/api/drive", response_model=DriveStatus)
    async def drive() -> DriveStatus:
        return await live_drive_snapshot()

    @app.get("/api/navigation", response_model=NavigationStatusResponse)
    async def navigation_status() -> NavigationStatusResponse:
        return navigation_status_response(active_navigation.status())

    @app.get("/api/edge", response_model=CommandBridgeStatus)
    async def edge_status() -> CommandBridgeStatus:
        if command_bridge is None:
            return CommandBridgeStatus(control_mode="simulation-no-motor")
        return command_bridge.status.model_copy(deep=True)

    @app.post("/api/mission/start", response_model=MissionSnapshot)
    async def start_mission(request: StartMissionRequest) -> MissionSnapshot:
        if mission_agent.snapshot.state.value != "IDLE":
            raise HTTPException(
                status_code=409,
                detail="새 임무를 시작하려면 현재 임무를 먼저 초기화해야 합니다.",
            )
        active_navigation.reset()
        drive_controller.reset()
        progress_detector.reset()
        app.state.progress_defect_ids.clear()
        app.state.progress_defect_last_accepted = 0.0
        drive_status.latest_observation = None
        drive_status.latest_perception = None
        drive_status.latest_plan = None
        drive_status.latest_command = None
        event_index = mission_agent.event_cursor
        snapshot = mission_agent.start_mission(request.area)
        if command_bridge is not None:
            assert snapshot.mission_id is not None
            try:
                if active_navigation.active_version is NavigationVersion.V2:
                    imu_health = await asyncio.to_thread(command_bridge.health)
                    imu_payload = imu_health.get("imu")
                    if not isinstance(imu_payload, dict) or not bool(
                        imu_payload.get("available")
                    ):
                        raise RuntimeError("V2 requires an available Pi MPU6050 IMU")
                    _validate_v2_imu_status(imu_payload)
                load_perception = getattr(active_perception, "load", None)
                if callable(load_perception):
                    await asyncio.to_thread(load_perception)
                    app.state.perception_loaded = True
                if active_navigation.active_version is NavigationVersion.V2:
                    await asyncio.to_thread(command_bridge.calibrate_imu, 100)
                command_bridge.arm(snapshot.mission_id)
            except RuntimeError as error:

                try:
                    command_bridge.stop_and_disarm("EDGE_PREFLIGHT_FAILED")
                except RuntimeError:
                    pass
                failed = mission_agent.fail_system(str(error))
                await progress_service.invalidate(
                    mission_id=failed.mission_id,
                    mission_state=failed.state,
                )
                publish_new_events(0, failed)
                raise HTTPException(
                    status_code=503,
                    detail=f"Edge 안전 점검 실패: {error}",
                ) from error
            mission_agent.snapshot.control_mode = "real-motor"
            snapshot.control_mode = "real-motor"
            drive_status.control_mode = "real-motor"
            app.state.heartbeat_task = asyncio.create_task(run_heartbeat_loop())
        publish_new_events(event_index, snapshot)
        if auto_drive_loop and command_bridge is not None:
            app.state.autonomy_task = asyncio.create_task(run_autonomy_loop())
        return snapshot

    @app.post("/api/mission/telemetry", response_model=AgentDecision)
    async def telemetry(observation: TelemetryObservation) -> AgentDecision:
        event_index = mission_agent.event_cursor
        try:
            decision = mission_agent.evaluate_telemetry(observation)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        publish_new_events(event_index, mission_agent.snapshot)
        if decision.mission_state is MissionState.FAILED_BLOCKED:
            snapshot = mission_agent.snapshot
            await progress_service.invalidate(
                mission_id=snapshot.mission_id,
                mission_state=snapshot.state,
            )
            progress_event = progress_detector.observe_mission(
                mission_state=snapshot.state,
                reason=decision.reason,
            )
            if progress_event is not None:
                schedule_progress_event(progress_event, snapshot)
        return decision

    @app.post("/api/mission/blocked", response_model=MissionSnapshot)
    async def blocked(request: BlockedMissionRequest) -> MissionSnapshot:
        event_index = mission_agent.event_cursor
        try:
            snapshot = mission_agent.fail_blocked(request.reason, request.retry_count)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await stop_autonomy_loop()
        if command_bridge is not None:
            command_bridge.stop_and_disarm("MISSION_BLOCKED")
        await progress_service.invalidate(
            mission_id=snapshot.mission_id,
            mission_state=snapshot.state,
        )
        publish_new_events(event_index, snapshot)
        progress_event = progress_detector.observe_mission(
            mission_state=snapshot.state,
            reason=request.reason,
        )
        if progress_event is not None:
            schedule_progress_event(progress_event, snapshot)
        return snapshot

    @app.post("/api/mission/complete", response_model=MissionSnapshot)
    async def complete() -> MissionSnapshot:
        event_index = mission_agent.event_cursor
        try:
            snapshot = mission_agent.complete_mission()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await stop_autonomy_loop()
        if command_bridge is not None:
            command_bridge.stop_and_disarm("MISSION_COMPLETED")
        await progress_service.invalidate(
            mission_id=snapshot.mission_id,
            mission_state=snapshot.state,
        )
        publish_new_events(event_index, snapshot)
        return snapshot

    @app.post("/api/mission/stop", response_model=MissionSnapshot)
    async def stop_mission() -> MissionSnapshot:
        """Stop mission immediately and disarm."""
        event_index = mission_agent.event_cursor

        # Stop autonomy loop
        await stop_autonomy_loop()

        # Stop motor and disarm
        if command_bridge is not None:
            await asyncio.to_thread(command_bridge.hold_stop, "MISSION_STOP_REQUEST")
            await asyncio.to_thread(command_bridge.stop_and_disarm, "MISSION_STOP_REQUEST")

        # Update mission state (same as complete but with STOPPING state)
        try:
            if mission_agent.snapshot.state.value == "RUNNING":
                snapshot = mission_agent.fail_system("사용자가 검사를 중지했습니다")
            else:
                snapshot = mission_agent.snapshot
        except ValueError:
            snapshot = mission_agent.snapshot

        await progress_service.invalidate(
            mission_id=snapshot.mission_id,
            mission_state=snapshot.state,
        )
        publish_new_events(event_index, snapshot)
        return snapshot

    @app.post("/api/mission/reset", response_model=MissionSnapshot)
    async def reset() -> MissionSnapshot:
        await stop_autonomy_loop()
        if command_bridge is not None and command_bridge.status.armed:
            await asyncio.to_thread(command_bridge.stop_and_disarm, "MISSION_RESET")
        if (
            command_bridge is not None
            and active_navigation.active_version is NavigationVersion.V2
        ):
            try:
                await asyncio.to_thread(command_bridge.reset_imu)
            except RuntimeError as error:
                raise HTTPException(
                    status_code=503,
                    detail=f"V2 IMU 초기화 실패: {error}",
                ) from error
        if command_bridge is not None:
            command_bridge.reset_session()
        try:
            snapshot = mission_agent.reset()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        active_navigation.reset()
        drive_controller.reset()
        progress_detector.reset()
        app.state.progress_defect_ids.clear()
        app.state.progress_defect_last_accepted = 0.0
        drive_status.latest_observation = None
        drive_status.latest_perception = None
        drive_status.latest_plan = None
        drive_status.latest_command = None
        snapshot.control_mode = drive_status.control_mode
        mission_agent.snapshot.control_mode = drive_status.control_mode
        await progress_service.reset()
        hub.publish({"type": "mission.snapshot", "mission": _dump(snapshot)})
        return snapshot

    @app.websocket("/ws/events")
    async def events(websocket: WebSocket) -> None:
        await stream_events(websocket, mission_agent, hub, progress_service)


    return app


def create_runtime_app() -> FastAPI:
    edge_url = os.environ.get("PIPECHECKKK_EDGE_URL", "").strip()
    edge_token = os.environ.get("PIPECHECKKK_EDGE_TOKEN", "").strip()
    if not edge_url or not edge_token:
        raise RuntimeError(
            "real-motor runtime requires both PIPECHECKKK_EDGE_URL "
            "and PIPECHECKKK_EDGE_TOKEN"
        )
    bridge = CommandBridge(HttpEdgeClient(edge_url, edge_token))
    navigation_router = NavigationRouter(base=LocalFreeSpacePlanner())
    return create_app(
        command_bridge=bridge,
        navigation_router=navigation_router,
        progress_provider=Qwen25Vl3BAwqProvider(),
        auto_drive_loop=bridge is not None,
        preload_perception=True,
        preload_progress=True,
        drive_snapshot_url=os.environ.get(
            "PIPECHECKKK_CAMERA_SNAPSHOT_URL",
            "http://192.168.100.246:8081/snapshot.jpg",
        ),
    )


app = create_runtime_app()
