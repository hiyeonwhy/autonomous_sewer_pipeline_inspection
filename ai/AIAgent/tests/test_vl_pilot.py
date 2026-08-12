from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "vl_pilot.py"
SPEC = importlib.util.spec_from_file_location("vl_pilot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
vl_pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vl_pilot)


def test_forward_preserves_a_requested_4000ms_bounded_burst(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.payload = None

        def health(self) -> dict[str, object]:
            return {
                "ok": True,
                "armed": True,
                "emergencyStop": False,
                "frontDistanceM": 0.97,
                "lastSequence": -1,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "READY",
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 0.0,
                    "rollDeg": 0.0,
                    "yawDeg": 0.0,
                },
            }

        def command(self, payload: dict[str, object]) -> dict[str, object]:
            self.payload = payload
            return {"accepted": True, "reason": "COMMAND_APPLIED"}

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    result = vl_pilot.do_fwd(edge, SimpleNamespace(speed=0.40, ms=4000))

    assert result == 0
    assert edge.payload is not None
    issued = datetime.fromisoformat(str(edge.payload["issuedAt"]))
    expires = datetime.fromisoformat(str(edge.payload["expiresAt"]))
    assert (expires - issued).total_seconds() == 4.0


def test_forward_until_distance_uses_bounded_bursts_and_stops_at_target(monkeypatch) -> None:
    class FakeEdge:
        distances = iter((0.50, 0.40, 0.29))

        def health(self):
            return {
                "ok": True,
                "armed": False,
                "emergencyStop": False,
                "frontDistanceM": next(self.distances),
                "imu": {"valid": True},
            }

    bursts = []
    monkeypatch.setattr(vl_pilot, "do_resume", lambda _edge, _args: 0)
    monkeypatch.setattr(
        vl_pilot,
        "do_fwd",
        lambda _edge, args: bursts.append(args.ms) or 0,
    )

    result = vl_pilot.do_fwd_until_distance(
        FakeEdge(),
        SimpleNamespace(speed=0.40, distance=0.30, timeout=8.0),
    )

    assert result == 0
    assert bursts == [500, 200]


def test_forward_until_distance_uses_configured_fixed_burst(monkeypatch) -> None:
    class FakeEdge:
        distances = iter((0.80, 0.60, 0.29))

        def health(self):
            return {
                "ok": True,
                "armed": False,
                "emergencyStop": False,
                "frontDistanceM": next(self.distances),
                "imu": {"valid": True},
            }

    bursts = []
    monkeypatch.setattr(vl_pilot, "do_resume", lambda _edge, _args: 0)
    monkeypatch.setattr(
        vl_pilot,
        "do_fwd",
        lambda _edge, args: bursts.append(args.ms) or 0,
    )

    result = vl_pilot.do_fwd_until_distance(
        FakeEdge(),
        SimpleNamespace(
            speed=0.40,
            distance=0.30,
            timeout=30.0,
            burst_ms=3000,
        ),
    )

    assert result == 0
    assert bursts == [3000, 3000]


@pytest.mark.parametrize("turn_mode", ["turn", "current", "relative"])
def test_pi_owned_turn_observes_past_old_fifteen_second_client_timeout_without_stopping(
    monkeypatch, turn_mode
) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.stop_count = 0
            self.disarm_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            terminal = self.health_count > 1
            return {
                "ok": True,
                "armed": not terminal,
                "emergencyStop": False,
                "lastSequence": 4,
                "localOperation": None if terminal else "YAW_STEP",
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED" if terminal else "READY",
                    "valid": True,
                    "ageS": 0.01,
                    "localOperation": None if terminal else "YAW_STEP",
                },
            }

        def _started(self) -> dict[str, object]:
            return {
                "phase": "PIVOT_YAW",
                "yawTargetDeg": 10.0,
                "localOperation": "YAW_STEP",
            }

        def imu_yaw_step(self, _direction):
            assert turn_mode == "turn"
            return self._started()

        def imu_yaw_step_current(self, _direction, _degrees):
            assert turn_mode == "current"
            return self._started()

        def imu_yaw_step_relative(self, _direction):
            assert turn_mode == "relative"
            return self._started()

        def stop(self, _sequence):
            self.stop_count += 1

        def disarm(self):
            self.disarm_count += 1

    monotonic_values = iter((0.0, 16.0))
    monkeypatch.setattr(vl_pilot.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)
    edge = FakeEdge()
    args = SimpleNamespace(
        direction="LEFT",
        timeout=15.0,
        current=turn_mode == "current",
        relative=turn_mode == "relative",
        degrees=10.0,
    )

    assert vl_pilot.do_turn(edge, args) == 0
    assert edge.stop_count == 0
    assert edge.disarm_count == 0


def test_turn_uses_pi_owned_operation_without_laptop_heartbeat() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.heartbeat_count = 0
            self.health_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            preflight = self.health_count == 1
            terminal = self.health_count >= 4
            phase = "READY" if preflight else (
                "YAW_STEP_REACHED" if terminal else "PIVOT_YAW"
            )
            return {
                "ok": True,
                "armed": False if terminal else True,
                "emergencyStop": False,
                "frontDistanceM": 0.84,
                "lastSequence": 0,
                "localOperation": None if preflight or terminal else "YAW_STEP",
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": phase,
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 0.0,
                    "rollDeg": 0.0,
                    "yawDeg": 10.0 if terminal else 4.0,
                    "yawTargetDeg": 10.0,
                    "localOperation": None if terminal else "YAW_STEP",
                },
            }

        def imu_yaw_step(self, direction: str) -> dict[str, object]:
            assert direction == "LEFT"
            return {
                "phase": "PIVOT_YAW",
                "yawTargetDeg": 10.0,
                "localOperation": "YAW_STEP",
            }

        def heartbeat(self) -> dict[str, object]:
            self.heartbeat_count += 1
            raise AssertionError("Pi-owned yaw step must not send laptop heartbeat")

        def stop(self, sequence: int) -> dict[str, object]:
            raise AssertionError(f"successful turn must not timeout-stop at sequence {sequence}")

    edge = FakeEdge()

    result = vl_pilot.do_turn(edge, SimpleNamespace(direction="LEFT", timeout=2.0))

    assert result == 0
    assert edge.heartbeat_count == 0


def test_heartbeat_keeper_flushes_bounded_timing_trace_without_per_tick_disk_writes(
    tmp_path,
) -> None:
    class FakeEdge:
        timeout = 0.1

        def heartbeat(self) -> dict[str, object]:
            return {"ok": True, "armed": True}

    trace_path = tmp_path / "heartbeat-traces.jsonl"
    keeper = vl_pilot._HeartbeatKeeper(
        FakeEdge(), interval_s=0.001, trace_path=trace_path
    )

    keeper.start()
    for _ in range(100):
        if keeper.sample_count >= 3:
            break
        threading.Event().wait(0.001)

    assert not trace_path.exists()
    keeper.close()
    records = [vl_pilot.json.loads(line) for line in trace_path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["sampleCount"] >= 3
    assert records[0]["samples"][-1]["armed"] is True
    assert len(records[0]["samples"]) <= 128


def test_temp_reference_capture_and_relative_turn_use_saved_snapshot() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.captured = None
            self.relative_direction = None
            self.health_count = 0

        def imu_capture_temporary_reference(self, name):
            self.captured = name
            return {
                "temporaryReference": {"name": name, "yawDeg": 8.8},
                "armed": False,
            }

        def imu_yaw_step_relative(self, direction):
            self.relative_direction = direction
            return {
                "phase": "PIVOT_YAW",
                "yawTargetDeg": 18.8,
                "localOperation": "YAW_STEP",
            }

        def heartbeat(self):
            raise AssertionError("Pi-owned yaw step must not send laptop heartbeat")

        def health(self):
            self.health_count += 1
            return {
                "ok": True,
                "armed": self.health_count == 1,
                "emergencyStop": False,
                "lastSequence": 0,
                "localOperation": None,
                "motorOutput": {"left": 0.0, "right": 0.0},
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "valid": True,
                    "ageS": 0.01,
                    "phase": "YAW_STEP_REACHED",
                    "reason": "V2_YAW_STEP_REACHED",
                    "yawDeg": 18.8,
                    "yawTargetDeg": 18.8,
                    "localOperation": None,
                    "temporaryReference": {"name": "AVOIDANCE", "yawDeg": 8.8},
                },
            }

    edge = FakeEdge()
    assert vl_pilot.do_temp_ref_capture(edge, SimpleNamespace(name="AVOIDANCE")) == 0
    assert vl_pilot.do_turn(
        edge, SimpleNamespace(direction="LEFT", timeout=2.0, relative=True)
    ) == 0
    assert edge.captured == "AVOIDANCE"
    assert edge.relative_direction == "LEFT"


def test_temp_reference_capture_retries_until_saved_snapshot_is_confirmed(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.capture_count = 0

        def imu_capture_temporary_reference(self, name):
            self.capture_count += 1
            if self.capture_count < 5:
                return {"ok": False, "reason": "TEMP_REFERENCE_NOT_CONFIRMED"}
            return {"temporaryReference": {"name": name, "yawDeg": 8.8}}

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    assert vl_pilot.do_temp_ref_capture(edge, SimpleNamespace(name="AVOIDANCE")) == 0
    assert edge.capture_count == 5


def test_temp_reference_capture_fails_closed_after_five_unconfirmed_attempts(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.capture_count = 0

        def imu_capture_temporary_reference(self, _name):
            self.capture_count += 1
            return {
                "ok": False,
                "reason": "IMU_TEMP_REFERENCE_CAPTURE_REJECTED",
                "detail": "temporary IMU reference values are unavailable",
            }

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    with pytest.raises(vl_pilot.PilotError, match="IMU_TEMP_REFERENCE_CAPTURE_REJECTED"):
        vl_pilot.do_temp_ref_capture(edge, SimpleNamespace(name="AVOIDANCE"))
    assert edge.capture_count == 5


def test_turn_accepts_expected_disarm_when_yaw_target_was_reached() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            completed = self.health_count > 1
            return {
                "ok": True,
                "armed": not completed,
                "emergencyStop": False,
                "frontDistanceM": 1.47,
                "lastSequence": 2,
                "localOperation": None,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED" if completed else "READY",
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 1.3,
                    "rollDeg": -0.6,
                    "yawDeg": 2.9,
                    "yawTargetDeg": 0.0,
                    "localOperation": None,
                },
            }

        def imu_yaw_step(self, _direction: str) -> dict[str, object]:
            return {
                "phase": "PIVOT_YAW",
                "yawTargetDeg": 0.0,
                "localOperation": "YAW_STEP",
            }

        def heartbeat(self) -> dict[str, object]:
            raise AssertionError("Pi-owned yaw step must not send laptop heartbeat")

        def stop(self, sequence: int) -> dict[str, object]:
            raise AssertionError(f"completed yaw step must not be failed at sequence {sequence}")

        def disarm(self) -> dict[str, object]:
            raise AssertionError("completed yaw step is already safely disarmed")

    assert vl_pilot.do_turn(
        FakeEdge(), SimpleNamespace(direction="RIGHT", timeout=2.0)
    ) == 0


def test_turn_slow_health_poll_never_starts_laptop_heartbeat() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.heartbeat_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            terminal = self.health_count > 1
            return {
                "ok": True,
                "armed": not terminal,
                "emergencyStop": False,
                "lastSequence": 0,
                "localOperation": None,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED" if terminal else "READY",
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 0.0,
                    "rollDeg": 0.0,
                    "localOperation": None,
                },
            }

        def imu_yaw_step(self, _direction: str) -> dict[str, object]:
            return {
                "phase": "PIVOT_YAW",
                "yawTargetDeg": 10.0,
                "localOperation": "YAW_STEP",
            }

        def heartbeat(self) -> dict[str, object]:
            self.heartbeat_count += 1
            raise AssertionError("Pi-owned yaw step must not send laptop heartbeat")

        def stop(self, _sequence: int) -> dict[str, object]:
            return {"accepted": True}

    edge = FakeEdge()

    result = vl_pilot.do_turn(edge, SimpleNamespace(direction="LEFT", timeout=2.0))

    assert result == 0
    assert edge.heartbeat_count == 0


def test_resume_arms_without_recalibrating_existing_mission_reference() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.calibrate_count = 0
            self.arm_count = 0

        def health(self) -> dict[str, object]:
            return {
                "ok": True,
                "armed": self.arm_count > 0,
                "emergencyStop": False,
                "frontDistanceM": 0.80,
                "lastSequence": 0,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "COMPLETED",
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 2.0,
                    "rollDeg": 1.0,
                    "yawDeg": -26.0,
                },
            }

        def arm(self) -> dict[str, object]:
            self.arm_count += 1
            return {"armed": True}

        def imu_calibrate(self, _samples: int) -> dict[str, object]:
            self.calibrate_count += 1
            raise AssertionError("resume must preserve the inspection-start IMU reference")

    edge = FakeEdge()

    result = vl_pilot.do_resume(edge, SimpleNamespace())

    assert result == 0
    assert edge.arm_count == 1
    assert edge.calibrate_count == 0


def test_resume_waits_past_five_invalid_samples_until_imu_is_valid(
    monkeypatch,
) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.arm_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            return {
                "ok": True,
                "armed": self.arm_count > 0,
                "emergencyStop": False,
                "frontDistanceM": 0.8,
                "lastSequence": 1,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED",
                    "valid": self.health_count >= 7,
                    "ageS": 0.01,
                    "yawDeg": 10.0,
                    "rollDeg": 0.0,
                    "pitchDeg": 0.0,
                    "yawTargetDeg": 10.0,
                },
            }

        def arm(self) -> dict[str, object]:
            self.arm_count += 1
            return {"armed": True}

    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)
    edge = FakeEdge()

    result = vl_pilot.do_resume(edge, SimpleNamespace())

    assert result == 0
    assert edge.health_count >= 7
    assert edge.arm_count == 1


def test_forward_waits_for_valid_imu_before_sending_one_motion_command(
    monkeypatch,
) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.heartbeat_count = 0
            self.commands: list[dict[str, object]] = []

        def health(self) -> dict[str, object]:
            self.health_count += 1
            return {
                "ok": True,
                "armed": True,
                "emergencyStop": False,
                "frontDistanceM": 0.8,
                "lastSequence": 4,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED",
                    "valid": self.health_count >= 7,
                    "ageS": 0.01,
                    "yawDeg": 10.0,
                    "rollDeg": 0.0,
                    "pitchDeg": 0.0,
                    "yawTargetDeg": 10.0,
                },
            }

        def heartbeat(self) -> dict[str, object]:
            self.heartbeat_count += 1
            return {"armed": True}

        def command(self, payload: dict[str, object]) -> dict[str, object]:
            self.commands.append(payload)
            return {"accepted": True, "reason": "COMMAND_APPLIED"}

    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)
    edge = FakeEdge()

    result = vl_pilot.do_fwd(edge, SimpleNamespace(speed=0.40, ms=1500))

    assert result == 0
    assert edge.health_count >= 7
    assert edge.heartbeat_count == 0
    assert len(edge.commands) == 1


def test_resume_does_not_send_laptop_heartbeat_when_edge_is_already_armed() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.heartbeat_count = 0

        def health(self) -> dict[str, object]:
            return {
                "ok": True,
                "armed": True,
                "emergencyStop": False,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "YAW_STEP_REACHED",
                    "valid": True,
                    "ageS": 0.01,
                },
            }

        def heartbeat(self) -> dict[str, object]:
            self.heartbeat_count += 1
            return {"armed": True}

    edge = FakeEdge()

    assert vl_pilot.do_resume(edge, SimpleNamespace()) == 0
    assert edge.heartbeat_count == 0


def test_recover_does_not_automatically_rearm_watchdog_error() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.arm_count = 0

        def health(self) -> dict[str, object]:
            return {
                "ok": True,
                "armed": False,
                "emergencyStop": False,
                "lastSequence": 7,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "ERROR",
                    "reason": "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED",
                    "valid": True,
                    "ageS": 0.01,
                    "pitchDeg": 14.0,
                    "rollDeg": -21.0,
                },
            }

        def arm(self) -> dict[str, object]:
            self.arm_count += 1
            return {"armed": True}

    edge = FakeEdge()

    try:
        vl_pilot.do_recover(edge, SimpleNamespace(timeout=2.0))
    except vl_pilot.PilotError as error:
        assert "manual" in str(error)
    else:
        raise AssertionError("watchdog recovery must require a manual re-arm")

    assert edge.arm_count == 0


def test_recovery_timeout_explicitly_stops_and_disarms() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.stopped = []
            self.disarm_count = 0

        def health(self) -> dict[str, object]:
            return {"armed": True, "lastSequence": 4, "imu": {"phase": "READY"}}

        def heartbeat(self) -> dict[str, object]:
            return {"armed": True}

        def imu_recover(self) -> dict[str, object]:
            return {"phase": "PRIMARY_YAW"}

        def stop(self, sequence: int) -> dict[str, object]:
            self.stopped.append(sequence)
            return {"accepted": True}

        def disarm(self) -> dict[str, object]:
            self.disarm_count += 1
            return {"armed": False}

    edge = FakeEdge()

    with pytest.raises(vl_pilot.PilotError, match="did not complete"):
        vl_pilot.do_recover(edge, SimpleNamespace(timeout=0.0))

    assert edge.stopped == [5]
    assert edge.disarm_count == 1


def test_recovery_poll_exception_explicitly_stops_and_disarms(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.stopped = []
            self.disarm_count = 0

        def health(self) -> dict[str, object]:
            self.health_count += 1
            if self.health_count > 1:
                raise vl_pilot.PilotError("health poll failed")
            return {"armed": True, "lastSequence": 9, "imu": {"phase": "READY"}}

        def heartbeat(self) -> dict[str, object]:
            return {"armed": True}

        def imu_recover(self) -> dict[str, object]:
            return {"phase": "PRIMARY_YAW"}

        def stop(self, sequence: int) -> dict[str, object]:
            self.stopped.append(sequence)
            return {"accepted": True}

        def disarm(self) -> dict[str, object]:
            self.disarm_count += 1
            return {"armed": False}

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    with pytest.raises(vl_pilot.PilotError, match="health poll failed"):
        vl_pilot.do_recover(edge, SimpleNamespace(timeout=1.0))

    assert edge.stopped == [10]
    assert edge.disarm_count == 1


def test_recovery_heartbeat_cadence_is_independent_of_slow_health_poll() -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.health_count = 0
            self.heartbeat_count = 0
            self.three_heartbeats = threading.Event()

        def health(self) -> dict[str, object]:
            self.health_count += 1
            phase = "READY"
            if self.health_count > 1:
                self.three_heartbeats.wait(timeout=0.6)
                phase = "COMPLETED"
            return {
                "armed": True,
                "lastSequence": 2,
                "imu": {"phase": phase},
            }

        def heartbeat(self) -> dict[str, object]:
            self.heartbeat_count += 1
            if self.heartbeat_count >= 3:
                self.three_heartbeats.set()
            return {"armed": True}

        def imu_recover(self) -> dict[str, object]:
            return {"phase": "PRIMARY_YAW"}

        def stop(self, _sequence: int) -> dict[str, object]:
            return {"accepted": True}

        def disarm(self) -> dict[str, object]:
            return {"armed": False}

    edge = FakeEdge()

    result = vl_pilot.do_recover(edge, SimpleNamespace(timeout=2.0))

    assert result == 0
    assert edge.three_heartbeats.is_set()


def test_recover_path_sends_one_start_then_only_polls_without_heartbeat(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.start_count = 0
            self.health_count = 0
            self.heartbeat_count = 0
            self.stop_count = 0
            self.disarm_count = 0

        def recover_path(self, deadline_s: float) -> dict[str, object]:
            self.start_count += 1
            assert deadline_s == 45.0
            return {"phase": "PRIMARY_YAW", "localOperation": "RECOVER_PATH"}

        def health(self) -> dict[str, object]:
            self.health_count += 1
            phase = "ROLL_RECOVERY" if self.health_count == 1 else "COMPLETED"
            return {
                "armed": phase != "COMPLETED",
                "imu": {"phase": phase, "reason": "V2_POSE_RECOVERED"},
            }

        def heartbeat(self):
            self.heartbeat_count += 1
            raise AssertionError("recover-path must never heartbeat")

        def stop(self, _sequence):
            self.stop_count += 1

        def disarm(self):
            self.disarm_count += 1

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    result = vl_pilot.do_recover_path(edge, SimpleNamespace(timeout=45.0))

    assert result == 0
    assert edge.start_count == 1
    assert edge.health_count == 2
    assert edge.heartbeat_count == 0
    assert edge.stop_count == 0
    assert edge.disarm_count == 0


def test_recover_path_client_poll_failure_does_not_drive_or_retry(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self) -> None:
            self.start_count = 0
            self.health_count = 0

        def recover_path(self, _deadline_s):
            self.start_count += 1
            return {"phase": "PRIMARY_YAW", "localOperation": "RECOVER_PATH"}

        def health(self):
            self.health_count += 1
            if self.health_count == 1:
                return {
                    "ok": True,
                    "armed": True,
                    "imu": {"phase": "COMPLETED", "valid": True, "ageS": 0.01},
                }
            raise vl_pilot.PilotError("poll disconnected")

        def heartbeat(self):
            raise AssertionError("must not heartbeat")

        def stop(self, _sequence):
            raise AssertionError("Pi-local operation owns fail-closed stop")

        def disarm(self):
            raise AssertionError("Pi-local operation owns fail-closed disarm")

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    with pytest.raises(vl_pilot.PilotError, match="poll disconnected"):
        vl_pilot.do_recover_path(edge, SimpleNamespace(timeout=30.0))

    assert edge.start_count == 1


def test_reference_export_accepts_monitor_only_pitch_and_roll_pose(tmp_path) -> None:
    class FakeEdge:
        token = "export-secret"

        def health(self):
            return {
                "ok": True,
                "armed": False,
                "imu": {
                    "available": True,
                    "calibrated": True,
                    "phase": "COMPLETED",
                    "valid": True,
                    "ageS": 0.01,
                    "yawDeg": 2.9,
                    "rollDeg": -52.0,
                    "pitchDeg": 40.0,
                },
            }

    target = tmp_path / "reference-transfer.json"
    assert vl_pilot.do_export_reference(FakeEdge(), SimpleNamespace(out=str(target))) == 0

    state = vl_pilot.json.loads(target.read_text())
    assert state["version"] == 1
    assert state["phase"] == "COMPLETED"
    assert state["relativeYawDeg"] == pytest.approx(2.9)
    assert state["relativeRollDeg"] == pytest.approx(-52.0)
    assert state["relativePitchDeg"] == pytest.approx(40.0)
    unsigned = {key: value for key, value in state.items() if key != "authTag"}
    canonical = vl_pilot.json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected_tag = hmac.new(b"export-secret", canonical, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(state["authTag"], expected_tag)
    assert target.stat().st_mode & 0o777 == 0o600


def test_reference_restore_deletes_one_shot_file_only_after_verified_success(tmp_path) -> None:
    class FakeEdge:
        def imu_restore_reference(self, state):
            assert state["nonce"] == "checkpoint-12"
            return {"phase": "COMPLETED", "referenceRestored": True}

        def health(self):
            return {
                "armed": False,
                "imu": {
                    "phase": "COMPLETED",
                    "referenceRestored": True,
                    "yawDeg": 2.9,
                    "rollDeg": -4.9,
                    "pitchDeg": 15.0,
                },
            }

    target = tmp_path / "reference-transfer.json"
    target.write_text(vl_pilot.json.dumps({"version": 1, "nonce": "checkpoint-12"}))
    target.chmod(0o600)

    assert vl_pilot.do_restore_reference(FakeEdge(), SimpleNamespace(file=str(target))) == 0
    assert not target.exists()


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660])
def test_reference_restore_rejects_transfer_with_wrong_mode(tmp_path, mode) -> None:
    target = tmp_path / "reference-transfer.json"
    target.write_text("{}")
    target.chmod(mode)

    with pytest.raises(vl_pilot.PilotError, match="mode 0600"):
        vl_pilot.do_restore_reference(object(), SimpleNamespace(file=str(target)))

    assert target.exists()


def test_reference_restore_rejects_transfer_owned_by_another_uid(tmp_path, monkeypatch) -> None:
    target = tmp_path / "reference-transfer.json"
    target.write_text("{}")
    target.chmod(0o600)
    monkeypatch.setattr(vl_pilot.os, "getuid", lambda: os.stat(target).st_uid + 1)

    with pytest.raises(vl_pilot.PilotError, match="current uid"):
        vl_pilot.do_restore_reference(object(), SimpleNamespace(file=str(target)))

    assert target.exists()


def test_reference_restore_rejects_symlink(tmp_path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}")
    real.chmod(0o600)
    target = tmp_path / "reference-transfer.json"
    target.symlink_to(real)

    with pytest.raises(vl_pilot.PilotError, match="cannot read reference transfer"):
        vl_pilot.do_restore_reference(object(), SimpleNamespace(file=str(target)))

    assert target.is_symlink()
    assert real.exists()


def test_reference_restore_rejects_non_regular_file(tmp_path) -> None:
    target = tmp_path / "reference-transfer.json"
    target.mkdir(mode=0o600)

    with pytest.raises(vl_pilot.PilotError, match="regular file"):
        vl_pilot.do_restore_reference(object(), SimpleNamespace(file=str(target)))

    assert target.is_dir()


def test_reference_restore_rejects_inode_swap_without_deleting_replacement(tmp_path) -> None:
    target = tmp_path / "reference-transfer.json"
    target.write_text(vl_pilot.json.dumps({"version": 1, "nonce": "checkpoint-12"}))
    target.chmod(0o600)

    class SwappingEdge:
        def imu_restore_reference(self, _state):
            target.unlink()
            target.write_text("replacement")
            target.chmod(0o600)
            return {"phase": "COMPLETED", "referenceRestored": True}

        def health(self):
            return {
                "armed": False,
                "imu": {"phase": "COMPLETED", "referenceRestored": True},
            }

    with pytest.raises(vl_pilot.PilotError, match="changed during restore"):
        vl_pilot.do_restore_reference(SwappingEdge(), SimpleNamespace(file=str(target)))

    assert target.read_text() == "replacement"


def test_yaw_scan_360_sends_one_start_then_polls_without_heartbeat(monkeypatch) -> None:
    class FakeEdge:
        def __init__(self):
            self.start_count = 0
            self.health_count = 0
            self.heartbeat_count = 0

        def yaw_scan_360(self, deadline_s):
            self.start_count += 1
            assert deadline_s == 45.0
            return {"phase": "YAW_SCAN_360", "localOperation": "YAW_SCAN_360"}

        def health(self):
            self.health_count += 1
            if self.health_count == 1:
                return {
                    "armed": True,
                    "imu": {
                        "phase": "YAW_SCAN_360",
                        "scanAccumulatedClockwiseDeg": 180.0,
                    },
                }
            return {
                "armed": False,
                "lease": None,
                "imu": {
                    "phase": "COMPLETED",
                    "reason": "V2_YAW_SCAN_360_COMPLETED",
                    "localOperation": None,
                    "scanAccumulatedClockwiseDeg": 359.0,
                    "yawDeg": 1.0,
                },
            }

        def heartbeat(self):
            self.heartbeat_count += 1
            raise AssertionError("yaw scan must not heartbeat")

    edge = FakeEdge()
    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    result = vl_pilot.do_yaw_scan_360(edge, SimpleNamespace(timeout=45.0))

    assert result == 0
    assert edge.start_count == 1
    assert edge.health_count == 2
    assert edge.heartbeat_count == 0


@pytest.mark.parametrize(
    ("location", "field", "invalid"),
    [
        ("health", "armed", True),
        ("health", "armed", 0),
        ("health", "lease", {"owner": "stale"}),
        ("imu", "localOperation", "YAW_SCAN_360"),
        ("imu", "reason", "WRONG_REASON"),
        ("imu", "yawDeg", None),
        ("imu", "yawDeg", True),
        ("imu", "yawDeg", "0"),
        ("imu", "yawDeg", float("nan")),
        ("imu", "yawDeg", float("inf")),
        ("imu", "yawDeg", 3.01),
        ("imu", "scanAccumulatedClockwiseDeg", True),
        ("imu", "scanAccumulatedClockwiseDeg", float("nan")),
        ("imu", "scanAccumulatedClockwiseDeg", float("inf")),
        ("imu", "scanAccumulatedClockwiseDeg", 356.99),
    ],
)
def test_yaw_scan_360_rejects_unverified_terminal_state(
    monkeypatch, location, field, invalid
) -> None:
    terminal = {
        "armed": False,
        "lease": None,
        "imu": {
            "phase": "COMPLETED",
            "reason": "V2_YAW_SCAN_360_COMPLETED",
            "localOperation": None,
            "scanAccumulatedClockwiseDeg": 357.0,
            "yawDeg": -3.0,
        },
    }
    if location == "health":
        terminal[field] = invalid
    else:
        terminal["imu"][field] = invalid

    class FakeEdge:
        def yaw_scan_360(self, _deadline_s):
            return {"phase": "YAW_SCAN_360", "localOperation": "YAW_SCAN_360"}

        def health(self):
            return terminal

    monkeypatch.setattr(vl_pilot.time, "sleep", lambda _seconds: None)

    with pytest.raises(vl_pilot.PilotError, match="terminal state failed"):
        vl_pilot.do_yaw_scan_360(FakeEdge(), SimpleNamespace(timeout=45.0))
