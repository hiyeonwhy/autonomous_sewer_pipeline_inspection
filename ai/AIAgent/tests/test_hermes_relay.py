from __future__ import annotations

import asyncio
import json
import threading
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import pipecheckkk_ai.api as api_module
import pipecheckkk_ai.hermes_relay as hermes_relay_module
from pipecheckkk_ai.api import create_app
from pipecheckkk_ai.hermes_relay import HermesInspectionRelay, run_hermes_discord_webhook
from pipecheckkk_ai.models import MissionState, ProgressAnnouncement


@pytest.mark.asyncio
async def test_relay_sends_only_valid_vl_context_to_discord_webhook() -> None:
    received: list[dict[str, object]] = []
    release = asyncio.Event()

    async def runner(payload: dict[str, object]) -> str:
        received.append(payload)
        await release.wait()
        return "delivered"

    relay = HermesInspectionRelay(runner=runner)
    payload = {
        "image_path": "/tmp/frame.jpg",
        "edge_status": {"armed": False, "frontDistanceM": 0.42},
    }
    await relay.start(payload)
    await asyncio.sleep(0)

    assert received == [payload]
    assert relay.status().state == "RUNNING"
    assert relay.status().command == "점검시작"
    assert "Discord" in relay.status().message

    release.set()
    await relay.wait()
    assert relay.status().state == "RUNNING"
    assert relay.status().command == "점검시작"
    assert relay.status().ended_at is None

    completed = await relay.complete("STOP_DISARMED")
    assert completed.state == "IDLE"
    assert completed.command is None
    assert completed.ended_at is not None

    restarted = await relay.start(payload)
    assert restarted.state == "RUNNING"
    stopped = await relay.stop()
    assert stopped.state == "IDLE"
    assert stopped.command is None


@pytest.mark.asyncio
async def test_unverified_replay_failure_is_terminal_but_not_complete() -> None:
    async def runner(_payload: dict[str, object]) -> str:
        await asyncio.Event().wait()
        return "unreachable"

    relay = HermesInspectionRelay(runner=runner)
    await relay.start(
        {"image_path": "/tmp/frame.jpg", "edge_status": {"armed": False}}
    )

    failed = await relay.complete("STOP_DISARM_REQUESTED")

    assert failed.state == "ERROR"
    assert failed.command is None
    assert failed.message == "STOP_DISARM_REQUESTED"
    assert failed.ended_at is not None


@pytest.mark.asyncio
async def test_webhook_runner_uses_hermes_test_without_gui_or_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"delivered", b""

        def kill(self) -> None:
            raise AssertionError("successful webhook must not be killed")

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args: str, **_kwargs: object):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        hermes_relay_module.shutil,
        "which",
        lambda name: "/opt/hermes" if name == "hermes" else None,
    )
    payload = {"image_path": "/tmp/frame.jpg", "edge_status": {"armed": False}}

    result = await run_hermes_discord_webhook(payload, timeout_s=1)

    assert result == "delivered"
    assert calls == [
        (
            "/opt/hermes",
            "webhook",
            "test",
            "pipecheckkk-vl",
            "--payload",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    ]
    assert all("gnome-terminal" not in call and "tmux" not in call for call in calls)


def test_hermes_start_api_captures_frame_and_edge_status_then_stop_disarms(
    tmp_path: Path,
) -> None:
    received: list[dict[str, object]] = []

    async def runner(payload: dict[str, object]) -> str:
        received.append(payload)
        await asyncio.Event().wait()
        return "unreachable"

    class FakeBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.status = SimpleNamespace(armed=True)

        def health(self) -> dict[str, object]:
            return {
                "ok": True,
                "armed": self.status.armed,
                "frontDistanceM": 0.42,
                "imu": {"valid": True, "yawDeg": 1.0, "rollDeg": 2.0, "pitchDeg": 3.0},
            }

        def hold_stop(self, reason: str) -> None:
            self.calls.append(("stop", reason))

        def stop_and_disarm(self, reason: str) -> None:
            self.calls.append(("disarm", reason))
            self.status.armed = False

    bridge = FakeBridge()
    relay = HermesInspectionRelay(runner=runner)
    application = create_app(
        command_bridge=bridge,  # type: ignore[arg-type]
        hermes_relay=relay,
        snapshot_fetcher=lambda _url: b"real-jpeg-bytes",
        vl_snapshot_dir=tmp_path,
    )
    with TestClient(application) as client:
        started = client.post(
            "/api/hermes/start",
            json={
                "area": "광주광역시 광산구 구간1",
                "session_id": 17,
                "operation_id": "operation-17",
            },
        )
        started_context = dict(application.state.inspection_context)
        decision = client.post(
            "/api/hermes/stage",
            json={
                "analysis": "기록1 단계 판단과 현재 영상이 일치합니다.",
                "action": "TURN_LEFT 10도",
                "step": 1,
            },
        )
        logs = client.get("/api/logs")
        duplicate = client.post(
            "/api/hermes/start",
            json={
                "area": "광주광역시 광산구 구간1",
                "session_id": 17,
                "operation_id": "operation-17",
            },
        )
        mission_after_start = application.state.agent.snapshot.state.value
        stopped = client.post("/api/hermes/stop")
        mission_after_stop = application.state.agent.snapshot.state.value

    assert started_context == {
        "area": "광주광역시 광산구 구간1",
        "session_id": 17,
        "operation_id": "operation-17",
    }
    assert application.state.inspection_context == {
        "area": None,
        "session_id": None,
        "operation_id": None,
    }

    assert started.status_code == 202
    assert started.json()["command"] == "점검시작"
    assert mission_after_start == "RUNNING"
    assert mission_after_stop == "IDLE"
    assert duplicate.status_code == 409
    assert decision.status_code == 202
    assert decision.json()["accepted"] is True
    assert [event["message"] for event in logs.json()][-1:] == [
        "이미지 분석 : 기록1 단계 판단과 현재 영상이 일치합니다.\n"
        "대처 동작 : TURN_LEFT 10도",
    ]
    assert len(received) == 1
    image_path = Path(str(received[0]["image_path"]))
    assert image_path.parent == tmp_path
    assert image_path.read_bytes() == b"real-jpeg-bytes"
    assert received[0]["edge_status"] == {
        "ok": True,
        "armed": False,
        "frontDistanceM": 0.42,
        "imu": {"valid": True, "yawDeg": 1.0, "rollDeg": 2.0, "pitchDeg": 3.0},
    }
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "IDLE"
    assert stopped.json()["command"] is None
    assert bridge.calls == [
        ("stop", "HERMES_VL_AWAITING_APPROVAL"),
        ("disarm", "HERMES_VL_AWAITING_APPROVAL"),
        ("disarm", "HERMES_DASHBOARD_STOP"),
    ]


def test_hermes_complete_callback_sets_relay_and_mission_idle(tmp_path: Path) -> None:
    async def runner(_payload: dict[str, object]) -> str:
        return "accepted"

    application = create_app(
        hermes_relay=HermesInspectionRelay(runner=runner),
        snapshot_fetcher=lambda _url: b"real-jpeg-bytes",
        vl_snapshot_dir=tmp_path,
    )
    with TestClient(application) as client:
        started = client.post(
            "/api/hermes/start",
            json={
                "area": "완료 콜백 시험",
                "session_id": 18,
                "operation_id": "operation-18",
            },
        )
        completed = client.post(
            "/api/hermes/complete",
            json={"operation_id": "operation-18", "message": "STOP_DISARMED"},
        )

    assert started.status_code == 202
    assert completed.status_code == 200
    assert completed.json()["state"] == "IDLE"
    assert application.state.agent.snapshot.state.value == "IDLE"
    assert application.state.inspection_context["operation_id"] is None
    assert application.state.latest_source_frame is None
    assert application.state.progress_service.current.status == "idle"
    assert application.state.progress_service.current.mission_state is MissionState.IDLE


def test_unverified_complete_keeps_mission_uncompleted_and_rejects_late_stage(
    tmp_path: Path,
) -> None:
    class PendingTask:
        cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    async def runner(_payload: dict[str, object]) -> str:
        await asyncio.Event().wait()
        return "unreachable"

    application = create_app(
        hermes_relay=HermesInspectionRelay(runner=runner),
        snapshot_fetcher=lambda _url: b"failure-frame",
        vl_snapshot_dir=tmp_path,
    )
    application.state.progress_service.current = ProgressAnnouncement(
        mission_id="operation-failure",
        mission_state=MissionState.RUNNING,
        message="old qwen result",
        status="ready",
    )
    with TestClient(application) as client:
        assert client.post(
            "/api/hermes/start",
            json={
                "area": "실패 종료 시험",
                "session_id": 19,
                "operation_id": "operation-failure",
            },
        ).status_code == 202
        failed = client.post(
            "/api/hermes/complete",
            json={
                "operation_id": "operation-failure",
                "message": "STOP_DISARM_REQUESTED",
            },
        )
        pending_task = PendingTask()
        application.state.defect_publish_task = pending_task
        step_before = application.state.inspection_step
        late = client.post(
            "/api/hermes/stage",
            json={"analysis": "late", "action": "BLOCKAGE", "step": 20},
        )
        pending_task_unchanged = application.state.defect_publish_task is pending_task
        pending_task_cancelled = pending_task.cancelled
        application.state.defect_publish_task = None

    assert failed.status_code == 200
    assert failed.json()["state"] == "ERROR"
    assert application.state.agent.snapshot.state.value == "ERROR"
    assert application.state.agent.snapshot.reason_code == "REPLAY_STOP_UNVERIFIED"
    assert application.state.agent.snapshot.agent_action == "AWAIT_OPERATOR_VERIFICATION"
    assert application.state.inspection_context["operation_id"] is None
    assert application.state.progress_service.current.status == "idle"
    assert late.status_code == 409
    assert application.state.inspection_step == step_before == 0
    assert pending_task_unchanged is True
    assert pending_task_cancelled is False


def test_blockage_publish_failure_never_changes_running_state(tmp_path: Path) -> None:
    class FailingCloudPublisher:
        def __init__(self) -> None:
            self.blockage_calls: list[bytes] = []

        async def upload_blockage(self, frame: bytes, **_kwargs: object) -> None:
            self.blockage_calls.append(frame)
            raise RuntimeError("cloud unavailable")

        async def upload_telemetry(self, _payload: dict[str, object]) -> None:
            return None

        async def upload_log(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def upload_live_frame(self, _frame: bytes) -> None:
            return None

        async def upload_frame(self, _frame: bytes, **_kwargs: object) -> None:
            return None

    class UnavailableYolo:
        def get_status(self):
            return SimpleNamespace(available=False, loaded=False, message="unavailable")

    async def runner(_payload: dict[str, object]) -> str:
        await asyncio.Event().wait()
        return "unreachable"

    publisher = FailingCloudPublisher()
    application = create_app(
        hermes_relay=HermesInspectionRelay(runner=runner),
        snapshot_fetcher=lambda _url: b"original-blockage-jpeg",
        vl_snapshot_dir=tmp_path,
        yolo_detector=UnavailableYolo(),  # type: ignore[arg-type]
        cloud_publisher=publisher,  # type: ignore[arg-type]
    )

    with TestClient(application) as client:
        assert client.post(
            "/api/hermes/start",
            json={"area": "구간1", "session_id": 185, "operation_id": "operation-185"},
        ).status_code == 202
        stage = client.post(
            "/api/hermes/stage",
            json={
                "analysis": "전방 막힘을 확인",
                "action": "BLOCKAGE를 전송한 뒤 TEMP_IMU yaw 대비 180°까지 연속 회전",
                "step": 20,
            },
        )
        time.sleep(0.05)

        assert stage.status_code == 202
        assert publisher.blockage_calls == [b"original-blockage-jpeg"]
        assert client.get("/api/hermes/status").json()["state"] == "RUNNING"
        assert client.get("/api/mission").json()["state"] == "RUNNING"
        client.post("/api/hermes/stop")


def test_busy_fallback_upload_does_not_starve_scoped_inspection_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_uploaded = threading.Event()
    source = BytesIO()
    Image.new("RGB", (8, 8), "white").save(source, format="JPEG")
    frame = source.getvalue()

    class Publisher:
        async def upload_live_frame(self, _frame: bytes) -> None:
            await asyncio.sleep(10)

        async def upload_frame(self, _frame: bytes, **_kwargs: object) -> None:
            scoped_uploaded.set()

        async def upload_telemetry(self, _payload: dict[str, object]) -> None:
            return None

        async def upload_log(self, *_args: object, **_kwargs: object) -> None:
            return None

    class FrameStream:
        def submit(self, _frame: bytes) -> None:
            return None

        async def run(self) -> None:
            await asyncio.Event().wait()

    class Detector:
        def get_status(self):
            return SimpleNamespace(available=True, loaded=True, message="ready")

        def infer_to_temp(self, _frame: bytes, _frame_id: str):
            return []

    async def runner(_payload: dict[str, object]) -> str:
        await asyncio.Event().wait()
        return "unreachable"

    monkeypatch.setenv("PIPECHECKKK_CLOUD_API_URL", "https://cloud.invalid")
    monkeypatch.setattr(api_module, "CloudFrameStreamPublisher", lambda **_kwargs: FrameStream())
    application = create_app(
        hermes_relay=HermesInspectionRelay(runner=runner),
        snapshot_fetcher=lambda _url: frame,
        vl_snapshot_dir=tmp_path,
        yolo_detector=Detector(),  # type: ignore[arg-type]
        cloud_publisher=Publisher(),  # type: ignore[arg-type]
    )
    with TestClient(application) as client:
        assert client.post(
            "/api/hermes/start",
            json={"area": "upload", "session_id": 23, "operation_id": "operation-23"},
        ).status_code == 202
        assert scoped_uploaded.wait(timeout=0.75)


def test_blockage_rejects_cached_frame_from_prior_inspection(tmp_path: Path) -> None:
    uploaded = threading.Event()
    blockage_frames: list[bytes] = []
    snapshots = iter((b"current-start-frame", b"current-blockage-frame"))

    class Publisher:
        async def upload_blockage(self, frame: bytes, **_kwargs: object) -> None:
            blockage_frames.append(frame)
            uploaded.set()

        async def upload_telemetry(self, _payload: dict[str, object]) -> None:
            return None

        async def upload_log(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def upload_live_frame(self, _frame: bytes) -> None:
            return None

        async def upload_frame(self, _frame: bytes, **_kwargs: object) -> None:
            return None

    class UnavailableYolo:
        def get_status(self):
            return SimpleNamespace(available=False, loaded=False, message="unavailable")

    async def runner(_payload: dict[str, object]) -> str:
        await asyncio.Event().wait()
        return "unreachable"

    application = create_app(
        hermes_relay=HermesInspectionRelay(runner=runner),
        snapshot_fetcher=lambda _url: next(snapshots),
        vl_snapshot_dir=tmp_path,
        yolo_detector=UnavailableYolo(),  # type: ignore[arg-type]
        cloud_publisher=Publisher(),  # type: ignore[arg-type]
    )
    with TestClient(application) as client:
        assert client.post(
            "/api/hermes/start",
            json={"area": "new", "session_id": 24, "operation_id": "operation-24"},
        ).status_code == 202
        application.state.latest_source_frame = b"prior-inspection-frame"
        application.state.latest_source_frame_identity = (23, "operation-23")
        assert client.post(
            "/api/hermes/stage",
            json={"analysis": "blocked", "action": "BLOCKAGE", "step": 20},
        ).status_code == 202
        assert uploaded.wait(timeout=0.5)
        assert client.post("/api/hermes/stop").status_code == 200

    assert blockage_frames == [b"current-blockage-frame"]
    assert application.state.latest_source_frame is None
