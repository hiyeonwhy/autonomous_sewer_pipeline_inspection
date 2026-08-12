import pytest

from pipecheckkk_ai.models import LocalPlan, PerceptionResult
from pipecheckkk_ai.navigation import V2ImuNavigationPlanner


class FakeDepthPlanner:
    def reset(self):
        pass

    def plan(self, perception, *, ultrasonic_distance_m=None):
        if perception.path_blocked_probability >= 0.50:
            return LocalPlan(
                steering=-1.0,
                throttle=0.50,
                confidence=0.90,
                risk=perception.path_blocked_probability,
                action="AI_LOCAL_ESCAPE_LEFT",
                reason="Depth obstacle",
                selected_sector=0,
                candidate_scores=[0.8] * 9,
                smoothed_free_space=[0.8] * 9,
            )
        return LocalPlan(
            steering=0.0,
            throttle=0.40,
            confidence=0.90,
            risk=perception.path_blocked_probability,
            action="AI_LOCAL_PLAN",
            reason="Depth clear",
            selected_sector=4,
            candidate_scores=[0.8] * 9,
            smoothed_free_space=[0.8] * 9,
        )


def perception(risk, *, left=0.8, center=0.8, right=0.8):
    return PerceptionResult(
        model_name="depth-freespace",
        backend="fake",
        center_error=0.0,
        confidence=0.90,
        latency_ms=1.0,
        image_width=640,
        image_height=480,
        safe_for_control=True,
        path_blocked_probability=risk,
        left_free_score=left,
        center_free_score=center,
        right_free_score=right,
        free_space_profile=[left] * 3 + [center] * 3 + [right] * 3,
        note="test",
    )


def trigger_avoidance(
    planner: V2ImuNavigationPlanner,
    sample: PerceptionResult | None = None,
) -> list[LocalPlan]:
    observed = sample or perception(0.70)
    return [
        planner.plan(
            observed,
            ultrasonic_distance_m=0.05,
            imu_status={"phase": "READY", "yawDeg": 0.0},
        )
        for index in range(3)
    ]


def test_v2_missing_ultrasonic_does_not_stop_an_otherwise_safe_search() -> None:
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())

    plan = planner.plan(
        perception(0.20),
        ultrasonic_distance_m=None,
        imu_status={"phase": "READY", "yawDeg": 0.0},
    )

    assert plan.action == "AI_LOCAL_PLAN"
    assert plan.throttle == 0.40
    assert "초음파 관측 없음" in plan.reason


@pytest.mark.parametrize("invalid_distance", [float("nan"), float("inf"), -0.01])
def test_v2_invalid_ultrasonic_fails_closed(invalid_distance) -> None:
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())

    plan = planner.plan(
        perception(0.20),
        ultrasonic_distance_m=invalid_distance,
        imu_status={"phase": "READY", "yawDeg": 0.0},
    )

    assert plan.action == "STOP"
    assert plan.throttle == 0.0
    assert "초음파" in plan.reason


def test_v2_normal_search_triggers_recovery_at_fifteen_degree_yaw_drift() -> None:
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())

    assert planner.should_trigger_yaw_drift_recovery(14.99, imu_phase="READY") is False
    assert planner.should_trigger_yaw_drift_recovery(15.0, imu_phase="READY") is True
    assert planner.should_trigger_yaw_drift_recovery(-15.0, imu_phase="READY") is True

    planner.begin_yaw_drift_recovery()
    assert planner.phase.value == "LOCAL_RECOVERY"
    planner.resume_after_recovery()
    assert planner.phase.value == "SEARCH"


def test_v2_does_not_retrigger_yaw_drift_recovery_during_obstacle_avoidance() -> None:
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    planner.plan(
        perception(0.90),
        ultrasonic_distance_m=0.05,
        imu_status={"phase": "READY", "yawDeg": 0.0},
    )

    assert planner.should_trigger_yaw_drift_recovery(20.0, imu_phase="READY") is False


def test_v2_requests_pi_local_ten_degree_steps_and_waits_while_pi_turns():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())

    normal = planner.plan(
        perception(0.90, left=0.2, center=0.2, right=0.8),
        ultrasonic_distance_m=0.16,
        imu_status={"phase": "READY"},
    )
    direction_plans = trigger_avoidance(
        planner,
        perception(0.90, left=0.2, center=0.2, right=0.8),
    )
    first_step = direction_plans[-1]
    moving = planner.plan(
        perception(0.70),
        ultrasonic_distance_m=0.05,
        imu_status={"phase": "PIVOT_YAW", "yawDeg": -5.0, "yawTargetDeg": -10.0},
    )
    second_step = planner.plan(
        perception(0.70),
        ultrasonic_distance_m=0.50,
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": -10.0, "yawTargetDeg": -10.0},
    )

    assert normal.action == "AI_LOCAL_PLAN"
    assert normal.steering == 0.0
    assert normal.throttle == 0.40
    assert [plan.action for plan in direction_plans[:2]] == [
        "V2_DIRECTION_ASSESS",
        "V2_DIRECTION_ASSESS",
    ]
    assert first_step.action == "V2_YAW_STEP_RIGHT"
    assert first_step.throttle == 0.0
    assert moving.action == "V2_YAW_STEP_WAIT"
    assert moving.throttle == 0.0
    assert second_step.action == "V2_YAW_STEP_RIGHT"
    assert "-20°" in second_step.reason


def test_v2_continues_ten_degree_yaw_steps_after_thirty_degrees():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)
    reached = {"phase": "YAW_STEP_REACHED", "yawDeg": 30.0, "yawTargetDeg": 30.0}

    fourth_step = planner.plan(perception(0.20), imu_status=reached)

    assert fourth_step.action == "V2_YAW_STEP_LEFT"
    assert fourth_step.throttle == 0.0
    assert "40°" in fourth_step.reason
    assert "10°" in fourth_step.reason


def test_v2_keeps_all_yaw_steps_at_ten_degrees_without_a_limit():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)

    second_step = planner.plan(
        perception(0.20),
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": 10.0, "yawTargetDeg": 10.0},
    )
    third_step = planner.plan(
        perception(0.20),
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": 20.0, "yawTargetDeg": 20.0},
    )
    fourth_step = planner.plan(
        perception(0.20),
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": 30.0, "yawTargetDeg": 30.0},
    )

    assert "20°" in second_step.reason
    assert "30°" in third_step.reason
    assert "40°" in fourth_step.reason
    assert all(plan.action == "V2_YAW_STEP_LEFT" for plan in (second_step, third_step, fourth_step))


def test_v2_keeps_rotating_past_thirty_five_degrees_if_obstacle_is_visible():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)

    plan = planner.plan(
        perception(0.90, left=0.2, center=0.2, right=0.2),
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": 40.0, "yawTargetDeg": 40.0},
    )

    assert plan.action == "V2_YAW_STEP_LEFT"
    assert plan.throttle == 0.0
    assert "50°" in plan.reason


def test_v2_rejects_pi_reported_finite_yaw_limit():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)

    plan = planner.plan(
        perception(0.90, left=0.2, center=0.2, right=0.2),
        imu_status={
            "phase": "YAW_STEP_REACHED",
            "yawDeg": 30.0,
            "yawTargetDeg": 30.0,
            "yawStepDeg": 10.0,
            "yawLimitDeg": 40.0,
        },
    )

    assert plan.action == "STOP"
    assert plan.throttle == 0.0
    assert "yaw 설정" in plan.reason


def _enter_pitch_advance_after_visual_clear(planner: V2ImuNavigationPlanner) -> None:
    reached = {
        "phase": "YAW_STEP_REACHED",
        "yawDeg": 40.0,
        "yawTargetDeg": 40.0,
        "yawStepDeg": 10.0,
        "yawLimitDeg": None,
    }
    for _ in range(3):
        plan = planner.plan(perception(0.20), imu_status=reached)
    assert plan.action == "V2_PITCH_ADVANCE"


def test_v2_pitch_advance_status_reports_thirty_five_degree_stop():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)

    _enter_pitch_advance_after_visual_clear(planner)
    plan = planner.plan(
        perception(0.20),
        imu_status={"phase": "PITCH_GUARD", "pitchDeg": 2.0},
    )

    assert plan.action == "V2_PITCH_ADVANCE"
    assert "pitch 35°" in plan.reason


def test_v2_visual_clear_never_starts_recovery_before_pitch_limit():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)
    _enter_pitch_advance_after_visual_clear(planner)
    pitch_guard = {"phase": "PITCH_GUARD", "pitchDeg": 2.0}

    still_advancing = planner.plan(perception(0.80, left=0.2), imu_status=pitch_guard)
    clear_plans = [planner.plan(perception(0.20), imu_status=pitch_guard) for _ in range(10)]
    recover = planner.plan(
        perception(0.20),
        imu_status={"phase": "PITCH_LIMIT_REACHED", "pitchDeg": 35.0},
    )

    assert still_advancing.action == "V2_PITCH_ADVANCE"
    assert all(plan.action == "V2_PITCH_ADVANCE" for plan in clear_plans)
    assert all(plan.throttle == 0.50 for plan in clear_plans)
    assert recover.action == "V2_LOCAL_RECOVERY"


def test_v2_roll_limit_starts_same_local_recovery_sequence():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)
    planner.plan(
        perception(0.90),
        imu_status={"phase": "YAW_STEP_REACHED", "yawDeg": 30.0, "yawTargetDeg": 30.0},
    )

    recover = planner.plan(
        perception(0.20),
        imu_status={"phase": "ROLL_LIMIT_REACHED", "rollDeg": 45.0},
    )

    assert recover.action == "V2_LOCAL_RECOVERY"
    assert recover.throttle == 0.0


def test_v2_recovery_completion_resumes_search_and_allows_next_obstacle_cycle():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)

    local = planner.plan(
        perception(0.20),
        imu_status={"phase": "PITCH_LIMIT_REACHED", "pitchDeg": 35.0},
    )
    recovering = planner.plan(
        perception(0.20),
        imu_status={"phase": "ROLL_RECOVERY", "rollErrorDeg": 8.0},
    )
    completed = planner.plan(
        perception(0.20),
        ultrasonic_distance_m=0.10,
        imu_status={"phase": "COMPLETED", "yawErrorDeg": 0.5},
    )
    planner.resume_after_recovery()
    resumed = planner.plan(
        perception(0.90), ultrasonic_distance_m=0.30,
        imu_status={"phase": "READY"},
    )
    next_cycle = trigger_avoidance(
        planner,
        perception(0.90, left=0.8, center=0.2, right=0.2),
    )

    assert local.action == "V2_LOCAL_RECOVERY"
    assert recovering.action == "V2_LOCAL_RECOVERY"
    assert completed.action == "V2_RECOVERY_CYCLE_COMPLETE"
    assert completed.throttle == 0.0
    assert resumed.action == "AI_LOCAL_PLAN"
    assert resumed.throttle == 0.40
    assert next_cycle[0].action == "V2_DIRECTION_ASSESS"
    assert next_cycle[-1].action == "V2_YAW_STEP_LEFT"


def test_v2_reset_clears_yaw_advance_and_terminal_state():
    planner = V2ImuNavigationPlanner(FakeDepthPlanner())
    trigger_avoidance(planner)
    planner.plan(perception(0.20), imu_status={"phase": "COMPLETED"})

    planner.reset()
    plan = planner.plan(
        perception(0.20), ultrasonic_distance_m=0.50,
        imu_status={"phase": "READY"},
    )

    assert plan.action == "AI_LOCAL_PLAN"
    assert plan.throttle == 0.40
