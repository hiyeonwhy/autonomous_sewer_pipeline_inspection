import cv2
import numpy as np
from pathlib import Path

from pipecheckkk_ai.perception.depth_freespace.adapter import DepthFreeSpaceProvider
from pipecheckkk_ai.local_planner import LocalFreeSpacePlanner
from pipecheckkk_ai.navigation import V2ImuNavigationPlanner


def _jpeg() -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.full((90, 90, 3), 128, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def test_depth_freespace_reports_center_obstacle_and_open_left_side() -> None:
    depth = np.full((90, 90), 0.10, dtype=np.float32)
    depth[25:90, 30:60] = 0.95
    provider = DepthFreeSpaceProvider(depth_estimator=lambda _frame: depth)

    result = provider.analyze(_jpeg())

    assert result.path_blocked_probability >= 0.80
    assert result.left_free_score > result.center_free_score
    assert result.left_free_score == result.right_free_score
    assert result.safe_for_control is True


def test_depth_freespace_prefers_right_when_left_side_is_near() -> None:
    depth = np.full((90, 90), 0.10, dtype=np.float32)
    depth[25:90, 0:30] = 0.95
    provider = DepthFreeSpaceProvider(depth_estimator=lambda _frame: depth)

    result = provider.analyze(_jpeg())

    assert result.right_free_score > result.left_free_score
    assert result.center_error > 0


def test_v2_turns_right_after_stopped_depth_frames_show_left_obstacle() -> None:
    depth = np.full((90, 90), 0.10, dtype=np.float32)
    depth[25:90, 0:30] = 0.95
    provider = DepthFreeSpaceProvider(depth_estimator=lambda _frame: depth)
    perception = provider.analyze(_jpeg())
    planner = V2ImuNavigationPlanner(LocalFreeSpacePlanner())

    plans = [
        planner.plan(
            perception,
            ultrasonic_distance_m=0.05,
            imu_status={"phase": "READY", "yawDeg": 0.0},
        )
        for index in range(3)
    ]

    assert perception.left_free_score < perception.right_free_score
    assert [plan.action for plan in plans] == [
        "V2_DIRECTION_ASSESS",
        "V2_DIRECTION_ASSESS",
        "V2_YAW_STEP_RIGHT",
    ]


def test_depth_freespace_can_be_configured_with_local_model_directory() -> None:
    provider = DepthFreeSpaceProvider(model_dir=Path("models/depth-anything-v2-small/weights"))

    assert provider.name == "depth-freespace"
