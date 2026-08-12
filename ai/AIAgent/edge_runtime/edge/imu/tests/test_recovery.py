from datetime import datetime, timezone
from threading import RLock

import pytest

from edge.imu.recovery import ImuObservation, ImuPhase, V2ImuRecoveryController, wrap_degrees


class FakeSensor:
    def __init__(self, observations):
        self.observations = iter(observations)
        self.dynamic_modes = []
        self.calibrated = False

    def calibrate(self, *, samples=100, sample_delay_s=0.01):
        self.calibrated = True

    def read(self):
        return next(self.observations)

    def set_dynamic_mode(self, enabled):
        self.dynamic_modes.append(bool(enabled))

    def close(self):
        pass


class FakeSafety:
    def __init__(self):
        self.safety_lock = RLock()
        self.armed = False
        self.commands = []
        self.stop_reasons = []
        self.disarm_reasons = []
        self.local_operation = None

    def stop_motion(self, reason):
        self.stop_reasons.append(reason)

    def disarm(self, reason="DISARMED"):
        self.armed = False
        self.local_operation = None
        self.disarm_reasons.append(reason)
        return reason

    def begin_local_operation(self, operation, *, now, deadline_s):
        if not 0 < deadline_s <= 60.0:
            self.disarm("INVALID_LOCAL_OPERATION_DEADLINE")
            raise ValueError("deadline")
        self.local_operation = operation
        self.armed = True

    def finish_local_operation(self, operation, reason):
        assert self.local_operation == operation
        return self.disarm(reason)

    def apply_local_signed(
        self, left, right, *, reason, now, ttl_s=0.25, local_operation=None
    ):
        if not self.armed:
            return False, "EDGE_NOT_ARMED"
        if self.local_operation is not None and local_operation != self.local_operation:
            return False, "LOCAL_OPERATION_CAPABILITY_REQUIRED"
        self.commands.append((left, right, reason, ttl_s))
        return True, "COMMAND_APPLIED"


def obs(yaw=0.0, roll=0.0, pitch=0.0, *, age=0.0, valid=True):
    return ImuObservation(yaw, roll, pitch, age, valid)


def make_controller(observations, *, settle_samples=1):
    sensor = FakeSensor(observations)
    safety = FakeSafety()
    controller = V2ImuRecoveryController(
        sensor=sensor,
        safety=safety,
        settle_samples=settle_samples,
        phase_timeout_s=30.0,
    )
    return controller, sensor, safety


def test_calibration_captures_yaw_roll_pitch_as_relative_zero():
    controller, sensor, safety = make_controller([obs(yaw=0.0, roll=4.0, pitch=-2.0)])

    status = controller.calibrate(samples=2)

    assert sensor.calibrated is True
    assert safety.armed is False
    assert status["phase"] == ImuPhase.READY
    assert status["yawDeg"] == pytest.approx(0.0)
    assert status["rollDeg"] == pytest.approx(0.0)
    assert status["pitchDeg"] == pytest.approx(0.0)
    assert status["pitchStopDeg"] is None
    assert status["warningPitchDeg"] is None
    assert status["rollStopDeg"] is None
    assert status["yawLimitDeg"] is None


@pytest.mark.parametrize(
    "override",
    [
        {"yaw_step_deg": 5.0},
    ],
)
def test_v2_imu_motion_contract_cannot_be_reconfigured(override):
    with pytest.raises(ValueError, match="fixed motion contract"):
        V2ImuRecoveryController(
            sensor=FakeSensor([obs()]),
            safety=FakeSafety(),
            **override,
        )


def test_pitch_is_monitor_only_and_never_stops_pitch_guard():
    controller, sensor, safety = make_controller(
        [obs(roll=1.0, pitch=2.0), obs(roll=1.0, pitch=36.99), obs(roll=1.0, pitch=37.0)]
    )
    controller.calibrate(samples=2)
    safety.stop_reasons.clear()
    safety.armed = True
    controller.start_pitch_guard()

    controller.poll_once(now_monotonic=1.0, now=datetime.now(timezone.utc))
    assert controller.phase is ImuPhase.PITCH_GUARD
    assert safety.stop_reasons == []

    above_old_limit = controller.poll_once(
        now_monotonic=1.02,
        now=datetime.now(timezone.utc),
    )
    assert above_old_limit["phase"] == ImuPhase.PITCH_GUARD
    assert above_old_limit["pitchDeg"] == pytest.approx(35.0)
    assert above_old_limit["advanceLimitTrigger"] is None
    assert safety.stop_reasons == []


def test_roll_is_monitor_only_and_never_stops_pitch_guard():
    for limit_roll in (45.0, -45.0):
        controller, _sensor, safety = make_controller(
            [obs(roll=0.0, pitch=0.0), obs(roll=44.99 if limit_roll > 0 else -44.99), obs(roll=limit_roll)]
        )
        controller.calibrate(samples=2)
        safety.stop_reasons.clear()
        safety.armed = True
        controller.start_pitch_guard()

        before_old_limit = controller.poll_once(
            now_monotonic=1.0,
            now=datetime.now(timezone.utc),
        )
        above_old_limit = controller.poll_once(
            now_monotonic=1.02,
            now=datetime.now(timezone.utc),
        )

        assert before_old_limit["phase"] == ImuPhase.PITCH_GUARD
        assert above_old_limit["phase"] == ImuPhase.PITCH_GUARD
        assert above_old_limit["rollDeg"] == pytest.approx(limit_roll)
        assert above_old_limit["rollStopDeg"] is None
        assert above_old_limit["advanceLimitTrigger"] is None
        assert safety.stop_reasons == []


def test_pitch_monitor_keeps_initial_calibration_reference_after_yaw_drift():
    controller, _sensor, safety = make_controller(
        [
            obs(yaw=0.0, pitch=0.0),
            obs(yaw=10.0, pitch=-5.27),
            obs(yaw=10.0, pitch=-34.99),
            obs(yaw=10.0, pitch=-35.0),
        ]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_yaw_step("LEFT", now_monotonic=0.0)
    reached = controller.poll_once(
        now_monotonic=0.02,
        now=datetime.now(timezone.utc),
    )
    assert reached["phase"] == ImuPhase.YAW_STEP_REACHED

    safety.armed = True
    started = controller.start_pitch_guard()
    before_limit = controller.poll_once(
        now_monotonic=0.04,
        now=datetime.now(timezone.utc),
    )
    above_old_limit = controller.poll_once(
        now_monotonic=0.06,
        now=datetime.now(timezone.utc),
    )

    assert started["pitchDeg"] == pytest.approx(-5.27)
    assert before_limit["phase"] == ImuPhase.PITCH_GUARD
    assert before_limit["pitchDeg"] == pytest.approx(-34.99)
    assert above_old_limit["phase"] == ImuPhase.PITCH_GUARD
    assert above_old_limit["pitchDeg"] == pytest.approx(-35.0)
    assert safety.disarm_reasons[-1] == "V2_YAW_STEP_REACHED"


def test_yaw_step_is_pi_owned_and_finishes_disarmed_for_camera_assessment():
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), obs(yaw=5.0), obs(yaw=10.0)]
    )
    controller.calibrate(samples=2)
    safety.armed = True

    started = controller.start_yaw_step("LEFT", now_monotonic=0.0)
    moving = controller.poll_once(now_monotonic=0.02, now=datetime.now(timezone.utc))
    reached = controller.poll_once(now_monotonic=0.04, now=datetime.now(timezone.utc))

    assert started["phase"] == ImuPhase.PIVOT_YAW
    assert started["yawTargetDeg"] == pytest.approx(10.0)
    assert started["localOperation"] == "YAW_STEP"
    assert safety.local_operation == "YAW_STEP" or reached["phase"] == ImuPhase.YAW_STEP_REACHED
    assert moving["phase"] == ImuPhase.PIVOT_YAW
    assert moving["localOperation"] == "YAW_STEP"
    assert safety.commands[-1][:2] == pytest.approx((-0.65, 0.65))
    assert reached["phase"] == ImuPhase.YAW_STEP_REACHED
    assert reached["yawDeg"] == pytest.approx(10.0)
    assert reached["localOperation"] is None
    assert safety.armed is False
    assert safety.local_operation is None
    assert safety.disarm_reasons[-1] == "V2_YAW_STEP_REACHED"


def test_right_yaw_step_keeps_standard_ten_degree_turn_power():
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), obs(yaw=-5.0), obs(yaw=-10.0)]
    )
    controller.calibrate(samples=2)
    safety.armed = True

    started = controller.start_yaw_step("RIGHT", now_monotonic=0.0)
    moving = controller.poll_once(now_monotonic=0.02, now=datetime.now(timezone.utc))
    reached = controller.poll_once(now_monotonic=0.04, now=datetime.now(timezone.utc))

    assert started["yawTargetDeg"] == pytest.approx(-10.0)
    assert moving["phase"] == ImuPhase.PIVOT_YAW
    assert safety.commands[-1][:2] == pytest.approx((0.65, -0.65))
    assert reached["phase"] == ImuPhase.YAW_STEP_REACHED


@pytest.mark.parametrize(
    ("start_yaw", "direction", "expected_target", "expected_power"),
    [
        (179.0, "LEFT", -171.0, (-0.65, 0.65)),
        (-179.0, "RIGHT", 171.0, (0.65, -0.65)),
    ],
)
def test_yaw_step_crossing_wrap_boundary_starts_in_requested_direction(
    start_yaw, direction, expected_target, expected_power
):
    controller, _sensor, safety = make_controller([obs(yaw=0.0), obs(yaw=start_yaw)])
    controller.calibrate(samples=2)
    controller.last_observation = obs(yaw=start_yaw)
    safety.armed = True

    started = controller.start_current_yaw_step(direction, now_monotonic=0.0)
    moving = controller.poll_once(
        now_monotonic=0.02, now=datetime.now(timezone.utc)
    )

    assert started["yawTargetDeg"] == pytest.approx(expected_target)
    assert moving["phase"] == ImuPhase.PIVOT_YAW
    assert safety.commands[-1][:2] == pytest.approx(expected_power)
    assert safety.armed is True


def test_yaw_step_dynamic_mode_start_failure_rolls_back_local_operation() -> None:
    controller, sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True

    def fail_dynamic_mode(enabled):
        assert enabled is True
        assert safety.local_operation == "YAW_STEP"
        raise OSError("dynamic mode failed")

    sensor.set_dynamic_mode = fail_dynamic_mode

    with pytest.raises(OSError, match="dynamic mode failed"):
        controller.start_yaw_step("LEFT", now_monotonic=0.0)

    assert controller.phase is ImuPhase.READY
    assert controller.status()["localOperation"] is None
    assert controller.status()["operationDeadlineS"] is None
    assert controller.status()["yawTargetDeg"] is None
    assert safety.local_operation is None
    assert safety.armed is False
    assert safety.disarm_reasons[-1] == "V2_YAW_STEP_START_FAILED"


def test_yaw_step_allows_loaded_turn_past_old_fifteen_second_timeout() -> None:
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), obs(yaw=-2.0), obs(yaw=-3.0)]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_yaw_step("RIGHT", now_monotonic=0.0)

    still_turning = controller.poll_once(
        now_monotonic=16.0, now=datetime.now(timezone.utc)
    )
    timed_out = controller.poll_once(
        now_monotonic=30.1, now=datetime.now(timezone.utc)
    )

    assert still_turning["phase"] == ImuPhase.PIVOT_YAW
    assert safety.commands[-1][:2] == pytest.approx((0.65, -0.65))
    assert timed_out["phase"] == ImuPhase.ERROR
    assert timed_out["reason"] == "V2_YAW_STEP_DEADLINE"


def test_yaw_step_targets_stay_on_ten_degree_grid_despite_detection_yaw_offset():
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True

    targets = []
    for measured_yaw in (3.0, -8.0, -18.0, -28.0):
        controller.last_observation = obs(yaw=measured_yaw)
        if targets:
            controller.phase = ImuPhase.YAW_STEP_REACHED
        targets.append(controller.start_yaw_step("RIGHT")["yawTargetDeg"])

    assert targets == pytest.approx([-10.0, -20.0, -30.0, -40.0])


@pytest.mark.parametrize("operation", ["capture", "clear", "restore"])
def test_temporary_reference_changes_hold_shared_safety_lock(operation):
    class TrackingLock:
        def __init__(self):
            self.held = False
            self.enter_count = 0

        def __enter__(self):
            assert self.held is False
            self.held = True
            self.enter_count += 1

        def __exit__(self, *_args):
            self.held = False

    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    controller.last_observation = obs(yaw=8.8, roll=1.2, pitch=-0.4)
    lock = TrackingLock()
    safety.safety_lock = lock
    original_status = controller.status
    controller.status = lambda: (assert_lock_held(lock), original_status())[1]

    if operation == "capture":
        controller.capture_temporary_reference("AVOIDANCE")
    elif operation == "clear":
        controller.clear_temporary_reference()
    else:
        controller.restore_temporary_reference(
            "AVOIDANCE", yaw_deg=8.8, roll_deg=1.2, pitch_deg=-0.4
        )

    assert lock.enter_count == 1
    assert lock.held is False


def test_temporary_reference_is_reusable_without_replacing_mission_calibration():
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    mission_reference = (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    )
    controller.last_observation = obs(yaw=8.8, roll=1.2, pitch=-0.4)

    captured = controller.capture_temporary_reference("AVOIDANCE")

    assert captured["temporaryReference"] == {
        "name": "AVOIDANCE",
        "yawDeg": pytest.approx(8.8),
        "rollDeg": pytest.approx(1.2),
        "pitchDeg": pytest.approx(-0.4),
    }
    assert (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    ) == mission_reference

    safety.armed = True
    left = controller.start_relative_yaw_step("LEFT")
    controller.phase = ImuPhase.YAW_STEP_REACHED
    controller.last_observation = obs(yaw=18.8)
    right = controller.start_relative_yaw_step("RIGHT")

    assert left["yawTargetDeg"] == pytest.approx(18.8)
    assert right["yawTargetDeg"] == pytest.approx(-1.2)
    assert right["temporaryReference"]["yawDeg"] == pytest.approx(8.8)


def test_current_yaw_step_uses_live_heading_without_temporary_reference() -> None:
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    controller.last_observation = obs(yaw=23.4)
    safety.armed = True

    result = controller.start_current_yaw_step("LEFT")

    assert result["yawTargetDeg"] == pytest.approx(33.4)
    assert result["temporaryReference"] is None
    assert result["phase"] == ImuPhase.PIVOT_YAW


def test_yaw_step_direction_reversal_walks_target_back_to_original_heading():
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True

    for measured_yaw in (0.0, 10.0, 20.0):
        controller.last_observation = obs(yaw=measured_yaw)
        if measured_yaw:
            controller.phase = ImuPhase.YAW_STEP_REACHED
        controller.start_yaw_step("LEFT")

    targets = []
    for measured_yaw in (30.0, 20.0, 10.0):
        controller.last_observation = obs(yaw=measured_yaw)
        controller.phase = ImuPhase.YAW_STEP_REACHED
        targets.append(controller.start_yaw_step("RIGHT")["yawTargetDeg"])

    assert targets == pytest.approx([20.0, 10.0, 0.0])


def test_yaw_step_has_no_cumulative_thirty_five_degree_limit():
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True
    for measured_yaw in (0.0, 10.0, 20.0, 30.0):
        controller.last_observation = obs(yaw=measured_yaw)
        if measured_yaw:
            controller.phase = ImuPhase.YAW_STEP_REACHED
        controller.start_yaw_step("LEFT")
    controller.last_observation = obs(yaw=40.0)
    controller.phase = ImuPhase.YAW_STEP_REACHED

    status = controller.start_yaw_step("LEFT")

    assert status["yawTargetDeg"] == pytest.approx(50.0)
    assert status["yawLimitDeg"] is None


def test_recovery_can_start_early_from_pitch_guard_after_visual_clearance():
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.PITCH_GUARD

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert safety.stop_reasons[-1] == "V2_RECOVERY_START"


def test_recovery_can_start_from_ready_for_normal_cruise_yaw_drift() -> None:
    controller, _sensor, safety = make_controller([obs(yaw=0.0)])
    controller.calibrate(samples=2)
    safety.armed = True

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert safety.stop_reasons[-1] == "V2_RECOVERY_START"


def test_roll_recovery_uses_thirty_second_phase_timeout() -> None:
    controller, _sensor, safety = make_controller(
        [
            obs(yaw=0.0, roll=0.0),
            obs(yaw=0.0, roll=0.0),
            obs(yaw=0.0, roll=20.0),
            obs(yaw=0.0, roll=20.0),
            obs(yaw=0.0, roll=20.0),
        ]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_local_recovery(
        now=datetime.now(timezone.utc),
        now_monotonic=0.0,
        deadline_s=60.0,
    )

    entered_roll = controller.poll_once(
        now_monotonic=0.02, now=datetime.now(timezone.utc)
    )
    still_recovering = controller.poll_once(
        now_monotonic=16.0, now=datetime.now(timezone.utc)
    )
    armed_at_16_seconds = safety.armed
    timed_out = controller.poll_once(
        now_monotonic=30.1, now=datetime.now(timezone.utc)
    )

    assert entered_roll["phase"] == ImuPhase.ROLL_RECOVERY
    assert still_recovering["phase"] == ImuPhase.ROLL_RECOVERY
    assert armed_at_16_seconds is True
    assert timed_out["phase"] == ImuPhase.ERROR
    assert timed_out["reason"] == "V2_RECOVERY_TIMEOUT"


def test_recovery_runs_primary_yaw_roll_final_yaw_and_keeps_session_armed_for_next_cycle():
    controller, sensor, safety = make_controller(
        [
            obs(yaw=0.0, roll=5.0, pitch=0.0),  # calibration
            obs(yaw=20.0, roll=15.0, pitch=30.0),  # pitch threshold
            obs(yaw=20.0, roll=15.0, pitch=30.0),  # primary yaw motion
            obs(yaw=1.0, roll=15.0, pitch=30.0),  # primary -> roll motion
            obs(yaw=-10.0, roll=5.0, pitch=30.0),  # roll -> final yaw motion
            obs(yaw=0.5, roll=5.0, pitch=30.0),  # final complete
            obs(yaw=0.5, roll=5.0, pitch=30.0),  # next-cycle calibration
        ]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_pitch_guard()
    controller.poll_once(now_monotonic=0.0, now=datetime.now(timezone.utc))
    controller.start_recovery(now_monotonic=0.01)

    controller.poll_once(now_monotonic=0.02, now=datetime.now(timezone.utc))
    controller.poll_once(now_monotonic=0.04, now=datetime.now(timezone.utc))
    controller.poll_once(now_monotonic=0.06, now=datetime.now(timezone.utc))
    controller.poll_once(now_monotonic=0.08, now=datetime.now(timezone.utc))

    assert safety.commands[0][:2] == pytest.approx((0.65, -0.65))
    assert safety.commands[1][:2] == pytest.approx((0.70, 0.05))
    assert safety.commands[2][:2] == pytest.approx((-0.45, 0.45))
    assert all((left, right) != (0.0, 0.0) for left, right, *_ in safety.commands)
    assert controller.phase is ImuPhase.COMPLETED
    assert safety.armed is True
    assert safety.disarm_reasons == []
    assert sensor.dynamic_modes[-1] is False

    resumed = controller.calibrate(samples=2)

    assert resumed["phase"] == ImuPhase.READY
    assert resumed["calibrated"] is True
    assert safety.armed is True


def test_wrap_boundary_uses_shortest_yaw_error():
    assert wrap_degrees(179.0 - (-170.0)) == pytest.approx(-11.0)


def test_stale_imu_is_ignored_without_stop_or_disarm_during_motion():
    controller, sensor, safety = make_controller([obs(), obs(age=0.21)])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_pitch_guard()

    status = controller.poll_once(
        now_monotonic=1.0, now=datetime.now(timezone.utc)
    )

    assert status["phase"] == ImuPhase.PITCH_GUARD
    assert status["reason"] == "V2_IMU_STALE_IGNORED_1"
    assert safety.armed is True
    assert safety.disarm_reasons == []
    assert sensor.dynamic_modes[-1] is True


def test_negative_imu_age_is_ignored_without_disarm_during_active_motion():
    controller, sensor, safety = make_controller([obs(), obs(age=-0.01)])
    controller.calibrate(samples=2)
    safety.stop_reasons.clear()
    safety.armed = True
    controller.start_yaw_step("LEFT", now_monotonic=0.0)

    status = controller.poll_once(
        now_monotonic=0.02, now=datetime.now(timezone.utc)
    )

    assert status["phase"] == ImuPhase.PIVOT_YAW
    assert status["reason"] == "V2_IMU_STALE_IGNORED_1"
    assert safety.armed is True
    assert safety.local_operation == "YAW_STEP"
    assert safety.stop_reasons == []
    assert safety.disarm_reasons == []
    assert sensor.dynamic_modes[-1] is True


def test_single_invalid_imu_read_is_ignored_and_next_valid_sample_resumes_control():
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), obs(yaw=0.0, valid=False), obs(yaw=5.0)]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_yaw_step("LEFT", now_monotonic=0.0)

    ignored = controller.poll_once(now_monotonic=0.02, now=datetime.now(timezone.utc))
    resumed = controller.poll_once(now_monotonic=0.04, now=datetime.now(timezone.utc))

    assert ignored["phase"] == ImuPhase.PIVOT_YAW
    assert ignored["reason"] == "V2_IMU_INVALID_IGNORED_1"
    assert safety.armed is True
    assert resumed["phase"] == ImuPhase.PIVOT_YAW
    assert safety.commands[-1][:2] == pytest.approx((-0.65, 0.65))


def test_repeated_invalid_imu_reads_never_fail_closed():
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), *[obs(yaw=0.0, valid=False) for _ in range(20)]]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_yaw_step("LEFT", now_monotonic=0.0)

    statuses = [
        controller.poll_once(now_monotonic=index * 0.02, now=datetime.now(timezone.utc))
        for index in range(1, 21)
    ]

    assert statuses[-1]["phase"] == ImuPhase.PIVOT_YAW
    assert statuses[-1]["reason"] == "V2_IMU_INVALID_IGNORED_20"
    assert safety.armed is True
    assert "V2_IMU_INVALID" not in safety.disarm_reasons


def test_valid_imu_sample_resets_ignored_invalid_counter():
    controller, _sensor, safety = make_controller(
        [
            obs(yaw=0.0),
            obs(yaw=0.0, valid=False),
            obs(yaw=0.0, valid=False),
            obs(yaw=5.0),
            obs(yaw=5.0, valid=False),
        ]
    )
    controller.calibrate(samples=2)
    safety.armed = True
    controller.start_yaw_step("LEFT", now_monotonic=0.0)

    controller.poll_once(now_monotonic=0.02, now=datetime.now(timezone.utc))
    controller.poll_once(now_monotonic=0.04, now=datetime.now(timezone.utc))
    controller.poll_once(now_monotonic=0.06, now=datetime.now(timezone.utc))
    ignored_after_valid = controller.poll_once(
        now_monotonic=0.08, now=datetime.now(timezone.utc)
    )

    assert ignored_after_valid["reason"] == "V2_IMU_INVALID_IGNORED_1"
    assert safety.armed is True


def test_recovery_retries_watchdog_disarm_without_losing_original_reference():
    controller, _sensor, safety = make_controller([
        obs(yaw=5.0, roll=2.0, pitch=1.0),
        obs(yaw=2.0, roll=-18.0, pitch=12.0),
    ])
    controller.calibrate(samples=2)
    original_reference = (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    )
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"
    safety.armed = True

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    ) == original_reference


def test_start_recovery_holds_safety_lock_through_validation_and_transition():
    class TrackingLock:
        held = False

        def __enter__(self):
            assert not self.held
            self.held = True

        def __exit__(self, *_args):
            self.held = False

    controller, sensor, safety = make_controller([obs(), obs(yaw=2.0)])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"
    lock = TrackingLock()
    safety.safety_lock = lock
    original_read = sensor.read
    original_dynamic_mode = sensor.set_dynamic_mode
    sensor.read = lambda: (assert_lock_held(lock), original_read())[1]
    sensor.set_dynamic_mode = lambda enabled: (
        assert_lock_held(lock),
        original_dynamic_mode(enabled),
    )[1]

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert lock.held is False


def assert_lock_held(lock) -> None:
    assert lock.held is True


def test_watchdog_retry_with_stale_fresh_sample_stops_disarms_and_becomes_terminal():
    controller, sensor, safety = make_controller([obs(), obs(age=0.21)])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_IMU_STALE"
    assert safety.armed is False
    assert safety.stop_reasons[-1] == "V2_IMU_STALE"
    assert safety.disarm_reasons[-1] == "V2_IMU_STALE"
    assert sensor.dynamic_modes[-1] is False


def test_watchdog_retry_read_exception_stops_disarms_and_becomes_terminal():
    controller, sensor, safety = make_controller([obs()])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"

    def fail_read():
        raise OSError("i2c offline")

    sensor.read = fail_read

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_IMU_START_EXCEPTION:i2c offline"
    assert safety.armed is False
    assert safety.stop_reasons[-1] == "V2_IMU_START_EXCEPTION:i2c offline"
    assert safety.disarm_reasons[-1] == "V2_IMU_START_EXCEPTION:i2c offline"


def test_non_whitelisted_error_cannot_start_recovery_and_fails_closed():
    controller, _sensor, safety = make_controller([obs()])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED_EXTRA"

    with pytest.raises(RuntimeError, match="requires ready"):
        controller.start_recovery(now_monotonic=1.0)

    assert safety.armed is False
    assert safety.stop_reasons[-1] == "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED_EXTRA"
    assert safety.disarm_reasons[-1] == "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED_EXTRA"


def test_recover_path_atomically_validates_fresh_imu_starts_lease_and_arms() -> None:
    controller, _sensor, safety = make_controller([obs(yaw=5.0), obs(yaw=8.0)])
    controller.calibrate(samples=2)

    status = controller.start_local_recovery(
        now_monotonic=10.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=45.0,
    )

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert status["localOperation"] == "RECOVER_PATH"
    assert status["operationDeadlineS"] == pytest.approx(45.0)
    assert safety.armed is True
    assert safety.local_operation == "RECOVER_PATH"
    assert controller.relative_yaw_deg == pytest.approx(3.0)


def test_recover_path_accepts_recorded_yaw_step_checkpoint_without_recalibration() -> None:
    controller, _sensor, safety = make_controller([obs(), obs(yaw=-16.25, roll=-24.72)])
    controller.calibrate(samples=2)
    original_references = (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    )
    controller.phase = ImuPhase.YAW_STEP_REACHED

    status = controller.start_local_recovery(
        now_monotonic=10.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=60.0,
    )

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert status["localOperation"] == "RECOVER_PATH"
    assert safety.armed is True
    assert (
        controller.reference_yaw_deg,
        controller.reference_roll_deg,
        controller.reference_pitch_deg,
    ) == original_references


def test_recover_path_startup_with_stale_imu_fails_closed_without_lease() -> None:
    controller, sensor, safety = make_controller([obs(), obs(age=0.21)])
    controller.calibrate(samples=2)

    status = controller.start_local_recovery(
        now_monotonic=1.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=60.0,
    )

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_IMU_STALE"
    assert safety.armed is False
    assert safety.local_operation is None
    assert safety.stop_reasons[-1] == "V2_IMU_STALE"
    assert sensor.dynamic_modes[-1] is False


def test_recover_path_single_invalid_imu_sample_pauses_without_disarm() -> None:
    controller, _sensor, safety = make_controller([obs(), obs(), obs(valid=False)])
    controller.calibrate(samples=2)
    controller.start_local_recovery(
        now_monotonic=1.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=60.0,
    )

    status = controller.poll_once(
        now_monotonic=1.02,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )

    assert status["phase"] == ImuPhase.PRIMARY_YAW
    assert status["reason"] == "V2_IMU_INVALID_IGNORED_1"
    assert safety.armed is True
    assert safety.local_operation == "RECOVER_PATH"
    assert status["localOperation"] == "RECOVER_PATH"


def test_recover_path_absolute_deadline_fails_closed_even_between_phase_timeouts() -> None:
    controller, _sensor, safety = make_controller([obs(), obs(), obs(yaw=20.0)])
    controller.calibrate(samples=2)
    controller.start_local_recovery(
        now_monotonic=10.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=20.0,
    )

    status = controller.poll_once(
        now_monotonic=30.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_RECOVER_PATH_DEADLINE"
    assert safety.armed is False
    assert safety.local_operation is None


def test_recover_path_completion_stops_disarms_and_clears_lease() -> None:
    controller, sensor, safety = make_controller(
        [obs(), obs(), obs(yaw=20.0), obs(yaw=0.0, roll=10.0), obs(), obs()]
    )
    controller.calibrate(samples=2)
    controller.start_local_recovery(
        now_monotonic=0.0,
        now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        deadline_s=60.0,
    )

    for tick in (0.02, 0.04, 0.06, 0.08):
        status = controller.poll_once(
            now_monotonic=tick,
            now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        )

    assert status["phase"] == ImuPhase.COMPLETED
    assert safety.commands[0][3] == pytest.approx(0.25)
    assert safety.armed is False
    assert safety.local_operation is None
    assert safety.disarm_reasons[-1] == "V2_POSE_RECOVERED"
    assert status["localOperation"] is None
    assert status["operationDeadlineS"] is None
    assert sensor.dynamic_modes[-1] is False


def test_watchdog_retry_transition_exception_stops_disarms_and_becomes_terminal():
    controller, sensor, safety = make_controller([obs(), obs()])
    controller.calibrate(samples=2)
    safety.armed = True
    controller.phase = ImuPhase.ERROR
    controller.last_reason = "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"

    def fail_dynamic_mode(_enabled):
        raise OSError("dynamic mode failed")

    sensor.set_dynamic_mode = fail_dynamic_mode

    status = controller.start_recovery(now_monotonic=1.0)

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_IMU_START_EXCEPTION:dynamic mode failed"
    assert safety.armed is False
    assert safety.stop_reasons[-1] == "V2_IMU_START_EXCEPTION:dynamic mode failed"
    assert safety.disarm_reasons[-1] == "V2_IMU_START_EXCEPTION:dynamic mode failed"


def test_reference_transfer_recalibrates_sensor_bias_but_preserves_relative_pose() -> None:
    captured_at = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, sensor, safety = make_controller([obs(yaw=12.0, roll=-6.0, pitch=14.0)])
    state = {
        "version": 1,
        "nonce": "checkpoint-12",
        "capturedAt": captured_at.isoformat(),
        "phase": "COMPLETED",
        "relativeYawDeg": 2.9,
        "relativeRollDeg": -4.9,
        "relativePitchDeg": 15.0,
    }

    status = controller.restore_reference_state(state, now=captured_at, max_age_s=300.0)

    assert sensor.calibrated is True
    assert status["phase"] == ImuPhase.COMPLETED
    assert status["yawDeg"] == pytest.approx(2.9)
    assert status["rollDeg"] == pytest.approx(-4.9)
    assert status["pitchDeg"] == pytest.approx(15.0)
    assert status["referenceRestored"] is True
    assert safety.armed is False


def test_reference_transfer_rejects_expired_or_non_completed_state() -> None:
    now = datetime(2026, 8, 6, 12, 10, tzinfo=timezone.utc)
    base = {
        "version": 1,
        "nonce": "checkpoint-12",
        "capturedAt": datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc).isoformat(),
        "phase": "COMPLETED",
        "relativeYawDeg": 2.9,
        "relativeRollDeg": -4.9,
        "relativePitchDeg": 15.0,
    }
    controller, _sensor, safety = make_controller([obs()])

    with pytest.raises(RuntimeError, match="expired"):
        controller.restore_reference_state(base, now=now, max_age_s=300.0)
    assert controller.phase is ImuPhase.UNCALIBRATED
    assert safety.armed is False

    controller, _sensor, _safety = make_controller([obs()])
    invalid_phase = {**base, "capturedAt": now.isoformat(), "phase": "READY"}
    with pytest.raises(RuntimeError, match="COMPLETED"):
        controller.restore_reference_state(invalid_phase, now=now, max_age_s=300.0)


def test_yaw_scan_360_starts_from_completed_and_tracks_clockwise_wraps_until_home() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, sensor, safety = make_controller(
        [
            obs(),
            obs(yaw=2.9, roll=-4.9, pitch=15.0),
            obs(yaw=-100.0, roll=-4.9, pitch=15.0),
            obs(yaw=170.0, roll=-4.9, pitch=15.0),
            obs(yaw=5.0, roll=-4.9, pitch=15.0),
            obs(yaw=-2.0, roll=-4.9, pitch=15.0),
        ]
    )
    controller.calibrate(samples=2)
    controller.phase = ImuPhase.COMPLETED

    started = controller.start_yaw_scan_360(now=now, now_monotonic=10.0, deadline_s=45.0)
    statuses = [
        controller.poll_once(now=now, now_monotonic=tick)
        for tick in (10.02, 10.04, 10.06, 10.08)
    ]

    assert started["phase"] == ImuPhase.YAW_SCAN_360
    assert started["localOperation"] == "YAW_SCAN_360"
    assert statuses[-2]["scanAccumulatedClockwiseDeg"] == pytest.approx(357.9)
    assert statuses[-2]["phase"] == ImuPhase.YAW_SCAN_360
    assert statuses[-1]["phase"] == ImuPhase.COMPLETED
    assert statuses[-1]["scanAccumulatedClockwiseDeg"] == pytest.approx(364.9)
    assert safety.commands[0][:2] == pytest.approx((0.80, -0.80))
    assert all(command[:2] == pytest.approx((0.80, -0.80)) for command in safety.commands)
    assert safety.armed is False
    assert safety.local_operation is None
    assert sensor.dynamic_modes[-1] is False


def test_temp_yaw_180_turns_left_continuously_at_point_eight_and_stops() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    controller, _sensor, safety = make_controller(
        [obs(yaw=0.0), obs(yaw=20.0), obs(yaw=80.0), obs(yaw=177.0)]
    )
    controller.calibrate(samples=2)
    controller.capture_temporary_reference("AVOIDANCE")
    controller.phase = ImuPhase.YAW_STEP_REACHED

    started = controller.start_yaw_scan_360(
        now=now,
        deadline_s=60.0,
        now_monotonic=0.0,
        temporary_reference_name="AVOIDANCE",
    )
    moving = controller.poll_once(now=now, now_monotonic=0.02)
    completed = controller.poll_once(now=now, now_monotonic=0.04)

    assert started["localOperation"] == "YAW_TEMP_180"
    assert started["scanTargetYawDeg"] == pytest.approx(-180.0)
    assert started["scanRequiredClockwiseDeg"] == pytest.approx(160.0)
    assert moving["phase"] == ImuPhase.YAW_SCAN_360
    assert safety.commands[0][:2] == pytest.approx((-0.80, 0.80))
    assert completed["phase"] == ImuPhase.COMPLETED
    assert completed["reason"] == "V2_YAW_TEMP_180_COMPLETED"
    assert safety.armed is False
    assert safety.local_operation is None


def test_yaw_scan_360_ignores_invalid_and_resumes_on_next_valid_sample() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, _sensor, safety = make_controller(
        [obs(), obs(), obs(valid=False), obs(yaw=-1.0), obs(yaw=-1.0), obs(yaw=-2.0)]
    )
    controller.calibrate(samples=2)
    controller.phase = ImuPhase.COMPLETED
    controller.start_yaw_scan_360(now=now, now_monotonic=0.0, deadline_s=45.0)

    paused = controller.poll_once(now=now, now_monotonic=0.02)
    resumed_1 = controller.poll_once(now=now, now_monotonic=0.20)
    resumed_2 = controller.poll_once(now=now, now_monotonic=0.22)
    resumed = controller.poll_once(now=now, now_monotonic=0.24)

    assert paused["phase"] == ImuPhase.YAW_SCAN_360
    assert paused["reason"] == "V2_IMU_INVALID_IGNORED_1"
    assert resumed_1["phase"] == ImuPhase.YAW_SCAN_360
    assert resumed_2["phase"] == ImuPhase.YAW_SCAN_360
    assert resumed["phase"] == ImuPhase.YAW_SCAN_360
    assert safety.armed is True
    assert safety.local_operation == "YAW_SCAN_360"
    assert safety.commands[-1][:2] == pytest.approx((0.80, -0.80))


def test_yaw_scan_360_invalid_imu_does_not_fail_before_operation_deadline() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, _sensor, safety = make_controller(
        [obs(), obs(), obs(valid=False), obs(valid=False)]
    )
    controller.calibrate(samples=2)
    controller.phase = ImuPhase.COMPLETED
    controller.start_yaw_scan_360(now=now, now_monotonic=0.0, deadline_s=45.0)

    controller.poll_once(now=now, now_monotonic=0.02)
    ignored = controller.poll_once(now=now, now_monotonic=10.02)

    assert ignored["phase"] == ImuPhase.YAW_SCAN_360
    assert ignored["reason"] == "V2_IMU_INVALID_IGNORED_2"
    assert safety.armed is True
    assert safety.local_operation == "YAW_SCAN_360"


def test_yaw_scan_360_ignores_pitch_and_roll_above_removed_limits() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, _sensor, safety = make_controller(
        [obs(), obs(), obs(yaw=-10.0, pitch=35.1, roll=-47.0)]
    )
    controller.calibrate(samples=2)
    controller.phase = ImuPhase.COMPLETED
    controller.start_yaw_scan_360(now=now, now_monotonic=0.0, deadline_s=45.0)

    moving = controller.poll_once(now=now, now_monotonic=0.02)

    assert moving["phase"] == ImuPhase.YAW_SCAN_360
    assert moving["pitchDeg"] == pytest.approx(35.1)
    assert moving["rollDeg"] == pytest.approx(-47.0)
    assert safety.commands[-1][:2] == pytest.approx((0.80, -0.80))


def test_yaw_scan_360_sensor_exception_fails_closed_without_retry() -> None:
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    controller, sensor, safety = make_controller([obs(), obs()])
    controller.calibrate(samples=2)
    controller.phase = ImuPhase.COMPLETED
    controller.start_yaw_scan_360(now=now, now_monotonic=0.0, deadline_s=45.0)

    def fail_read():
        raise OSError("i2c offline")

    sensor.read = fail_read
    status = controller.poll_once(now=now, now_monotonic=0.02)

    assert status["phase"] == ImuPhase.ERROR
    assert status["reason"] == "V2_IMU_RUNTIME_EXCEPTION:i2c offline"
    assert safety.armed is False
    assert safety.local_operation is None
