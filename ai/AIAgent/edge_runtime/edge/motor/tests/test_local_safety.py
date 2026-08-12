from datetime import datetime, timedelta, timezone

import pytest

from edge.motor.local_safety import LocalSafetySupervisor, MotorCommand


class RecordingMotor:
    def __init__(self) -> None:
        self.applied: list[tuple[float, float]] = []
        self.stop_count = 0

    def set_speeds(self, left: float, right: float) -> None:
        self.applied.append((left, right))

    def stop(self) -> None:
        self.stop_count += 1

    def close(self) -> None:
        self.stop()


def command(
    now: datetime,
    *,
    sequence: int = 1,
    expires_delta_ms: int = 300,
    action: str = "FOLLOW_PIPE",
    left: float = 0.12,
    right: float = 0.10,
) -> MotorCommand:
    return MotorCommand(
        command_id=f"cmd-{sequence}",
        mission_id="mission-1",
        sequence=sequence,
        action=action,
        left_speed=left,
        right_speed=right,
        issued_at=now,
        expires_at=now + timedelta(milliseconds=expires_delta_ms),
    )


def test_non_stop_command_requires_arm_and_recent_heartbeat() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=500)

    rejected = safety.apply(command(now), now=now)
    assert rejected.accepted is False
    assert rejected.reason == "EDGE_NOT_ARMED"
    assert motor.applied == []

    safety.arm()
    safety.heartbeat(now=now)
    accepted = safety.apply(command(now), now=now)

    assert accepted.accepted is True
    assert accepted.applied_left_speed == pytest.approx(0.12)
    assert accepted.applied_right_speed == pytest.approx(0.10)
    assert motor.applied == [(0.12, 0.10)]


def test_authenticated_stop_revokes_recover_path_lease_and_prevents_restart() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=60.0)
    accepted, _reason = safety.apply_local_signed(
        -0.45,
        0.45,
        reason="V2_PRIMARY_YAW",
        now=now,
        local_operation="RECOVER_PATH",
    )
    assert accepted is True

    ack = safety.apply(command(now, action="STOP"), now=now)

    assert ack.accepted is True
    assert ack.reason == "STOP_APPLIED"
    assert safety.armed is False
    assert safety.local_operation is None
    before = list(motor.applied)
    accepted, reason = safety.apply_local_signed(
        -0.45,
        0.45,
        reason="V2_PRIMARY_YAW",
        now=now,
        local_operation="RECOVER_PATH",
    )
    assert accepted is False
    assert reason == "EDGE_NOT_ARMED"
    assert motor.applied == before


def test_pi_owned_yaw_step_ignores_laptop_heartbeat_until_bounded_completion() -> None:
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=100)
    safety.arm(now=now)

    safety.begin_local_operation("YAW_STEP", now=now, deadline_s=30.0)
    accepted, reason = safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_YAW_STEP",
        now=now + timedelta(seconds=5),
        local_operation="YAW_STEP",
    )

    assert accepted is True
    assert reason == "COMMAND_APPLIED"
    assert safety.watchdog_tick(now=now + timedelta(seconds=5)) is None
    assert safety.armed is True
    assert safety.local_operation == "YAW_STEP"

    safety.finish_local_operation("YAW_STEP", "V2_YAW_STEP_REACHED")
    assert safety.armed is False
    assert safety.local_operation is None
    assert motor.stop_count >= 3


def test_expired_or_replayed_command_fails_closed() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm()
    safety.heartbeat(now=now)

    expired = safety.apply(command(now, expires_delta_ms=-1), now=now)
    assert expired.accepted is False
    assert expired.reason == "COMMAND_EXPIRED"

    first = safety.apply(command(now, sequence=2), now=now)
    replay = safety.apply(command(now, sequence=2), now=now)
    assert first.accepted is True
    assert replay.accepted is False
    assert replay.reason == "INVALID_SEQUENCE"
    assert motor.stop_count >= 2


def test_command_ttl_stops_motor_even_when_heartbeat_is_still_alive() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=500)
    safety.arm()
    safety.heartbeat(now=now)
    assert safety.apply(command(now, expires_delta_ms=300), now=now).accepted is True

    safety.heartbeat(now=now + timedelta(milliseconds=250))
    reason = safety.watchdog_tick(now=now + timedelta(milliseconds=301))

    assert reason == "COMMAND_TTL_EXPIRED"
    assert safety.armed is True
    assert motor.stop_count >= 3


def test_watchdog_and_estop_override_motion() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=500)
    safety.arm()
    safety.heartbeat(now=now)
    assert safety.apply(command(now), now=now).accepted is True

    watchdog = safety.watchdog_tick(now=now + timedelta(milliseconds=501))
    assert watchdog == "HEARTBEAT_TIMEOUT"
    assert safety.armed is False

    safety.arm()
    safety.heartbeat(now=now)
    safety.set_emergency_stop(True)
    rejected = safety.apply(command(now, sequence=2), now=now)
    assert rejected.accepted is False
    assert rejected.reason == "EMERGENCY_STOP"
    assert safety.armed is False


def test_arm_grants_one_heartbeat_window_before_watchdog_disarms() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=500)

    safety.arm(now=now)

    assert safety.watchdog_tick(now=now + timedelta(milliseconds=400)) is None
    assert safety.armed is True
    assert safety.watchdog_tick(now=now + timedelta(milliseconds=501)) == "HEARTBEAT_TIMEOUT"
    assert safety.armed is False


def test_default_watchdog_tolerates_a_subsecond_heartbeat_jitter() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)

    safety.arm(now=now)

    assert safety.watchdog_tick(now=now + timedelta(milliseconds=750)) is None
    assert safety.armed is True
    assert safety.watchdog_tick(now=now + timedelta(milliseconds=1001)) == "HEARTBEAT_TIMEOUT"
    assert safety.armed is False


def test_arm_starts_a_new_sequence_session_after_emergency_stop_sequence() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    emergency_stop = command(now, sequence=999999, action="STOP")
    safety.apply(emergency_stop, now=now)
    assert safety.last_sequence == 999999

    safety.arm()

    assert safety.last_sequence == -1
    assert safety.armed is True


def test_ultrasonic_none_does_not_block_motion_in_an_open_pipe() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(None)

    ack = safety.apply(command(now), now=now)

    assert ack.accepted is True
    assert motor.applied == [(0.12, 0.10)]


def test_ultrasonic_hard_stop_preempts_valid_motor_command() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.10)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(0.10)

    ack = safety.apply(command(now), now=now)

    assert ack.accepted is False
    assert ack.reason == "ULTRASONIC_HARD_STOP"
    assert motor.applied == []
    assert safety.front_distance_m == 0.10


def test_five_centimeter_hard_stop_allows_distance_just_above_boundary() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.05)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(0.051)

    ack = safety.apply(command(now), now=now)

    assert ack.accepted is True


def test_five_centimeter_hard_stop_blocks_at_boundary() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.05)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(0.05)

    ack = safety.apply(command(now), now=now)

    assert ack.accepted is False
    assert ack.reason == "ULTRASONIC_HARD_STOP"


def test_default_ultrasonic_hard_stop_blocks_motion_at_ten_centimeters() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(0.01)

    ack = safety.apply(command(now), now=now)

    assert ack.accepted is False
    assert ack.reason == "ULTRASONIC_HARD_STOP"
    assert motor.applied == []


@pytest.mark.parametrize("invalid_distance", [float("nan"), float("inf"), -0.01])
def test_invalid_ultrasonic_reading_is_telemetry_only(invalid_distance) -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    safety.update_ultrasonic(invalid_distance)
    ack = safety.apply(command(now), now=now)

    assert safety.ultrasonic_invalid is True
    assert ack.accepted is True
    assert ack.reason == "COMMAND_APPLIED"


@pytest.mark.parametrize("distance", [float("nan"), float("inf"), -0.01])
def test_invalid_ultrasonic_does_not_block_pi_local_imu_motion(distance) -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(distance)

    accepted, reason = safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now,
    )

    assert accepted is True
    assert reason == "COMMAND_APPLIED"
    assert motor.applied[-1] == pytest.approx((-0.65, 0.65))


def test_valid_ultrasonic_hard_stop_still_blocks_pi_local_imu_motion() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)
    safety.update_ultrasonic(0.10)

    accepted, reason = safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now,
    )

    assert accepted is False
    assert reason == "ULTRASONIC_HARD_STOP"
    assert motor.applied == []


@pytest.mark.parametrize(
    "action",
    [
        "CONTINUE",
        "BYPASS_OBSTACLE",
        "AI_LOCAL_PLAN",
        "RECOVER_LEFT",
        "RECOVER_RIGHT",
    ],
)
def test_edge_accepts_current_agent_navigation_actions(action: str) -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(command(now, action=action), now=now)

    assert ack.accepted is True
    assert ack.reason == "COMMAND_APPLIED"


def test_bounded_backward_recovery_is_applied() -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(
        command(now, action="BACKWARD", left=0.20, right=0.20),
        now=now,
    )

    assert ack.accepted is True
    assert ack.reason == "COMMAND_APPLIED"
    assert motor.applied == [(-0.20, -0.20)]


def test_backward_recovery_rejects_excess_speed_or_duration() -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(
        command(
            now,
            action="BACKWARD",
            left=0.31,
            right=0.20,
            expires_delta_ms=801,
        ),
        now=now,
    )

    assert ack.accepted is False
    assert ack.reason == "BACKWARD_RECOVERY_LIMIT"
    assert motor.applied == []


@pytest.mark.parametrize("invalid_speed", [float("nan"), float("inf")])
def test_backward_recovery_rejects_non_finite_speed(invalid_speed: float) -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(
        command(now, action="BACKWARD", left=invalid_speed, right=0.20),
        now=now,
    )

    assert ack.accepted is False
    assert ack.reason == "NON_FINITE_SPEED"
    assert motor.applied == []


def test_backward_recovery_rejects_more_than_800ms_actual_remaining_ttl() -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    future_issued_at = now + timedelta(milliseconds=900)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(
        command(future_issued_at, action="BACKWARD", left=0.20, right=0.20),
        now=now,
    )

    assert ack.accepted is False
    assert ack.reason == "BACKWARD_RECOVERY_LIMIT"
    assert motor.applied == []


def test_real_motor_speed_is_clamped_to_keyboard_ninety_percent() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm()
    safety.heartbeat(now=now)

    ack = safety.apply(command(now, left=1.0, right=0.6), now=now)

    assert ack.accepted is True
    assert ack.applied_left_speed == pytest.approx(0.90)
    assert ack.applied_right_speed == pytest.approx(0.60)
    assert motor.applied == [(0.90, 0.60)]


@pytest.mark.parametrize("action", ["V2_PIVOT_LEFT", "V2_PIVOT_RIGHT"])
def test_legacy_v2_continuous_pivot_actions_are_rejected(action) -> None:
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    ack = safety.apply(command(now, action=action, left=0.65, right=0.65), now=now)

    assert ack.accepted is False
    assert ack.reason == "ACTION_NOT_ALLOWED"
    assert motor.applied == []


def test_pi_local_imu_command_requires_arm_heartbeat_and_signed_hard_cap() -> None:
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, max_speed=0.70)

    assert safety.apply_local_signed(-0.65, 0.65, reason="V2_PRIMARY_YAW", now=now) == (
        False,
        "EDGE_NOT_ARMED",
    )
    safety.arm(now=now)
    safety.heartbeat(now=now)
    assert safety.apply_local_signed(-0.65, 0.65, reason="V2_PRIMARY_YAW", now=now) == (
        True,
        "COMMAND_APPLIED",
    )
    assert motor.applied == [(-0.65, 0.65)]
    assert safety.apply_local_signed(-0.71, 0.65, reason="V2_BAD", now=now) == (
        False,
        "LOCAL_SPEED_LIMIT",
    )
    assert motor.stop_count >= 2


def test_pi_local_default_contract_accepts_point_eight_and_rejects_above() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.arm(now=now)
    safety.heartbeat(now=now)

    assert safety.apply_local_signed(
        0.80, -0.80, reason="V2_YAW_SCAN_360", now=now
    ) == (True, "COMMAND_APPLIED")
    assert safety.apply_local_signed(
        0.81, -0.80, reason="V2_YAW_SCAN_360", now=now
    ) == (False, "LOCAL_SPEED_LIMIT")


def test_recover_path_lease_arms_without_heartbeat_and_is_absolutely_bounded() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=100)

    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=60.0)

    assert safety.armed is True
    assert safety.local_operation == "RECOVER_PATH"
    assert safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now + timedelta(seconds=2),
        local_operation="RECOVER_PATH",
    ) == (True, "COMMAND_APPLIED")
    assert safety.watchdog_tick(now=now + timedelta(seconds=2, milliseconds=250)) == (
        "COMMAND_TTL_EXPIRED"
    )
    assert safety.armed is True
    assert safety.watchdog_tick(now=now + timedelta(seconds=60)) == "LOCAL_OPERATION_DEADLINE"
    assert safety.armed is False
    assert safety.local_operation is None
    assert motor.applied == [(-0.65, 0.65)]


def test_yaw_temp_180_local_operation_accepts_continuous_left_pivot() -> None:
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)

    safety.begin_local_operation("YAW_TEMP_180", now=now, deadline_s=60.0)

    assert safety.apply_local_signed(
        -0.80,
        0.80,
        reason="V2_YAW_TEMP_180",
        now=now,
        local_operation="YAW_TEMP_180",
    ) == (True, "COMMAND_APPLIED")
    assert safety.local_operation == "YAW_TEMP_180"


def test_continuous_timed_forward_stops_at_pi_local_deadline() -> None:
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.0)
    safety.update_ultrasonic(0.80)

    safety.begin_continuous_timed_forward(speed=0.40, duration_s=10.0, now=now)

    assert safety.armed is True
    assert safety.local_operation == "FORWARD_TIMED_CONTINUOUS"
    assert motor.applied[-1] == pytest.approx((0.40, 0.40))
    assert safety.watchdog_tick(now=now + timedelta(seconds=9.9)) is None

    reason = safety.watchdog_tick(now=now + timedelta(seconds=10.0))

    assert reason == "V2_FORWARD_TIMED_COMPLETED"
    assert safety.armed is False
    assert safety.local_operation is None
    assert motor.stop_count >= 2


def test_continuous_forward_local_operation_stops_at_target_distance() -> None:
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.0)
    safety.update_ultrasonic(0.80)

    safety.begin_continuous_forward(
        speed=0.40,
        target_distance_m=0.20,
        now=now,
        deadline_s=60.0,
    )
    safety.update_ultrasonic(0.21)

    assert safety.armed is True
    assert safety.local_operation == "FORWARD_UNTIL_DISTANCE"
    assert motor.applied[-1] == pytest.approx((0.40, 0.40))

    safety.update_ultrasonic(0.20)

    assert safety.armed is False
    assert safety.local_operation is None
    assert motor.stop_count >= 2


def test_continuous_forward_local_operation_allows_invalid_start_and_uses_deadline() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.0)
    safety.update_ultrasonic(float("nan"))

    safety.begin_continuous_forward(
        speed=0.40,
        target_distance_m=0.20,
        deadline_s=7.0,
        now=now,
    )

    assert safety.armed is True
    assert safety.local_operation == "FORWARD_UNTIL_DISTANCE"
    assert motor.applied[-1] == pytest.approx((0.40, 0.40))
    assert safety.watchdog_tick(now=now + timedelta(seconds=7.0)) == (
        "V2_FORWARD_DURATION_COMPLETED"
    )
    assert safety.armed is False


def test_local_operation_rejects_oversized_deadline_and_fails_closed() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)

    with pytest.raises(ValueError, match="deadline"):
        safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=60.001)

    assert safety.armed is False
    assert safety.local_operation is None
    assert motor.stop_count >= 2


def test_non_recovery_commands_cannot_use_or_interrupt_recover_path_lease() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=10.0)

    remote = safety.apply(command(now), now=now)
    wrong_local = safety.apply_local_signed(
        0.2,
        0.2,
        reason="V2_PITCH_ADVANCE",
        now=now,
        local_operation="RECOVER_PATH",
    )

    assert remote.reason == "LOCAL_OPERATION_ACTIVE"
    assert wrong_local == (False, "LOCAL_OPERATION_COMMAND_REJECTED")
    assert safety.armed is False
    assert safety.local_operation is None


def test_local_operation_capability_is_required_for_heartbeat_bypass() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=10.0)

    result = safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now,
    )

    assert result == (False, "LOCAL_OPERATION_CAPABILITY_REQUIRED")
    assert safety.armed is False
    assert safety.local_operation is None


def test_finishing_local_operation_always_stops_disarms_and_clears_lease() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=10.0)

    reason = safety.finish_local_operation("RECOVER_PATH", "V2_POSE_RECOVERED")

    assert reason == "V2_POSE_RECOVERED"
    assert safety.armed is False
    assert safety.local_operation is None
    assert safety.active_command_expires_at is None


@pytest.mark.parametrize("distance", [None, 0.05, float("nan")])
def test_recover_path_ignores_ultrasonic_but_normal_motion_keeps_gate(distance) -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    safety.begin_local_operation("RECOVER_PATH", now=now, deadline_s=10.0)
    assert safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now,
        local_operation="RECOVER_PATH",
    )[0]

    safety.update_ultrasonic(distance)

    assert safety.armed is True
    assert safety.local_operation == "RECOVER_PATH"
    assert safety.apply_local_signed(
        -0.65,
        0.65,
        reason="V2_PRIMARY_YAW",
        now=now,
        local_operation="RECOVER_PATH",
    ) == (True, "COMMAND_APPLIED")


def test_yaw_scan_lease_allows_only_right_pivot_ignores_ultrasonic_and_stop_cancels() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, heartbeat_timeout_ms=100)
    safety.begin_local_operation("YAW_SCAN_360", now=now, deadline_s=45.0)

    assert safety.apply_local_signed(
        0.45,
        -0.45,
        reason="V2_YAW_SCAN_360",
        now=now + timedelta(seconds=2),
        local_operation="YAW_SCAN_360",
    ) == (True, "COMMAND_APPLIED")
    safety.update_ultrasonic(0.01)
    assert safety.armed is True
    assert safety.local_operation == "YAW_SCAN_360"

    ack = safety.apply(command(now, action="STOP", left=0.0, right=0.0), now=now)

    assert ack.accepted is True
    assert safety.armed is False
    assert safety.local_operation is None
    assert safety.active_command_expires_at is None
    assert motor.applied == [(0.45, -0.45)]
