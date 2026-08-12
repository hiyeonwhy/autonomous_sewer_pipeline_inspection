import pytest

from pipecheckkk_ai.control import DriveController
from pipecheckkk_ai.models import (
    DriveObservation,
    LocalPlan,
)








def test_ai_local_plan_becomes_continuous_bounded_differential_drive() -> None:
    controller = DriveController(max_speed=0.90)
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.50,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        steering=0.50,
        throttle=0.40,
        risk=0.70,
        confidence=0.90,
        selected_sector=7,
        candidate_scores=[0.1] * 9,
        smoothed_free_space=[0.2] * 9,
        reason="우측 자유공간 후보가 가장 안전합니다.",
    )

    command = controller.compute_local_plan(observation, plan)

    assert command.action == "AI_LOCAL_PLAN"
    assert command.left_speed == pytest.approx(0.60)
    assert command.right_speed == pytest.approx(0.20)
    assert command.safety_approved is True


def test_ai_local_plan_carries_active_navigation_model_into_drive_command() -> None:
    controller = DriveController(max_speed=0.90)
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.50,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        action="AI_LOCAL_PLAN",
        steering=0.6,
        throttle=0.4,
        risk=0.8,
        confidence=0.9,
        selected_sector=7,
        candidate_scores=[0.2] * 9,
        smoothed_free_space=[0.2] * 9,
        reason="V2 우측 회피",
    )

    command = controller.compute_local_plan(
        observation,
        plan,
        decision_model="depth-anything-plus-pi-imu-v2",
    )

    assert command.decision_model == "depth-anything-plus-pi-imu-v2"


def test_v2_stop_plan_carries_active_navigation_model_into_drive_command() -> None:
    controller = DriveController()
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.05,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        action="STOP",
        steering=0.0,
        throttle=0.0,
        risk=1.0,
        confidence=0.9,
        selected_sector=4,
        candidate_scores=[0.2] * 9,
        smoothed_free_space=[0.2] * 9,
        reason="접촉 거리에서 전진 금지",
    )

    command = controller.compute_local_plan(
        observation,
        plan,
        decision_model="depth-anything-plus-pi-imu-v2",
    )

    assert command.action == "STOP"
    assert command.left_speed == 0.0
    assert command.right_speed == 0.0
    assert command.decision_model == "depth-anything-plus-pi-imu-v2"


def test_close_ultrasonic_reading_does_not_override_a_moving_local_plan() -> None:
    controller = DriveController(max_speed=0.90)
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.01,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        steering=0.0,
        throttle=0.30,
        risk=0.20,
        confidence=0.90,
        selected_sector=4,
        candidate_scores=[0.8] * 9,
        smoothed_free_space=[0.9] * 9,
        reason="카메라 자유공간이 열려 있습니다.",
    )

    command = controller.compute_local_plan(observation, plan)

    assert command.action == "AI_LOCAL_PLAN"
    assert command.left_speed == pytest.approx(0.30)
    assert command.right_speed == pytest.approx(0.30)
    assert command.safety_approved is True


def test_ai_local_plan_stop_is_not_forwarded_as_an_approved_drive() -> None:
    controller = DriveController()
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.04,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        action="STOP",
        steering=0.0,
        throttle=0.0,
        risk=1.0,
        confidence=0.9,
        selected_sector=4,
        candidate_scores=[0.0] * 9,
        smoothed_free_space=[1.0] * 9,
        reason="초음파 하드 STOP",
    )

    command = controller.compute_local_plan(observation, plan)

    assert command.action == "STOP"
    assert command.left_speed == 0.0
    assert command.right_speed == 0.0
    assert command.safety_approved is False




















def test_v2_pitch_advance_preserves_equal_positive_magnitudes() -> None:
    throttle = 0.50
    controller = DriveController(max_speed=0.90)
    observation = DriveObservation(
        center_error=0.0,
        perception_confidence=0.9,
        frame_age_s=0.0,
        ultrasonic_distance_m=0.50,
        perception_model="depth-freespace",
    )
    plan = LocalPlan(
        action="V2_PITCH_ADVANCE",
        steering=0.0,
        throttle=throttle,
        risk=0.70,
        confidence=0.90,
        selected_sector=4,
        candidate_scores=[0.1] * 9,
        smoothed_free_space=[0.2] * 9,
        reason="V2 test",
    )

    command = controller.compute_local_plan(observation, plan)

    assert command.action == "V2_PITCH_ADVANCE"
    assert command.left_speed == pytest.approx(throttle)
    assert command.right_speed == pytest.approx(throttle)
