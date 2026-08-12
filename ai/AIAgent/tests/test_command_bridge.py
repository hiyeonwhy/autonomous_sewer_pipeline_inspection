from datetime import timedelta

import pytest

from pipecheckkk_ai.command.bridge import CommandBridge
from pipecheckkk_ai.models import DriveCommand, utc_now


class FakeEdgeClient:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.stop_sequences: list[int] = []
        self.armed = False
        self.heartbeat_count = 0
        self.mismatch_ack = False
        self.front_distance_m: float | None = 0.5
        self.ultrasonic_stop_m = 0.05
        self.yaw_steps: list[str] = []
        self.imu_phase = "READY"
        self.imu_calibrations: list[int] = []

    def health(self) -> dict[str, object]:
        return {
            "ok": True,
            "armed": self.armed,
            "emergencyStop": False,
            "maxSpeed": 0.20,
            "frontDistanceM": self.front_distance_m,
            "ultrasonicStopM": self.ultrasonic_stop_m,
            "imu": {"available": True, "calibrated": True, "phase": self.imu_phase},
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
            "commandId": "wrong" if self.mismatch_ack else payload["commandId"],
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

    def imu_start_yaw_step(self, direction: str) -> dict[str, object]:
        self.yaw_steps.append(direction)
        return {"phase": "PIVOT_YAW", "yawTargetDeg": 10.0}

    def imu_calibrate(self, samples: int = 100) -> dict[str, object]:
        self.imu_calibrations.append(samples)
        self.imu_phase = "READY"
        return {"calibrated": True, "phase": "READY"}


def drive_command(
    *,
    safety_approved: bool = True,
    left_speed: float = 0.12,
    right_speed: float = 0.10,
) -> DriveCommand:
    now = utc_now()
    return DriveCommand(
        command_id="cmd-local-1",
        action="FOLLOW_PIPE",
        left_speed=left_speed,
        right_speed=right_speed,
        ttl_ms=300,
        issued_at=now,
        expires_at=now + timedelta(milliseconds=300),
        safety_approved=safety_approved,
        reason="test",
        perception_model="depth-freespace",
    )


def test_bridge_arms_heartbeats_sequences_and_validates_edge_ack() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)

    status = bridge.arm("mission-1")
    ack = bridge.dispatch(drive_command())

    assert status.armed is True
    assert edge.heartbeat_count == 1
    assert edge.commands[0]["missionId"] == "mission-1"
    assert edge.commands[0]["sequence"] == 1
    assert edge.commands[0]["leftSpeed"] == 0.12
    assert ack.accepted is True
    assert ack.sequence == 1
    assert bridge.latest_ack == ack


def test_bridge_requests_pi_local_ten_degree_yaw_step() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)
    bridge.arm("mission-yaw")

    status = bridge.start_imu_yaw_step("LEFT")

    assert status["phase"] == "PIVOT_YAW"
    assert edge.yaw_steps == ["LEFT"]


def test_bridge_recalibrates_completed_recovery_without_dropping_armed_session() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)
    bridge.arm("mission-repeat")
    edge.imu_phase = "COMPLETED"

    status = bridge.recalibrate_imu_after_recovery(samples=100)

    assert status["phase"] == "READY"
    assert edge.imu_calibrations == [100]
    assert bridge.status.armed is True
    assert bridge.status.mission_id == "mission-repeat"


def test_reset_session_clears_all_previous_mission_edge_state() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)
    bridge.arm("mission-1")
    bridge.dispatch(drive_command())
    bridge.stop_and_disarm("MISSION_COMPLETED")

    status = bridge.reset_session()

    assert status.armed is False
    assert status.connected is False
    assert status.mission_id is None
    assert status.latest_sequence == 0
    assert status.heartbeat_age_ms is None
    assert status.reason == "SESSION_RESET"
    assert bridge.latest_ack is None


def test_bridge_clamps_both_wheels_to_keyboard_ninety_percent_cap() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)
    bridge.arm("mission-fast")

    bridge.dispatch(drive_command(left_speed=1.0, right_speed=0.9))

    assert edge.commands[-1]["leftSpeed"] == 0.90
    assert edge.commands[-1]["rightSpeed"] == 0.90


@pytest.mark.parametrize("distance", [0.05, 0.049])
def test_bridge_refuses_arm_without_safe_ultrasonic_clearance(
    distance: float,
) -> None:
    edge = FakeEdgeClient()
    edge.front_distance_m = distance
    bridge = CommandBridge(edge)

    with pytest.raises(RuntimeError, match="ultrasonic"):
        bridge.arm("mission-unsafe")

    assert edge.armed is False


def test_bridge_allows_arm_when_ultrasonic_has_no_reflection() -> None:
    edge = FakeEdgeClient()
    edge.front_distance_m = None
    bridge = CommandBridge(edge)

    status = bridge.arm("mission-open-pipe")

    assert status.armed is True


def test_bridge_allows_arm_just_over_five_cm() -> None:
    edge = FakeEdgeClient()
    edge.front_distance_m = 0.051
    bridge = CommandBridge(edge)

    status = bridge.arm("mission-clear")

    assert status.armed is True


def test_bridge_allows_close_arm_when_edge_collision_stop_is_disabled() -> None:
    edge = FakeEdgeClient()
    edge.front_distance_m = 0.01
    edge.ultrasonic_stop_m = 0.0
    bridge = CommandBridge(edge)

    status = bridge.arm("mission-contact-allowed")

    assert status.armed is True


def test_bridge_rejects_unapproved_motion_and_sends_stop() -> None:
    edge = FakeEdgeClient()
    bridge = CommandBridge(edge)
    bridge.arm("mission-1")

    with pytest.raises(RuntimeError, match="not safety-approved"):
        bridge.dispatch(drive_command(safety_approved=False))

    assert edge.commands == []
    assert edge.stop_sequences == [1]


def test_ack_mismatch_fails_closed_and_disarms() -> None:
    edge = FakeEdgeClient()
    edge.mismatch_ack = True
    bridge = CommandBridge(edge)
    bridge.arm("mission-1")

    with pytest.raises(RuntimeError, match="ACK mismatch"):
        bridge.dispatch(drive_command())

    assert edge.stop_sequences == [2]
    assert edge.armed is False
