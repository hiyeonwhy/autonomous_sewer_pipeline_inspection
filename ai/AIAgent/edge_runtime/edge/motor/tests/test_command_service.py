from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from threading import Event

import pytest

from edge.imu.recovery import ImuObservation
from edge.motor.command_service import EdgeCommandService
from edge.motor.local_safety import LocalSafetySupervisor


class RecordingMotor:
    def __init__(self) -> None:
        self.applied: list[tuple[float, float]] = []
        self.stop_count = 0
        self.current_speeds = (0.0, 0.0)

    def set_speeds(self, left: float, right: float) -> None:
        self.applied.append((left, right))
        self.current_speeds = (left, right)

    def stop(self) -> None:
        self.stop_count += 1
        self.current_speeds = (0.0, 0.0)

    def close(self) -> None:
        self.stop()


class FakeImuController:
    def __init__(self) -> None:
        self.phase = "UNCALIBRATED"
        self.calls = []

    def status(self):
        return {
            "available": True,
            "calibrated": self.phase != "UNCALIBRATED",
            "phase": self.phase,
            "pitchDeg": 0.0,
            "pitchStopDeg": 30.0,
        }

    def calibrate(self, *, samples=100):
        self.calls.append(("calibrate", samples))
        self.phase = "READY"
        return self.status()

    def start_pitch_guard(self):
        self.calls.append(("pitch_guard", None))
        self.phase = "PITCH_GUARD"
        return self.status()

    def start_yaw_step(self, direction):
        self.calls.append(("yaw_step", direction))
        self.phase = "PIVOT_YAW"
        return {
            **self.status(),
            "yawTargetDeg": 10.0 if direction == "LEFT" else -10.0,
        }

    def capture_temporary_reference(self, name):
        self.calls.append(("capture_temporary_reference", name))
        return {
            **self.status(),
            "temporaryReference": {"name": name, "yawDeg": 8.8},
        }

    def start_relative_yaw_step(self, direction):
        self.calls.append(("relative_yaw_step", direction))
        self.phase = "PIVOT_YAW"
        return {
            **self.status(),
            "yawTargetDeg": 18.8 if direction == "LEFT" else -1.2,
        }

    def start_recovery(self):
        self.calls.append(("recover", None))
        self.phase = "PRIMARY_YAW"
        return self.status()

    def start_local_recovery(self, *, now, deadline_s):
        self.calls.append(("recover_path", deadline_s))
        self.phase = "PRIMARY_YAW"
        return {
            **self.status(),
            "localOperation": "RECOVER_PATH",
            "operationDeadlineS": deadline_s,
        }

    def start_yaw_scan_360(self, *, now, deadline_s):
        self.calls.append(("yaw_scan_360", deadline_s))
        self.phase = "YAW_SCAN_360"
        return {
            **self.status(),
            "localOperation": "YAW_SCAN_360",
            "operationDeadlineS": deadline_s,
        }

    def restore_reference_state(self, state, *, now, max_age_s):
        self.calls.append(("restore_reference", state["nonce"]))
        self.phase = "COMPLETED"
        return {**self.status(), "referenceRestored": True}

    def reset(self):
        self.calls.append(("reset", None))
        self.phase = "UNCALIBRATED"
        return self.status()


def signed_reference_state(token: str = "secret") -> dict[str, object]:
    state: dict[str, object] = {
        "version": 1,
        "nonce": "checkpoint-12",
        "capturedAt": "2026-07-23T00:00:00+00:00",
        "phase": "COMPLETED",
        "relativeYawDeg": 2.9,
        "relativeRollDeg": -4.9,
        "relativePitchDeg": 15.0,
    }
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    state["authTag"] = hmac.new(token.encode(), canonical, hashlib.sha256).hexdigest()
    return state


class LimitSensor:
    def __init__(self, limit_observation: ImuObservation) -> None:
        self.observations = [
            ImuObservation(0.0, 0.0, 0.0, 0.0),
            limit_observation,
        ]

    def calibrate(self, *, samples=100, sample_delay_s=0.01):
        return None

    def read(self):
        return self.observations.pop(0)

    def set_dynamic_mode(self, enabled):
        return None

    def close(self):
        return None


class BlockingLimitMotor(RecordingMotor):
    def __init__(self) -> None:
        super().__init__()
        self.block_next_stop = False
        self.stop_entered = Event()
        self.release_stop = Event()

    def stop(self) -> None:
        super().stop()
        if self.block_next_stop:
            self.block_next_stop = False
            self.stop_entered.set()
            assert self.release_stop.wait(timeout=2)


def payload(now: datetime, sequence: int = 1) -> dict[str, object]:
    return {
        "commandId": f"cmd-{sequence}",
        "missionId": "mission-1",
        "sequence": sequence,
        "action": "FOLLOW_PIPE",
        "leftSpeed": 0.12,
        "rightSpeed": 0.10,
        "issuedAt": now.isoformat(),
        "expiresAt": (now + timedelta(milliseconds=300)).isoformat(),
    }


def test_authenticated_arm_heartbeat_command_and_ack_flow() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret")

    assert service.arm("wrong")[0] == 401
    assert service.arm("secret") == (200, {"armed": True, "reason": "ARMED"})
    assert service.heartbeat("secret", now=now)[0] == 200

    status, ack = service.command("secret", payload(now), now=now)

    assert status == 200
    assert ack["accepted"] is True
    assert ack["commandId"] == "cmd-1"
    assert ack["sequence"] == 1
    assert ack["appliedLeftSpeed"] == 0.12
    assert motor.applied == [(0.12, 0.10)]


def test_malformed_command_and_bad_token_fail_closed() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret")
    service.arm("secret")
    service.heartbeat("secret", now=now)

    unauthorized = service.command("wrong", payload(now), now=now)
    malformed = service.command("secret", {"action": "FOLLOW_PIPE"}, now=now)

    assert unauthorized[0] == 401
    assert malformed[0] == 400
    assert malformed[1]["accepted"] is False
    assert malformed[1]["reason"] == "MALFORMED_COMMAND"
    assert motor.stop_count >= 3


def test_disarm_and_emergency_stop_are_visible_in_health() -> None:
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(safety, token="secret")

    service.arm("secret")
    motor.set_speeds(0.65, -0.65)
    moving_health = service.health()
    assert service.disarm("secret") == (200, {"armed": False, "reason": "DISARMED"})
    service.set_emergency_stop(True)
    health = service.health()

    assert health["ok"] is True
    assert health["armed"] is False
    assert health["emergencyStop"] is True
    assert health["controlMode"] == "real-motor"
    assert health["maxSpeed"] == 0.90
    assert health["frontDistanceM"] is None
    assert health["ultrasonicStopM"] == 0.10
    assert moving_health["motorOutput"] == {"left": 0.65, "right": -0.65}
    assert health["motorOutput"] == {"left": 0.0, "right": 0.0}


def test_health_records_heartbeat_gap_and_timeout_without_disk_io() -> None:
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=1000)
    service = EdgeCommandService(safety, token="secret")

    safety.arm(now=start)
    safety.heartbeat(now=start + timedelta(milliseconds=150))
    safety.heartbeat(now=start + timedelta(milliseconds=310))
    assert safety.watchdog_tick(now=start + timedelta(milliseconds=1311)) == "HEARTBEAT_TIMEOUT"

    telemetry = service.health()["heartbeat"]
    assert telemetry["count"] == 2
    assert telemetry["lastGapMs"] == 160.0
    assert telemetry["maxGapMs"] == 160.0
    assert telemetry["timeoutGapMs"] == 1001.0
    assert telemetry["lastReceivedAt"] == "2026-08-10T00:00:00.310000+00:00"


def test_imu_control_endpoints_are_authenticated_and_visible_in_health() -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret", imu=imu)

    assert service.imu_calibrate("wrong", {"samples": 100})[0] == 401
    status, calibrated = service.imu_calibrate("secret", {"samples": 100})
    assert status == 200
    assert calibrated["phase"] == "READY"
    assert service.health()["imu"]["pitchStopDeg"] == 30.0

    service.arm("secret")
    assert service.imu_start_yaw_step("wrong", {"direction": "LEFT"})[0] == 401
    status, yaw_step = service.imu_start_yaw_step("secret", {"direction": "LEFT"})
    assert status == 200
    assert yaw_step["phase"] == "PIVOT_YAW"
    assert imu.calls[-1] == ("yaw_step", "LEFT")
    imu.phase = "READY"
    status, guarded = service.imu_start_pitch_guard("secret")
    assert status == 200
    assert guarded["phase"] == "PITCH_GUARD"
    status, recovering = service.imu_start_recovery("secret")
    assert status == 200
    assert recovering["phase"] == "PRIMARY_YAW"
    assert service.imu_calibrate("secret", {"samples": 100})[0] == 409

    imu.phase = "COMPLETED"
    status, resumed = service.imu_calibrate("secret", {"samples": 100})
    assert status == 200
    assert resumed["phase"] == "READY"
    assert service.health()["armed"] is True
    assert service.imu_reset("secret")[0] == 409
    service.disarm("secret")
    assert service.imu_reset("secret")[1]["phase"] == "UNCALIBRATED"


def test_temporary_reference_and_relative_yaw_step_are_authenticated() -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret", imu=imu)

    assert service.imu_capture_temporary_reference("wrong", {"name": "AVOIDANCE"})[0] == 401
    status, captured = service.imu_capture_temporary_reference(
        "secret", {"name": "AVOIDANCE"}
    )
    assert status == 200
    assert captured["temporaryReference"]["name"] == "AVOIDANCE"

    service.arm("secret")
    assert service.imu_start_relative_yaw_step("wrong", {"direction": "LEFT"})[0] == 401
    status, started = service.imu_start_relative_yaw_step(
        "secret", {"direction": "LEFT"}
    )
    assert status == 200
    assert started["yawTargetDeg"] == pytest.approx(18.8)
    assert imu.calls[-1] == ("relative_yaw_step", "LEFT")


@pytest.mark.parametrize(
    "endpoint",
    [
        "yaw-step",
        "yaw-step-current",
        "yaw-step-relative",
        "pitch-guard",
        "recovery",
        "temp-clear",
        "temp-restore",
        "forward-timed",
        "forward-distance",
        "recover-path",
        "yaw-scan",
        "malformed-command",
        "unauthorized-command",
    ],
)
def test_rejected_non_stop_endpoint_preserves_active_local_operation(endpoint) -> None:
    class ActiveOperationImu(FakeImuController):
        def start_current_yaw_step(self, direction, *, step_degrees):
            self.calls.append(("current_yaw_step", direction, step_degrees))
            self.phase = "PIVOT_YAW"
            return {**self.status(), "localOperation": "YAW_STEP"}

        def clear_temporary_reference(self):
            self.calls.append(("clear_temporary_reference", None))
            return self.status()

        def restore_temporary_reference(self, name, **_pose):
            self.calls.append(("restore_temporary_reference", name))
            return self.status()

    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    imu = ActiveOperationImu()
    imu.phase = "READY"
    service = EdgeCommandService(safety, token="secret", imu=imu)
    safety.begin_local_operation("YAW_STEP", now=now, deadline_s=30.0)
    motor.set_speeds(-0.65, 0.65)
    before = (
        safety.armed,
        safety.local_operation,
        safety.local_operation_deadline,
        motor.current_speeds,
        motor.stop_count,
        tuple(imu.calls),
        imu.phase,
    )

    if endpoint == "yaw-step":
        result = service.imu_start_yaw_step("secret", {"direction": "LEFT"})
    elif endpoint == "yaw-step-current":
        result = service.imu_start_current_yaw_step(
            "secret", {"direction": "LEFT", "degrees": 10.0}
        )
    elif endpoint == "yaw-step-relative":
        result = service.imu_start_relative_yaw_step("secret", {"direction": "LEFT"})
    elif endpoint == "pitch-guard":
        result = service.imu_start_pitch_guard("secret")
    elif endpoint == "recovery":
        result = service.imu_start_recovery("secret")
    elif endpoint == "temp-clear":
        result = service.imu_clear_temporary_reference("secret")
    elif endpoint == "temp-restore":
        result = service.imu_restore_temporary_reference(
            "secret",
            {"name": "AVOIDANCE", "yawDeg": 1.0, "rollDeg": 2.0, "pitchDeg": 3.0},
        )
    elif endpoint == "forward-timed":
        result = service.forward_timed_continuous(
            "secret", {"speed": 0.4, "durationSeconds": 10.0}, now=now
        )
    elif endpoint == "forward-distance":
        result = service.forward_until_distance(
            "secret",
            {"speed": 0.4, "targetDistanceM": 0.2, "deadlineSeconds": 30.0},
            now=now,
        )
    elif endpoint == "recover-path":
        result = service.recover_path(
            "secret", {"deadlineSeconds": 30.0}, now=now
        )
    elif endpoint == "yaw-scan":
        result = service.yaw_scan_360(
            "secret", {"deadlineSeconds": 30.0}, now=now
        )
    elif endpoint == "malformed-command":
        result = service.command("secret", {"action": "FOLLOW_PIPE"}, now=now)
    else:
        result = service.command("wrong", payload(now), now=now)

    assert result[0] in {400, 401, 409}
    assert (
        safety.armed,
        safety.local_operation,
        safety.local_operation_deadline,
        motor.current_speeds,
        motor.stop_count,
        tuple(imu.calls),
        imu.phase,
    ) == before


def test_temporary_reference_capture_rejection_does_not_stop_between_retries() -> None:
    class RejectingTemporaryReferenceImu(FakeImuController):
        def capture_temporary_reference(self, name):
            del name
            raise RuntimeError("temporary IMU reference values are unavailable")

    motor = RecordingMotor()
    service = EdgeCommandService(
        LocalSafetySupervisor(motor),
        token="secret",
        imu=RejectingTemporaryReferenceImu(),
    )
    stop_count_before_capture = motor.stop_count

    status, response = service.imu_capture_temporary_reference(
        "secret", {"name": "AVOIDANCE"}
    )

    assert status == 409
    assert response["reason"] == "IMU_TEMP_REFERENCE_CAPTURE_REJECTED"
    assert motor.stop_count == stop_count_before_capture


def test_recover_path_is_one_authenticated_atomic_start_without_prior_arm() -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    imu.phase = "READY"
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(safety, token="secret", imu=imu)

    assert service.recover_path("wrong", {"deadlineSeconds": 45.0})[0] == 401
    status, response = service.recover_path("secret", {"deadlineSeconds": 45.0})

    assert status == 202
    assert response["phase"] == "PRIMARY_YAW"
    assert response["localOperation"] == "RECOVER_PATH"
    assert imu.calls == [("recover_path", 45.0)]


@pytest.mark.parametrize("deadline", [0, -1, 60.001, float("nan"), "60"])
def test_recover_path_rejects_invalid_deadline_fail_closed(deadline) -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    imu.phase = "READY"
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(safety, token="secret", imu=imu)

    status, response = service.recover_path(
        "secret", {"deadlineSeconds": deadline}
    )

    assert status == 400
    assert response["reason"] == "INVALID_RECOVER_PATH_DEADLINE"
    assert safety.armed is False
    assert imu.calls == []


def test_late_v2_advance_after_pitch_limit_is_absorbed_as_stop() -> None:
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    motor = RecordingMotor()
    imu = FakeImuController()
    imu.phase = "PITCH_LIMIT_REACHED"
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret", imu=imu)
    service.arm("secret")
    service.heartbeat("secret", now=now)
    body = payload(now)
    body["action"] = "V2_PITCH_ADVANCE"
    body["leftSpeed"] = 0.50
    body["rightSpeed"] = 0.50

    status, ack = service.command("secret", body, now=now)

    assert status == 200
    assert ack["accepted"] is True
    assert ack["appliedLeftSpeed"] == 0.0
    assert ack["appliedRightSpeed"] == 0.0
    assert motor.applied == []


def test_yaw_scan_360_is_authenticated_bounded_and_admitted_from_completed() -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    imu.phase = "COMPLETED"
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(safety, token="secret", imu=imu)

    assert service.yaw_scan_360("wrong", {"deadlineSeconds": 60.0})[0] == 401
    status, response = service.yaw_scan_360("secret", {"deadlineSeconds": 60.0})

    assert status == 202
    assert response["phase"] == "YAW_SCAN_360"
    assert response["localOperation"] == "YAW_SCAN_360"
    assert imu.calls == [("yaw_scan_360", 60.0)]


@pytest.mark.parametrize("deadline", [0, -1, 60.001, float("nan"), "60"])
def test_yaw_scan_360_rejects_invalid_deadline_fail_closed(deadline) -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    imu.phase = "COMPLETED"
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(safety, token="secret", imu=imu)

    status, response = service.yaw_scan_360("secret", {"deadlineSeconds": deadline})

    assert status == 400
    assert response["reason"] == "INVALID_YAW_SCAN_360_DEADLINE"
    assert safety.armed is False
    assert imu.calls == []


def test_reference_restore_is_authenticated_and_only_allowed_disarmed_uncalibrated(tmp_path) -> None:
    motor = RecordingMotor()
    imu = FakeImuController()
    safety = LocalSafetySupervisor(motor)
    service = EdgeCommandService(
        safety,
        token="secret",
        imu=imu,
        reference_nonce_store=tmp_path / "reference-nonces.json",
    )
    state = signed_reference_state()

    assert service.imu_restore_reference("wrong", state)[0] == 401
    status, response = service.imu_restore_reference("secret", state)

    assert status == 200
    assert response["phase"] == "COMPLETED"
    assert response["referenceRestored"] is True
    assert imu.calls == [("restore_reference", "checkpoint-12")]

    safety.arm()
    assert service.imu_restore_reference("secret", state)[0] == 409


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("version", 2),
        ("nonce", "checkpoint-13"),
        ("capturedAt", "2026-07-23T00:00:01+00:00"),
        ("phase", "READY"),
        ("relativeYawDeg", 3.0),
        ("relativeRollDeg", -5.0),
        ("relativePitchDeg", 15.1),
        ("authTag", "0" * 64),
    ],
)
def test_reference_restore_rejects_each_tampered_transfer_field(
    tmp_path, field, tampered
) -> None:
    imu = FakeImuController()
    service = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=imu,
        reference_nonce_store=tmp_path / "reference-nonces.json",
    )
    state = signed_reference_state()
    state[field] = tampered

    status, response = service.imu_restore_reference("secret", state)

    assert status == 400
    assert response["reason"] == "INVALID_REFERENCE_AUTH"
    assert imu.calls == []


def test_reference_restore_rejects_missing_auth_tag(tmp_path) -> None:
    imu = FakeImuController()
    service = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=imu,
        reference_nonce_store=tmp_path / "reference-nonces.json",
    )
    state = signed_reference_state()
    del state["authTag"]

    status, response = service.imu_restore_reference("secret", state)

    assert status == 400
    assert response["reason"] == "INVALID_REFERENCE_AUTH"
    assert imu.calls == []


def test_reference_nonce_is_consumed_across_service_restarts(tmp_path) -> None:
    store = tmp_path / "reference-nonces.json"
    first_imu = FakeImuController()
    first = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=first_imu,
        reference_nonce_store=store,
    )
    state = signed_reference_state()

    assert first.imu_restore_reference("secret", state)[0] == 200
    assert store.stat().st_mode & 0o777 == 0o600

    second_imu = FakeImuController()
    second = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=second_imu,
        reference_nonce_store=store,
    )
    status, response = second.imu_restore_reference("secret", state)

    assert status == 409
    assert response["reason"] == "REFERENCE_NONCE_REPLAYED"
    assert second_imu.calls == []


def test_reference_restore_fails_closed_when_nonce_cannot_be_persisted(tmp_path) -> None:
    store = tmp_path / "store-is-a-directory"
    store.mkdir()
    imu = FakeImuController()
    service = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=imu,
        reference_nonce_store=store,
    )

    status, response = service.imu_restore_reference("secret", signed_reference_state())

    assert status == 409
    assert response["reason"] == "REFERENCE_NONCE_PERSISTENCE_FAILED"
    assert imu.calls == []
