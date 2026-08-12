import pytest

from pipecheckkk_ai.local_planner import LocalFreeSpacePlanner
from pipecheckkk_ai.models import PerceptionResult


def perception(
    profile: list[float],
    *,
    blocked: float,
    confidence: float = 0.90,
) -> PerceptionResult:
    third = len(profile) // 3
    return PerceptionResult(
        model_name="depth-freespace",
        backend="relative-depth-freespace",
        center_error=0.0,
        confidence=confidence,
        latency_ms=30.0,
        image_width=640,
        image_height=480,
        safe_for_control=True,
        path_blocked_probability=blocked,
        left_free_score=sum(profile[:third]) / third,
        center_free_score=sum(profile[third : 2 * third]) / third,
        right_free_score=sum(profile[2 * third :]) / third,
        free_space_profile=profile,
        note="test",
    )


def test_planner_drives_fast_and_centered_when_depth_model_sees_open_corridor() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception([0.55, 0.70, 0.82, 0.92, 1.0, 0.92, 0.82, 0.70, 0.55], blocked=0.05),
        ultrasonic_distance_m=1.2,
    )

    assert abs(plan.steering) < 0.08
    assert plan.throttle == pytest.approx(0.40)
    assert plan.selected_sector == 4
    assert plan.action == "AI_LOCAL_PLAN"


@pytest.mark.parametrize("distance", [float("nan"), float("inf"), -0.01, 0.05])
def test_local_planner_fails_closed_for_invalid_or_hard_stop_ultrasonic(distance) -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception([0.8] * 9, blocked=0.05),
        ultrasonic_distance_m=distance,
    )

    assert plan.action == "STOP"
    assert plan.throttle == 0.0
    assert plan.steering == 0.0


def test_local_planner_does_not_stop_for_distance_just_above_five_centimeters() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception([0.8] * 9, blocked=0.05),
        ultrasonic_distance_m=0.051,
    )

    assert plan.action == "AI_LOCAL_PLAN"
    assert plan.throttle == pytest.approx(0.40)


def test_planner_uses_full_depth_profile_to_steer_toward_open_right_side() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception([0.08, 0.10, 0.15, 0.12, 0.10, 0.35, 0.72, 0.92, 0.88], blocked=0.90),
        ultrasonic_distance_m=0.35,
    )

    assert plan.steering >= 0.25
    assert 0.0 < plan.throttle < 0.55
    assert plan.selected_sector >= 6
    assert plan.risk > 0.70


def test_high_blockage_commits_to_one_open_side_with_escape_torque() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception(
            [0.54, 0.64, 0.66, 0.05, 0.01, 0.47, 0.64, 0.56, 0.39],
            blocked=0.83,
        ),
        ultrasonic_distance_m=None,
    )

    assert plan.selected_sector in {0, 1, 2, 6, 7, 8}
    assert abs(plan.steering) == pytest.approx(1.0)
    assert plan.throttle == pytest.approx(0.50)
    assert "강제 회피" in plan.reason


def test_v1_starts_forced_avoidance_at_fifty_percent_vision_risk() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception(
            [0.54, 0.64, 0.66, 0.05, 0.01, 0.47, 0.64, 0.56, 0.39],
            blocked=0.50,
        ),
        ultrasonic_distance_m=None,
    )

    assert abs(plan.steering) == pytest.approx(1.0)
    assert "강제 회피" in plan.reason


def test_planner_recovers_from_full_escape_turn_to_open_center() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0, maximum_steering_step=0.25)
    blocked = perception(
        [0.54, 0.64, 0.66, 0.05, 0.01, 0.47, 0.64, 0.56, 0.39],
        blocked=0.83,
    )
    open_center = perception(
        [0.45, 0.58, 0.72, 0.90, 1.0, 0.90, 0.72, 0.58, 0.45],
        blocked=0.05,
    )

    escape = planner.plan(blocked, ultrasonic_distance_m=None)
    recovery = [
        planner.plan(open_center, ultrasonic_distance_m=None)
        for _ in range(5)
    ]

    assert abs(escape.steering) == pytest.approx(1.0)
    assert all(
        abs(current.steering) <= abs(previous.steering)
        for previous, current in zip([escape, *recovery[:-1]], recovery, strict=True)
    )
    assert recovery[-1].selected_sector == 4
    assert abs(recovery[-1].steering) < 0.08
    assert recovery[-1].throttle == pytest.approx(0.40)


def test_temporal_depth_memory_prevents_a_single_frame_left_right_flip() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=0.25, maximum_steering_step=0.25)
    right_open = perception([0.05, 0.10, 0.15, 0.18, 0.10, 0.30, 0.75, 0.95, 0.90], blocked=0.88)
    left_open = perception(list(reversed(right_open.free_space_profile)), blocked=0.88)

    first = planner.plan(right_open, ultrasonic_distance_m=0.40)
    second = planner.plan(left_open, ultrasonic_distance_m=0.40)

    assert first.steering > 0
    assert second.steering >= 0
    assert abs(second.steering - first.steering) <= 0.25 + 1e-9


def test_v1_cruise_throttle_stays_constant_across_safe_confidence_changes() -> None:
    confident_planner = LocalFreeSpacePlanner(ema_alpha=1.0)
    uncertain_planner = LocalFreeSpacePlanner(ema_alpha=1.0)
    profile = [0.65, 0.75, 0.85, 0.90, 0.95, 0.90, 0.85, 0.75, 0.65]

    confident = confident_planner.plan(perception(profile, blocked=0.10, confidence=0.95), ultrasonic_distance_m=None)
    uncertain = uncertain_planner.plan(perception(profile, blocked=0.10, confidence=0.62), ultrasonic_distance_m=None)

    assert confident.throttle == pytest.approx(0.40)
    assert uncertain.throttle == pytest.approx(0.40)
    assert uncertain.confidence == pytest.approx(0.62)


def test_close_ultrasonic_reading_forces_hard_stop() -> None:
    planner = LocalFreeSpacePlanner(ema_alpha=1.0)

    plan = planner.plan(
        perception([0.9] * 9, blocked=0.0),
        ultrasonic_distance_m=0.04,
    )

    assert plan.throttle == 0.0
    assert plan.action == "STOP"
