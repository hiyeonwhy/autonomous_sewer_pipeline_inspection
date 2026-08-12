import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from pipecheckkk_ai.api import (
    EventHub,
    _automatic_yolo_db_publish_allowed,
    _cloud_robot_log_payload,
    _normalize_cloud_event_cursor,
    _parse_front_distance,
    _sanitize_dashboard_log_text,
    _validate_v2_imu_status,
    _yolo_detection_event_id,
    create_app,
    create_runtime_app,
    stream_events,
)
from pipecheckkk_ai.command.bridge import CommandBridge
from pipecheckkk_ai.local_planner import LocalFreeSpacePlanner
from pipecheckkk_ai.model_registry import model_files_present
from pipecheckkk_ai.navigation import NavigationRouter
from pipecheckkk_ai.models import (
    PerceptionResult,
    ProgressAnnouncement,
    utc_now,
)


def test_cloud_robot_log_contains_only_image_analysis() -> None:
    payload = _cloud_robot_log_payload(
        category="VL_DECISION",
        level="info",
        message=(
            "이미지 분석 : 장애물 측정 거리 기반, 장애물 회피가 아직 완료되지 않았다고 판단, "
            "현재 front=1.019m, IMU yaw=-16.4°, roll=-3.4°, pitch=8.5°\n"
            "대처 동작 : 총 3초 전진 중 첫 1.5초"
        ),
        timestamp="2026-08-09T13:28:51Z",
    )

    assert payload == {
        "command_source": "이미지 분석",
        "agent_state": "info",
        "note": (
            "이미지 분석 : 장애물 측정 거리 기반, 장애물 회피가 아직 완료되지 않았다고 판단, "
            "현재 front=1.019m, IMU yaw=-16.4°, roll=-3.4°, pitch=8.5°"
        ),
        "logged_at": "2026-08-09T13:28:51Z",
    }
    assert _cloud_robot_log_payload(
        category="QWEN", level="info", message="요약", timestamp="now"
    ) is None


def test_cloud_event_cursor_resets_for_new_inspection_and_bounded_history() -> None:
    assert _normalize_cloud_event_cursor(
        uploaded_cursor=37,
        current_cursor=4,
        buffered_event_count=4,
        identity_changed=True,
    ) == 0
    assert _normalize_cloud_event_cursor(
        uploaded_cursor=0,
        current_cursor=510,
        buffered_event_count=500,
        identity_changed=False,
    ) == 10
    assert _normalize_cloud_event_cursor(
        uploaded_cursor=15,
        current_cursor=20,
        buffered_event_count=20,
        identity_changed=False,
    ) == 15


def test_yolo_detection_event_id_deduplicates_small_bbox_jitter() -> None:
    first = [{"label": "class_1", "confidence": 0.8, "bbox": [95, 100, 159, 164]}]
    jittered = [{"label": "class_1", "confidence": 0.9, "bbox": [96, 100, 160, 164]}]
    elsewhere = [{"label": "class_1", "confidence": 0.9, "bbox": [300, 120, 380, 210]}]
    assert _yolo_detection_event_id(first) == _yolo_detection_event_id(jittered)
    assert _yolo_detection_event_id(first) != _yolo_detection_event_id(elsewhere)
    pair = [first[0], elsewhere[0]]
    assert _yolo_detection_event_id(pair) == _yolo_detection_event_id(list(reversed(pair)))


def test_automatic_yolo_detection_and_db_publish_stop_at_return_phase() -> None:
    assert _automatic_yolo_db_publish_allowed(0) is True
    assert _automatic_yolo_db_publish_allowed(19) is True
    assert _automatic_yolo_db_publish_allowed(20) is False
    assert _automatic_yolo_db_publish_allowed(21) is False
    assert _automatic_yolo_db_publish_allowed(22) is False
    assert _automatic_yolo_db_publish_allowed(999) is False


def test_dashboard_log_text_hides_internal_replay_names() -> None:
    assert _sanitize_dashboard_log_text("Discord Hermes VL 기록1 점검이 실행 중입니다.") == (
        "AIagent 점검이 실행 중입니다."
    )
    assert "record-001" not in _sanitize_dashboard_log_text(
        "inspection-record-001.json 단계 완료"
    )


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "https://i15c102.p.ssafy.io"],
)
def test_cors_allows_c102_frontend_origins(origin: str) -> None:
    client = TestClient(create_app())

    response = client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("invalid_distance", [float("nan"), float("inf"), -0.01, True, "0.1"])
def test_edge_front_distance_parser_rejects_invalid_values(invalid_distance) -> None:
    with pytest.raises(RuntimeError, match="invalid ultrasonic"):
        _parse_front_distance(invalid_distance)


def test_edge_front_distance_parser_preserves_missing_and_finite_values() -> None:
    assert _parse_front_distance(None) is None
    assert _parse_front_distance(0.10) == pytest.approx(0.10)


def test_api_rejects_a_finite_pi_yaw_limit() -> None:
    with pytest.raises(RuntimeError, match="unlimited-yaw V2 IMU contract"):
        _validate_v2_imu_status(
            {
                "yawStepDeg": 10.0,
                "yawLimitDeg": 40.0,
                "pitchStopDeg": None,
                "warningPitchDeg": None,
                "rollStopDeg": None,
            }
        )


def test_api_accepts_monitor_only_pitch_and_roll_pi_imu_motion_contract() -> None:
    _validate_v2_imu_status(
        {
            "yawStepDeg": 10.0,
            "yawLimitDeg": None,
            "pitchStopDeg": None,
            "warningPitchDeg": None,
            "rollStopDeg": None,
        }
    )




class FakeProgressProvider:
    def generate_progress_announcement(self, **kwargs) -> ProgressAnnouncement:
        messages = {
            "ROBOT_ACTION": "왼쪽 회전 중",
            "OBSTACLE_DETECTED": "전방 장애물을 인식해 우측 회피를 시작합니다.",
            "OBSTACLE_AVOIDANCE_COMPLETED": "장애물 회피 완료 후 탐사를 계속합니다.",
            "EXPLORATION_UNAVAILABLE": "경로가 차단되어 현재 구간은 탐사 불가 상태입니다.",
            "DEFECT_DETECTED": "관로에서 결함을 발견했습니다.",
        }
        event_type = kwargs["event_type"]
        return ProgressAnnouncement(
            mission_id=kwargs["mission_id"],
            mission_state=kwargs["mission_state"],
            event_type=event_type,
            message=messages[event_type.value],
            generated_at=utc_now(),
            latency_ms=5,
            status="ready",
        )


class LoadTrackingProgressProvider(FakeProgressProvider):
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True


def test_progress_summary_api_and_websocket_start_with_idle_snapshot() -> None:
    with TestClient(create_app(progress_provider=FakeProgressProvider())) as client:
        response = client.get("/api/progress-summary")
        with client.websocket_connect("/ws/events") as websocket:
            mission_message = websocket.receive_json()
            progress_message = websocket.receive_json()

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
    assert mission_message["type"] == "mission.snapshot"
    assert progress_message["type"] == "progress.summary"
    assert progress_message["summary"]["status"] == "idle"


def test_hermes_stage_records_latest_facts_without_immediate_qwen_publish() -> None:
    class CountingProgressProvider(FakeProgressProvider):
        def __init__(self) -> None:
            self.calls = 0

        def generate_progress_announcement(self, **kwargs) -> ProgressAnnouncement:
            self.calls += 1
            return super().generate_progress_announcement(**kwargs)

    provider = CountingProgressProvider()
    app = create_app(progress_provider=provider)
    with TestClient(app) as client:
        app.state.inspection_context = {
            "area": "관로 A",
            "session_id": 17,
            "operation_id": "operation-17",
        }
        response = client.post(
            "/api/hermes/stage",
            json={
                "analysis": "왼쪽에 장애물 확인, 현재 front=1.228m",
                "action": "왼쪽으로 10도 회전",
                "step": 1,
            },
        )
        summary = client.get("/api/progress-summary").json()
        logs = client.get("/api/logs").json()

    expected = (
        "이미지 분석 : 왼쪽에 장애물 확인, 현재 front=1.228m\n"
        "대처 동작 : 왼쪽으로 10도 회전"
    )
    assert response.status_code == 202
    assert response.json()["sessionId"] == 17
    assert response.json()["operationId"] == "operation-17"
    assert summary["status"] == "idle"
    assert provider.calls == 0
    assert app.state.latest_progress_facts == {
        "situation": "왼쪽에 장애물 확인, 현재 front=1.228m",
        "decidedAction": "왼쪽으로 10도 회전",
    }
    assert logs[-1]["category"] == "VL_DECISION"
    assert logs[-1]["message"] == expected


def test_hermes_stage_does_not_start_slow_qwen_summary() -> None:
    class SlowProgressProvider(FakeProgressProvider):
        def generate_progress_announcement(self, **kwargs) -> ProgressAnnouncement:
            time.sleep(0.25)
            return super().generate_progress_announcement(**kwargs)

    app = create_app(progress_provider=SlowProgressProvider())
    with TestClient(app) as client:
        app.state.inspection_context = {
            "area": "관로 A",
            "session_id": 18,
            "operation_id": "operation-18",
        }
        started = time.monotonic()
        response = client.post(
            "/api/hermes/stage",
            json={
                "analysis": "전방 장애물 확인",
                "action": "장애물 회피",
                "step": 2,
            },
        )
        elapsed = time.monotonic() - started

        assert response.status_code == 202
        assert elapsed < 0.15
        assert client.get("/api/progress-summary").json()["status"] == "idle"


def test_websocket_subscribes_before_sending_initial_snapshots() -> None:
    app = create_app(progress_provider=FakeProgressProvider())
    hub = EventHub()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.subscriber_counts: list[int] = []

        async def accept(self) -> None:
            return None

        async def send_json(self, _payload) -> None:
            self.subscriber_counts.append(hub.subscriber_count)

        async def receive(self):
            return {"type": "websocket.disconnect"}

    socket = FakeWebSocket()
    asyncio.run(
        stream_events(
            socket,
            app.state.agent,
            hub,
            app.state.progress_service,
        )
    )

    assert socket.subscriber_counts
    assert socket.subscriber_counts[0] == 1


def test_confirmed_defect_generates_qwen_progress_summary() -> None:
    with TestClient(create_app(progress_provider=FakeProgressProvider())) as client:
        started = client.post("/api/mission/start", json={"area": "결함 알림 시험"})
        mission_id = started.json()["missionId"]
        response = client.post(
            "/api/progress-summary/defect",
            json={
                "eventId": "defect-1",
                "missionId": mission_id,
                "defectType": "CRACK",
                "confidence": 0.91,
                "locationM": 2.4,
            },
        )
        deadline = time.monotonic() + 2.0
        summary = client.get("/api/progress-summary").json()
        while summary["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.01)
            summary = client.get("/api/progress-summary").json()

    assert started.status_code == 200
    assert response.status_code == 202
    assert summary["eventType"] == "DEFECT_DETECTED"
    assert summary["status"] == "ready"


def test_defect_progress_endpoint_rejects_remote_lan_clients() -> None:
    app = create_app(progress_provider=FakeProgressProvider())
    # TestClient exposes the configured address as request.client.host.
    with TestClient(app, client=("192.168.100.77", 50000)) as client:
        started = client.post("/api/mission/start", json={"area": "원격 차단 시험"})
        response = client.post(
            "/api/progress-summary/defect",
            json={
                "eventId": "defect-remote",
                "missionId": started.json()["missionId"],
                "defectType": "CRACK",
                "confidence": 0.91,
                "locationM": 2.4,
            },
        )

    assert response.status_code == 403


def test_defect_progress_endpoint_is_idempotent_and_rate_limited() -> None:
    with TestClient(create_app(progress_provider=FakeProgressProvider())) as client:
        started = client.post("/api/mission/start", json={"area": "중복 방지 시험"})
        mission_id = started.json()["missionId"]
        payload = {
            "eventId": "defect-idempotent",
            "missionId": mission_id,
            "defectType": "CRACK",
            "confidence": 0.91,
            "locationM": 2.4,
        }
        first = client.post("/api/progress-summary/defect", json=payload)
        duplicate = client.post("/api/progress-summary/defect", json=payload)
        second = client.post(
            "/api/progress-summary/defect",
            json={**payload, "eventId": "defect-too-fast"},
        )

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json() == {"accepted": False}
    assert second.status_code == 429


def test_blocked_mission_generates_exploration_unavailable_summary() -> None:
    with TestClient(create_app(progress_provider=FakeProgressProvider())) as client:
        assert client.post(
            "/api/mission/start", json={"area": "Qwen 진행 요약 시험"}
        ).status_code == 200
        blocked = client.post(
            "/api/mission/blocked",
            json={"reason": "좌우 경로 차단", "retryCount": 3},
        )
        deadline = time.monotonic() + 2.0
        summary = client.get("/api/progress-summary").json()
        while summary["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.01)
            summary = client.get("/api/progress-summary").json()

    assert blocked.status_code == 200
    assert summary["status"] == "ready"
    assert summary["eventType"] == "EXPLORATION_UNAVAILABLE"
    assert "탐사 불가" in summary["message"]








class ApiFakeEdgeClient:
    def __init__(self) -> None:
        self.armed = False
        self.commands: list[dict[str, object]] = []
        self.stop_sequences: list[int] = []
        self.front_distance_m: float | None = 0.5
        self.ultrasonic_stop_m = 0.0
        self.heartbeat_count = 0
        self.imu_phase = "UNCALIBRATED"
        self.imu_calls: list[str] = []
        self.yaw_target_deg = 0.0
        self.motor_output = {"left": 0.0, "right": 0.0}
        self.pitch_deg = 0.0
        self.roll_deg = 0.0

    def health(self):
        return {
            "ok": True,
            "armed": self.armed,
            "emergencyStop": False,
            "frontDistanceM": self.front_distance_m,
            "ultrasonicStopM": self.ultrasonic_stop_m,
            "motorOutput": self.motor_output,
            "imu": {
                "available": True,
                "calibrated": self.imu_phase != "UNCALIBRATED",
                "phase": self.imu_phase,
                "pitchDeg": self.pitch_deg,
                "rollDeg": self.roll_deg,
                "yawDeg": self.yaw_target_deg,
                "pitchStopDeg": None,
                "warningPitchDeg": None,
                "rollStopDeg": None,
                "yawTargetDeg": self.yaw_target_deg,
                "yawStepDeg": 10.0,
                "yawLimitDeg": None,
            },
        }

    def arm(self) -> dict[str, object]:
        self.armed = True
        return {"armed": True, "reason": "ARMED"}

    def heartbeat(self) -> dict[str, object]:
        self.heartbeat_count += 1
        return {"ok": True, "armed": self.armed}

    def command(self, payload: dict[str, object]) -> dict[str, object]:
        self.commands.append(payload)
        return {
            "commandId": payload["commandId"],
            "sequence": payload["sequence"],
            "accepted": True,
            "reason": "COMMAND_APPLIED",
            "appliedLeftSpeed": payload["leftSpeed"],
            "appliedRightSpeed": payload["rightSpeed"],
            "timestamp": payload["issuedAt"],
        }

    def stop(self, sequence: int) -> dict[str, object]:
        self.stop_sequences.append(sequence)
        return {"accepted": True, "reason": "STOP_APPLIED", "sequence": sequence}

    def disarm(self) -> dict[str, object]:
        self.armed = False
        return {"armed": False, "reason": "DISARMED"}

    def imu_calibrate(self, samples: int = 100) -> dict[str, object]:
        assert self.armed is False or self.imu_phase == "COMPLETED"
        self.imu_calls.append(f"calibrate:{samples}")
        self.imu_phase = "READY"
        self.yaw_target_deg = 0.0
        return {"available": True, "calibrated": True, "phase": "READY"}

    def imu_start_pitch_guard(self) -> dict[str, object]:
        self.imu_calls.append("pitch_guard")
        self.imu_phase = "PITCH_GUARD"
        return {"available": True, "calibrated": True, "phase": self.imu_phase}

    def imu_start_yaw_step(self, direction: str) -> dict[str, object]:
        self.imu_calls.append(f"yaw_step:{direction}")
        self.yaw_target_deg += 10.0 if direction == "LEFT" else -10.0
        self.imu_phase = "PIVOT_YAW"
        return {
            "available": True,
            "calibrated": True,
            "phase": self.imu_phase,
            "yawDeg": self.yaw_target_deg,
            "yawTargetDeg": self.yaw_target_deg,
        }

    def imu_start_recovery(self) -> dict[str, object]:
        self.imu_calls.append("recover")
        self.imu_phase = "PRIMARY_YAW"
        return {"available": True, "calibrated": True, "phase": self.imu_phase}

    def imu_reset(self) -> dict[str, object]:
        assert self.armed is False
        self.imu_calls.append("reset")
        self.imu_phase = "UNCALIBRATED"
        self.yaw_target_deg = 0.0
        return {"available": True, "calibrated": False, "phase": self.imu_phase}


class FakePerceptionProvider:
    name = "fake-pipe-model"

    def analyze(self, image_bytes: bytes) -> PerceptionResult:
        assert image_bytes == b"jpeg-frame"
        return PerceptionResult(
            model_name=self.name,
            backend="test",
            center_error=0.3,
            confidence=0.91,
            latency_ms=4.2,
            image_width=640,
            image_height=480,
            safe_for_control=True,
            path_blocked_probability=0.05,
            left_free_score=0.82,
            center_free_score=0.96,
            right_free_score=0.78,
            free_space_profile=[0.55, 0.68, 0.80, 0.91, 0.98, 0.90, 0.78, 0.65, 0.52],
            note="synthetic provider",
        )






class ObstaclePerceptionProvider(FakePerceptionProvider):
    name = "depth-freespace"

    def analyze(self, image_bytes: bytes) -> PerceptionResult:
        result = super().analyze(image_bytes)
        return result.model_copy(
            update={
                "model_name": self.name,
                "path_blocked_probability": 0.92,
                "left_free_score": 0.81,
                "center_free_score": 0.08,
                "right_free_score": 0.22,
                "free_space_profile": [0.95, 0.90, 0.82, 0.30, 0.08, 0.10, 0.14, 0.20, 0.25],
            }
        )


class LoadTrackingPerceptionProvider(FakePerceptionProvider):
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True


class LoadAwareEdgeClient(ApiFakeEdgeClient):
    def __init__(self, perception: LoadTrackingPerceptionProvider) -> None:
        super().__init__()
        self.perception = perception

    def arm(self) -> dict[str, object]:
        assert self.perception.loaded is True
        return super().arm()


class SlowPerceptionProvider(FakePerceptionProvider):
    def analyze(self, image_bytes: bytes) -> PerceptionResult:
        time.sleep(0.65)
        return super().analyze(image_bytes)


class AvoidThenClearPerceptionProvider(FakePerceptionProvider):
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, image_bytes: bytes) -> PerceptionResult:
        self.calls += 1
        result = super().analyze(image_bytes)
        if self.calls <= 2:
            return result.model_copy(
                update={
                    "path_blocked_probability": 0.9,
                    "left_free_score": 0.8,
                    "center_free_score": 0.1,
                    "right_free_score": 0.2,
                    "free_space_profile": [0.94, 0.90, 0.82, 0.30, 0.10, 0.12, 0.18, 0.20, 0.22],
                }
            )
        return result.model_copy(
            update={
                "path_blocked_probability": 0.1,
                "left_free_score": 0.8,
                "center_free_score": 0.9,
                "right_free_score": 0.8,
                "free_space_profile": [0.62, 0.72, 0.82, 0.92, 0.98, 0.92, 0.82, 0.72, 0.62],
            }
        )


class V2CompletingEdgeClient(ApiFakeEdgeClient):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_health_reads = 0
        self.limit_stop_at: float | None = None
        self.recovery_start_at: float | None = None

    def stop(self, sequence: int) -> dict[str, object]:
        if self.imu_phase in {"PITCH_LIMIT_REACHED", "ROLL_LIMIT_REACHED"}:
            self.limit_stop_at = time.monotonic()
        return super().stop(sequence)

    def imu_start_recovery(self) -> dict[str, object]:
        assert self.imu_phase == "PITCH_LIMIT_REACHED"
        self.recovery_start_at = time.monotonic()
        return super().imu_start_recovery()

    def imu_calibrate(self, samples: int = 100) -> dict[str, object]:
        recovery_resume = self.armed and self.imu_phase == "COMPLETED"
        response = super().imu_calibrate(samples)
        if recovery_resume:
            self.front_distance_m = 0.50
        return response

    def command(self, payload: dict[str, object]) -> dict[str, object]:
        ack = super().command(payload)
        if payload["action"] == "V2_PITCH_ADVANCE":
            self.imu_phase = "PITCH_LIMIT_REACHED"
        return ack

    def health(self):
        if self.imu_phase == "PIVOT_YAW":
            self.imu_phase = "YAW_STEP_REACHED"
        payload = super().health()
        if self.imu_phase == "PRIMARY_YAW":
            self.recovery_health_reads += 1
            if self.recovery_health_reads >= 2:

                self.imu_phase = "COMPLETED"
                payload["imu"]["phase"] = "COMPLETED"
        return payload


class YawDriftEdgeClient(ApiFakeEdgeClient):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_order: list[str] = []

    def arm(self) -> dict[str, object]:
        response = super().arm()
        self.yaw_target_deg = 15.0
        return response

    def stop(self, sequence: int) -> dict[str, object]:
        self.recovery_order.append("stop")
        return super().stop(sequence)

    def imu_start_recovery(self) -> dict[str, object]:
        self.recovery_order.append("recover")
        return super().imu_start_recovery()










def test_default_app_uses_general_depth_freespace_perception() -> None:
    app = create_app()

    assert app.state.perception_provider.name == "depth-freespace"


def test_drive_status_reports_continuous_real_motor_mode_when_bridge_is_configured() -> None:
    edge = ApiFakeEdgeClient()
    edge.motor_output = {"left": 0.65, "right": -0.65}
    edge.pitch_deg = 12.5
    edge.roll_deg = -4.0
    edge.yaw_target_deg = 27.0
    client = TestClient(
        create_app(
            command_bridge=CommandBridge(edge),
            auto_drive_loop=True,
        )
    )

    response = client.get("/api/drive")

    assert response.status_code == 200
    assert response.json()["loopMode"] == "continuous"
    assert response.json()["controlMode"] == "real-motor"
    assert response.json()["motorOutput"] == {"left": 0.65, "right": -0.65}
    assert response.json()["imu"] == {
        "pitchDeg": 12.5,
        "rollDeg": -4.0,
        "yawDeg": 27.0,
    }


def test_autonomy_loop_dispatches_commands_after_start_and_stops_after_completion() -> None:
    edge = ApiFakeEdgeClient()
    edge.front_distance_m = None
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=lambda _url: b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
    )

    with TestClient(app) as client:
        started = client.post("/api/mission/start", json={"area": "자동 주행 시험"})
        assert started.status_code == 200


        deadline = time.monotonic() + 1.0
        while not edge.commands and time.monotonic() < deadline:
            time.sleep(0.01)
        assert edge.commands
        left_speed = float(edge.commands[0]["leftSpeed"])
        right_speed = float(edge.commands[0]["rightSpeed"])
        assert left_speed <= 0.90
        assert right_speed <= 0.90
        assert edge.commands[0]["action"] == "AI_LOCAL_PLAN"
        assert left_speed > 0
        assert right_speed > 0

        completed = client.post("/api/mission/complete")
        assert completed.status_code == 200
        dispatched = len(edge.commands)
        time.sleep(0.05)
        assert len(edge.commands) == dispatched
        assert edge.armed is False


def test_v2_stops_at_five_centimeters_before_snapshot_and_depth() -> None:
    edge = ApiFakeEdgeClient()
    edge.front_distance_m = 0.05
    order: list[str] = []
    original_stop = edge.stop

    def recording_stop(sequence: int) -> dict[str, object]:
        order.append("stop")
        return original_stop(sequence)

    def recording_snapshot(_url: str) -> bytes:
        order.append("snapshot")
        return b"jpeg-frame"

    edge.stop = recording_stop  # type: ignore[method-assign]
    navigation = NavigationRouter(
        base=LocalFreeSpacePlanner(),
    )
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=recording_snapshot,
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
        navigation_router=navigation,
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/mission/start", json={"area": "5cm 선행 정지 시험"}
        ).status_code == 200
        deadline = time.monotonic() + 1.0
        while len(order) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        client.post("/api/mission/complete")

    assert order[:2] == ["stop", "snapshot"]


def test_v2_does_not_prestop_just_above_five_centimeters() -> None:
    edge = ApiFakeEdgeClient()
    edge.front_distance_m = 0.051
    order: list[str] = []
    original_stop = edge.stop

    def recording_stop(sequence: int) -> dict[str, object]:
        order.append("stop")
        return original_stop(sequence)

    def recording_snapshot(_url: str) -> bytes:
        order.append("snapshot")
        return b"jpeg-frame"

    edge.stop = recording_stop  # type: ignore[method-assign]
    navigation = NavigationRouter(
        base=LocalFreeSpacePlanner(),
    )
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=recording_snapshot,
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
        navigation_router=navigation,
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/mission/start", json={"area": "11cm 정상주행 시험"}
        ).status_code == 200
        deadline = time.monotonic() + 1.0
        while not order and time.monotonic() < deadline:
            time.sleep(0.01)
        client.post("/api/mission/complete")

    assert order[0] == "snapshot"


def test_autonomy_loop_recovers_from_one_transient_first_snapshot_failure() -> None:
    edge = ApiFakeEdgeClient()
    attempts = 0

    def flaky_first_snapshot(_url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("camera is still warming up")
        return b"jpeg-frame"

    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=flaky_first_snapshot,
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
    )

    with TestClient(app) as client:
        started = client.post("/api/mission/start", json={"area": "첫 snapshot 재시도 시험"})
        assert started.status_code == 200

        deadline = time.monotonic() + 1.0
        while not edge.commands and time.monotonic() < deadline:
            time.sleep(0.01)

        mission = client.get("/api/mission").json()
        assert attempts >= 2
        assert edge.commands
        assert mission["state"] == "RUNNING"
        assert edge.armed is True




def test_mission_loads_perception_before_edge_arm() -> None:
    perception = LoadTrackingPerceptionProvider()
    edge = LoadAwareEdgeClient(perception)
    client = TestClient(
        create_app(
            command_bridge=CommandBridge(edge),
            perception_provider=perception,
        )
    )

    response = client.post("/api/mission/start", json={"area": "모델 준비 순서 시험"})

    assert response.status_code == 200
    assert perception.loaded is True
    assert edge.armed is True




def test_heartbeat_continues_while_slow_perception_is_running() -> None:
    edge = ApiFakeEdgeClient()
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=SlowPerceptionProvider(),
        snapshot_fetcher=lambda _url: b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
    )

    with TestClient(app) as client:
        response = client.post("/api/mission/start", json={"area": "느린 추론 시험"})
        assert response.status_code == 200
        time.sleep(0.5)

        assert edge.heartbeat_count >= 2


def test_runtime_app_defaults_to_v2(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_EDGE_URL", "http://192.168.100.246:8765")
    monkeypatch.setenv("PIPECHECKKK_EDGE_TOKEN", "secret")

    runtime_app = create_runtime_app()
    response = TestClient(runtime_app).get("/api/navigation")

    assert response.status_code == 200
    assert response.json()["activeVersion"] == "v2"
    assert TestClient(runtime_app).get("/api/drive").json()["decisionModel"] == (
        "depth-anything-plus-pi-imu-v2"
    )




def test_runtime_app_refuses_to_start_without_real_motor_environment(monkeypatch) -> None:
    monkeypatch.delenv("PIPECHECKKK_EDGE_URL", raising=False)
    monkeypatch.delenv("PIPECHECKKK_EDGE_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="real-motor runtime requires"):
        create_runtime_app()


def test_runtime_app_requires_real_bridge_environment(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_EDGE_URL", "http://192.168.100.246:8765")
    monkeypatch.setenv("PIPECHECKKK_EDGE_TOKEN", "secret")

    runtime_app = create_runtime_app()

    assert runtime_app.state.command_bridge is not None
    client = TestClient(runtime_app)
    assert client.get("/health").json()["controlMode"] == "real-motor"
    assert client.get("/api/mission").json()["controlMode"] == "real-motor"




def test_real_motor_start_fails_closed_when_edge_preflight_fails() -> None:
    edge = ApiFakeEdgeClient()
    edge.health = lambda: {"ok": False, "armed": False, "emergencyStop": False}
    client = TestClient(create_app(command_bridge=CommandBridge(edge)))

    response = client.post("/api/mission/start", json={"area": "오프라인 시험"})

    assert response.status_code == 503
    mission = client.get("/api/mission").json()
    assert mission["state"] == "ERROR"
    assert mission["agentAction"] == "STOP"
    assert edge.armed is False


def test_model_preloads_without_edge_and_reports_loaded() -> None:
    perception = LoadTrackingPerceptionProvider()
    app = create_app(
        perception_provider=perception,
        command_bridge=None,
        preload_perception=True,
    )

    with TestClient(app) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    assert perception.loaded is True
    assert response.json()["perception"]["loaded"] is True


def test_progress_model_preloads_before_first_mission() -> None:
    progress = LoadTrackingProgressProvider()
    app = create_app(
        progress_provider=progress,
        preload_progress=True,
    )

    with TestClient(app):
        assert progress.loaded is True


def test_model_status_reports_downloaded_swappable_backends() -> None:
    client = TestClient(create_app())

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["perception"]["name"] == "depth-anything-v2-small"
    assert payload["perception"]["downloaded"] is model_files_present(
        "depth-anything-v2-small"
    )
    assert payload["decision"]["name"] == "qwen2.5-vl-3b-instruct-awq"
    assert payload["decision"]["downloaded"] is model_files_present(
        "qwen2.5-vl-3b-instruct-awq"
    )
    assert payload["decision"]["loaded"] is False


def test_start_and_blocked_flow_is_exposed_as_truthful_api_state() -> None:
    client = TestClient(create_app())

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "ok": True,
        "service": "pipecheckkk-ai",
        "controlMode": "simulation-no-motor",
    }

    started = client.post("/api/mission/start", json={"area": "원통형 시험 관로"})
    assert started.status_code == 200
    assert started.json()["state"] == "RUNNING"
    assert started.json()["distanceM"] is None
    assert started.json()["batteryPercent"] is None
    assert started.json()["controlMode"] == "simulation-no-motor"

    failed = client.post(
        "/api/mission/blocked",
        json={
            "reason": "좌우 회피 공간이 모두 안전 기준보다 작습니다.",
            "retryCount": 3,
        },
    )
    assert failed.status_code == 200
    assert failed.json()["state"] == "FAILED_BLOCKED"
    assert failed.json()["agentAction"] == "STOP"
    assert failed.json()["reportAvailable"] is False

    logs = client.get("/api/logs").json()
    assert [item["category"] for item in logs[-4:]] == [
        "SCENE",
        "DECISION",
        "SAFETY",
        "MISSION",
    ]


def test_telemetry_endpoint_lets_agent_choose_avoidance_then_abort_blocked_path() -> None:
    client = TestClient(create_app())
    client.post("/api/mission/start", json={"area": "원통형 시험 관로"})

    avoid = client.post(
        "/api/mission/telemetry",
        json={
            "cameraConnected": True,
            "frameAgeS": 0.1,
            "ultrasonicDistanceM": 0.16,
            "pathBlockedProbability": 0.91,
            "leftFreeScore": 0.72,
            "rightFreeScore": 0.18,
            "recoveryAttempts": 0,
        },
    )
    assert avoid.status_code == 200
    assert avoid.json()["action"] == "AVOID_LEFT"
    assert avoid.json()["missionState"] == "RUNNING"

    blocked = client.post(
        "/api/mission/telemetry",
        json={
            "cameraConnected": True,
            "frameAgeS": 0.1,
            "ultrasonicDistanceM": 0.14,
            "pathBlockedProbability": 0.96,
            "leftFreeScore": 0.12,
            "rightFreeScore": 0.19,
            "recoveryAttempts": 3,
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["action"] == "ABORT_MISSION"
    assert blocked.json()["missionState"] == "FAILED_BLOCKED"
    assert client.get("/api/mission").json()["state"] == "FAILED_BLOCKED"




def test_websocket_sends_canonical_snapshot_before_live_events() -> None:
    client = TestClient(create_app())

    with client.websocket_connect("/ws/events") as websocket:
        payload = websocket.receive_json()

    assert payload["type"] == "mission.snapshot"
    assert payload["mission"]["state"] == "IDLE"
    assert payload["mission"]["distanceM"] is None


def test_websocket_pushes_agent_events_and_terminal_state_without_reload() -> None:
    client = TestClient(create_app())


    with client.websocket_connect("/ws/events") as websocket:
        assert websocket.receive_json()["mission"]["state"] == "IDLE"
        initial_progress = websocket.receive_json()
        assert initial_progress["type"] == "progress.summary"
        assert initial_progress["summary"]["status"] == "idle"

        assert client.post("/api/mission/start", json={"area": "시험 관로"}).status_code == 200
        started_messages = [websocket.receive_json() for _ in range(4)]
        assert any(
            message.get("type") == "mission.snapshot"
            and message["mission"]["state"] == "RUNNING"
            for message in started_messages
        )

        assert client.post(
            "/api/mission/blocked",
            json={"reason": "좌우 공간 부족", "retryCount": 3},
        ).status_code == 200
        blocked_messages = [websocket.receive_json() for _ in range(6)]
        assert any(
            message.get("type") == "agent.event"
            and message["event"]["category"] == "SAFETY"
            for message in blocked_messages
        )
        assert any(
            message.get("type") == "mission.snapshot"
            and message["mission"]["state"] == "FAILED_BLOCKED"
            for message in blocked_messages
        )


def test_complete_and_reset_endpoints_drive_completion_modal_state() -> None:
    client = TestClient(create_app())
    client.post("/api/mission/start", json={"area": "원통형 시험 관로"})

    completed = client.post("/api/mission/complete")

    assert completed.status_code == 200
    assert completed.json()["state"] == "COMPLETED"
    assert completed.json()["distanceM"] is None
    assert completed.json()["batteryPercent"] is None
    assert completed.json()["reportAvailable"] is False
    assert client.post("/api/mission/start", json={"area": "다른 관로"}).status_code == 409

    reset = client.post("/api/mission/reset")
    assert reset.status_code == 200
    assert reset.json()["state"] == "IDLE"


def test_reset_clears_previous_drive_and_edge_mission_state() -> None:
    edge = ApiFakeEdgeClient()
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=lambda _url: b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
    )

    with TestClient(app) as client:
        assert client.post("/api/mission/start", json={"area": "첫 번째 점검"}).status_code == 200
        deadline = time.monotonic() + 1.0
        while not edge.commands and time.monotonic() < deadline:
            time.sleep(0.01)
        assert edge.commands

        assert client.post("/api/mission/complete").status_code == 200
        reset = client.post("/api/mission/reset")
        drive = client.get("/api/drive").json()
        bridge = app.state.command_bridge.status

        assert reset.status_code == 200
        assert reset.json()["controlMode"] == "real-motor"
        assert drive["latestObservation"] is None
        assert drive["latestPerception"] is None
        assert drive["latestPlan"] is None
        assert drive["latestCommand"] is None
        assert bridge.mission_id is None
        assert app.state.command_bridge.latest_ack is None

        previous_command_count = len(edge.commands)
        restarted = client.post("/api/mission/start", json={"area": "두 번째 점검"})

        deadline = time.monotonic() + 1.0
        while len(edge.commands) == previous_command_count and time.monotonic() < deadline:
            time.sleep(0.01)
        restarted_drive = client.get("/api/drive").json()

        assert restarted.status_code == 200
        assert len(edge.commands) > previous_command_count
        assert edge.commands[-1]["action"] != "STOP"
        assert restarted_drive["latestPlan"]["action"] != "STOP"
        assert "추가 주행을 완료" not in restarted_drive["latestPlan"]["reason"]




def test_v2_autonomy_recovers_then_resumes_normal_search_for_next_obstacle() -> None:
    edge = V2CompletingEdgeClient()
    edge.front_distance_m = 0.05
    navigation = NavigationRouter(base=LocalFreeSpacePlanner())
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=AvoidThenClearPerceptionProvider(),
        snapshot_fetcher=lambda _url: b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
        navigation_router=navigation,
    )

    with TestClient(app) as client:
        started = client.post("/api/mission/start", json={"area": "V2 IMU 복귀 시험"})
        deadline = time.monotonic() + 3.0
        mission = client.get("/api/mission").json()
        while edge.imu_calls.count("calibrate:100") < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
            mission = client.get("/api/mission").json()
        while (
            not edge.commands or edge.commands[-1]["action"] != "AI_LOCAL_PLAN"
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
            mission = client.get("/api/mission").json()

        actions = [command["action"] for command in edge.commands]
        assert started.status_code == 200
        assert edge.imu_calls[0] == "calibrate:100"
        assert "V2_PIVOT_LEFT" not in actions
        assert "V2_PITCH_ADVANCE" in actions
        assert edge.imu_calls[1:8] == [
            "yaw_step:LEFT",
            "yaw_step:LEFT",
            "yaw_step:LEFT",
            "yaw_step:LEFT",
            "pitch_guard",
            "recover",
            "calibrate:100",
        ]
        assert mission["state"] == "RUNNING"
        assert edge.armed is True
        assert edge.commands[-1]["action"] == "AI_LOCAL_PLAN"
        recovery_events = [
            event
            for event in client.get("/api/logs").json()
            if event["message"] == "V2 경로 복귀 시작: PITCH"
        ]
        assert len(recovery_events) == 1
        assert recovery_events[0]["data"]["trigger"] == "PITCH"

        completed_by_operator = client.post("/api/mission/complete")
        assert completed_by_operator.status_code == 200
        reset = client.post("/api/mission/reset")
        assert reset.status_code == 200
        assert edge.imu_calls[-1] == "reset"


def test_v2_starts_imu_recovery_without_a_dedicated_limit_stop() -> None:
    edge = V2CompletingEdgeClient()
    edge.front_distance_m = 0.05
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=AvoidThenClearPerceptionProvider(),
        snapshot_fetcher=lambda _url: b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
        navigation_router=NavigationRouter(base=LocalFreeSpacePlanner()),
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/mission/start", json={"area": "V2 recovery 정지 유지 시험"}
        ).status_code == 200
        deadline = time.monotonic() + 3.0
        while edge.recovery_start_at is None and time.monotonic() < deadline:
            time.sleep(0.01)
        client.post("/api/mission/complete")

    assert edge.limit_stop_at is None
    assert edge.recovery_start_at is not None


def test_v2_normal_search_stops_then_recovers_at_fifteen_degree_yaw_drift() -> None:
    edge = YawDriftEdgeClient()
    snapshot_calls: list[str] = []
    app = create_app(
        command_bridge=CommandBridge(edge),
        perception_provider=FakePerceptionProvider(),
        snapshot_fetcher=lambda url: snapshot_calls.append(url) or b"jpeg-frame",
        auto_drive_loop=True,
        drive_loop_interval_s=0.01,
        navigation_router=NavigationRouter(base=LocalFreeSpacePlanner()),
    )

    with TestClient(app) as client:
        assert client.post(
            "/api/mission/start", json={"area": "정상주행 yaw drift 복귀 시험"}
        ).status_code == 200
        deadline = time.monotonic() + 1.0
        while "recover" not in edge.recovery_order and time.monotonic() < deadline:
            time.sleep(0.01)
        client.post("/api/mission/complete")

    assert edge.recovery_order[:2] == ["stop", "recover"]
    assert snapshot_calls == []
