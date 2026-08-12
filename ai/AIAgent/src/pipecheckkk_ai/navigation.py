from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import RLock
from typing import Protocol

from pipecheckkk_ai.models import LocalPlan, PerceptionResult


class NavigationVersion(StrEnum):
    V2 = "v2"


class Planner(Protocol):
    def reset(self) -> None: ...

    def plan(
        self,
        perception: PerceptionResult,
        *,
        ultrasonic_distance_m: float | None,
    ) -> LocalPlan: ...


@dataclass(frozen=True)
class NavigationStatus:
    active_version: NavigationVersion = NavigationVersion.V2
    label: str = "V2 Depth + Pi IMU"


class V2Phase(StrEnum):
    SEARCH = "SEARCH"
    DIRECTION_ASSESS = "DIRECTION_ASSESS"
    PIVOT = "PIVOT"
    PITCH_ADVANCE = "PITCH_ADVANCE"
    LOCAL_RECOVERY = "LOCAL_RECOVERY"
    COMPLETED = "COMPLETED"


class V2ImuNavigationPlanner:
    """Depth free-space planning with Pi-local IMU recovery."""

    RECOVERY_PHASES = {"PRIMARY_YAW", "ROLL_RECOVERY", "FINAL_YAW"}

    def __init__(
        self,
        base: Planner,
        *,
        risk_threshold: float = 0.50,
        obstacle_trigger_m: float = 0.05,
        cruise_throttle: float = 0.40,
        pivot_throttle: float = 0.65,
        advance_throttle: float = 0.50,
        clear_confirmation_frames: int = 3,
        direction_confirmation_frames: int = 3,
        yaw_step_deg: float = 10.0,
        minimum_yaw_before_clear_deg: float = 35.0,
        yaw_limit_deg: None = None,
        yaw_drift_recovery_deg: float = 15.0,
    ) -> None:
        if not 0 < risk_threshold <= 1:
            raise ValueError("V2 risk threshold must be in (0, 1]")
        if obstacle_trigger_m <= 0:
            raise ValueError("V2 ultrasonic obstacle trigger must be positive")
        if not 0 < cruise_throttle <= 0.40:
            raise ValueError("V2 cruise throttle must be in (0, 0.40]")
        if not 0 < pivot_throttle <= 0.70:
            raise ValueError("V2 pivot throttle must be in (0, 0.70]")
        if not 0 < advance_throttle <= 0.50:
            raise ValueError("V2 advance throttle must be in (0, 0.50]")
        if clear_confirmation_frames < 1:
            raise ValueError("V2 clear confirmation frames must be positive")
        if direction_confirmation_frames < 1:
            raise ValueError("V2 direction confirmation frames must be positive")
        if not isfinite(yaw_drift_recovery_deg) or not 0 < yaw_drift_recovery_deg <= 30.0:
            raise ValueError("V2 yaw drift recovery threshold must be in (0, 30]")
        if (
            yaw_step_deg <= 0
            or minimum_yaw_before_clear_deg < yaw_step_deg
            or (yaw_step_deg, minimum_yaw_before_clear_deg) != (10.0, 35.0)
            or yaw_limit_deg is not None
        ):
            raise ValueError("V2 yaw contract requires 10-degree steps without a maximum limit")
        self._base = base
        self._risk_threshold = risk_threshold
        self._obstacle_trigger_m = obstacle_trigger_m
        self._cruise_throttle = cruise_throttle
        self._pivot_throttle = pivot_throttle
        self._advance_throttle = advance_throttle
        self._clear_confirmation_frames = clear_confirmation_frames
        self._direction_confirmation_frames = direction_confirmation_frames
        self._yaw_step_deg = yaw_step_deg
        self._minimum_yaw_before_clear_deg = minimum_yaw_before_clear_deg

        self._yaw_drift_recovery_deg = yaw_drift_recovery_deg
        self._phase = V2Phase.SEARCH
        self._direction = 0
        self._clear_frames = 0
        self._direction_frames = 0
        self._left_free_sum = 0.0
        self._right_free_sum = 0.0
        self._terminal: LocalPlan | None = None

    @property
    def phase(self) -> V2Phase:
        return self._phase

    def reset(self) -> None:
        self._phase = V2Phase.SEARCH
        self._direction = 0
        self._clear_frames = 0
        self._reset_direction_assessment()
        self._terminal = None

    def resume_after_recovery(self) -> None:
        if self._phase not in {
            V2Phase.PITCH_ADVANCE,
            V2Phase.LOCAL_RECOVERY,
            V2Phase.COMPLETED,
        }:
            raise RuntimeError(f"cannot resume V2 search from {self._phase}")
        self._phase = V2Phase.SEARCH
        self._direction = 0
        self._clear_frames = 0
        self._reset_direction_assessment()
        self._terminal = None

    def should_preempt_for_obstacle(self, ultrasonic_distance_m: float | None) -> bool:
        return (
            self._terminal is None
            and self._phase is V2Phase.SEARCH
            and ultrasonic_distance_m is not None
            and isfinite(ultrasonic_distance_m)
            and ultrasonic_distance_m <= self._obstacle_trigger_m
        )

    def should_trigger_yaw_drift_recovery(
        self,
        yaw_deg: float | None,
        *,
        imu_phase: str,
    ) -> bool:
        return (
            self._terminal is None
            and self._phase is V2Phase.SEARCH
            and imu_phase == "READY"
            and yaw_deg is not None
            and isfinite(yaw_deg)
            and abs(yaw_deg) >= self._yaw_drift_recovery_deg
        )

    def begin_yaw_drift_recovery(self) -> None:
        if self._terminal is not None or self._phase is not V2Phase.SEARCH:
            raise RuntimeError(f"cannot start V2 yaw drift recovery from {self._phase}")
        self._phase = V2Phase.LOCAL_RECOVERY

    def plan(
        self,
        perception: PerceptionResult,
        *,
        ultrasonic_distance_m: float | None = None,
        imu_status: dict[str, object] | None = None,
    ) -> LocalPlan:
        if self._terminal is not None:
            return self._terminal.model_copy(deep=True)
        ultrasonic_invalid = ultrasonic_distance_m is not None and (
            not isfinite(ultrasonic_distance_m) or ultrasonic_distance_m < 0.0
        )
        base = self._base.plan(
            perception,
            ultrasonic_distance_m=(
                None
                if ultrasonic_invalid
                or (
                    ultrasonic_distance_m is not None
                    and ultrasonic_distance_m <= self._obstacle_trigger_m
                )
                else ultrasonic_distance_m
            ),
        )
        if ultrasonic_invalid:
            return self._latch_stop(base, "V2 초음파 관측값이 유한한 비음수가 아니므로 안전 정지합니다.")
        imu = imu_status or {}
        if not self._yaw_configuration_matches_contract(imu):
            return self._latch_stop(base, "Pi V2 yaw 설정이 고정 10°/35° 계약과 달라 안전 정지합니다.")
        imu_phase = str(imu.get("phase", "UNAVAILABLE"))
        if imu_phase in {"ERROR", "UNAVAILABLE"}:
            return self._latch_stop(base, f"V2 IMU 상태가 {imu_phase}이므로 안전 정지합니다.")
        if not perception.safe_for_control or base.action == "STOP":
            return self._latch_stop(base, "V2 Depth 안전 게이트가 주행을 승인하지 않아 정지합니다.")
        if imu_phase == "COMPLETED":
            self._phase = V2Phase.COMPLETED
            return base.model_copy(deep=True, update={
                "action": "V2_RECOVERY_CYCLE_COMPLETE", "steering": 0.0,
                "throttle": 0.0, "selected_sector": 4,
                "reason": "V2 yaw→roll→yaw 경로 복귀 완료. IMU 기준 재설정 후 정상 탐사를 재개합니다.",
            })
        if imu_phase in {"PITCH_LIMIT_REACHED", "ROLL_LIMIT_REACHED"} or imu_phase in self.RECOVERY_PHASES:
            self._phase = V2Phase.LOCAL_RECOVERY
            return base.model_copy(deep=True, update={
                "action": "V2_LOCAL_RECOVERY", "steering": 0.0, "throttle": 0.0,
                "selected_sector": 4,
                "reason": f"Pi 50 Hz IMU 경로 복귀 진행 중입니다: {imu_phase}.",
            })
        if self._phase is V2Phase.LOCAL_RECOVERY:
            return self._latch_stop(base, f"V2 local 복귀 상태가 예기치 않게 {imu_phase}로 변경됐습니다.")

        blocked = perception.path_blocked_probability >= self._risk_threshold
        obstacle_visible = min(
            perception.left_free_score,
            perception.center_free_score,
            perception.right_free_score,
        ) < self._risk_threshold
        if imu_phase == "PIVOT_YAW":
            self._phase = V2Phase.PIVOT
            return base.model_copy(deep=True, update={
                "action": "V2_YAW_STEP_WAIT", "steering": 0.0, "throttle": 0.0,
                "selected_sector": 4,
                "reason": f"Pi가 yaw {imu.get('yawTargetDeg')}° target까지 50 Hz로 제자리회전 중입니다.",
            })
        if self._phase is V2Phase.SEARCH:
            if ultrasonic_distance_m is None:
                return base.model_copy(deep=True, update={
                    "action": "AI_LOCAL_PLAN", "steering": 0.0,
                    "throttle": self._cruise_throttle, "selected_sector": 4,
                    "reason": (
                        "V2 정상주행: 초음파 관측 없음. "
                        "Depth와 IMU 안전 게이트로 주행을 계속합니다."
                    ),
                })
            if ultrasonic_distance_m > self._obstacle_trigger_m:
                return self._cruise_plan(base, ultrasonic_distance_m)
            self._phase = V2Phase.DIRECTION_ASSESS
            self._clear_frames = 0
            self._reset_direction_assessment()
        if self._phase is V2Phase.DIRECTION_ASSESS:
            if imu_phase != "READY":
                return self._latch_stop(
                    base,
                    f"V2 회피 방향 정지 평가 중 IMU 상태가 올바르지 않습니다: {imu_phase}.",
                )
            self._direction_frames += 1
            self._left_free_sum += perception.left_free_score
            self._right_free_sum += perception.right_free_score
            if self._direction_frames < self._direction_confirmation_frames:
                return base.model_copy(deep=True, update={
                    "action": "V2_DIRECTION_ASSESS", "steering": 0.0,
                    "throttle": 0.0, "selected_sector": 4,
                    "reason": "초음파 10cm 회피 trigger 후 완전 정지하여 카메라 좌·우 "
                    f"회피공간을 확인 중입니다: {self._direction_frames}/"
                    f"{self._direction_confirmation_frames} 프레임.",
                })
            self._direction = -1 if self._left_free_sum >= self._right_free_sum else 1
            self._phase = V2Phase.PIVOT
        if self._phase is V2Phase.PIVOT:
            if imu_phase not in {"READY", "YAW_STEP_REACHED"}:
                return self._latch_stop(base, f"V2 yaw step 상태가 올바르지 않습니다: {imu_phase}.")
            target = imu.get("yawTargetDeg")
            target_deg = float(target) if isinstance(target, (int, float)) else 0.0
            if abs(target_deg) < self._minimum_yaw_before_clear_deg:
                self._clear_frames = 0
                return self._yaw_step_request(base, target_deg)
            if imu_phase == "READY" or blocked or obstacle_visible:
                self._clear_frames = 0
                return self._yaw_step_request(base, target_deg)
            self._clear_frames += 1
            if self._clear_frames < self._clear_confirmation_frames:
                return base.model_copy(deep=True, update={
                    "action": "V2_YAW_ASSESS", "steering": 0.0, "throttle": 0.0,
                    "selected_sector": 4,
                    "reason": "10° yaw step 정지 후 카메라 전체 clear를 확인 중입니다: "
                    f"{self._clear_frames}/{self._clear_confirmation_frames} 프레임.",
                })
            self._phase = V2Phase.PITCH_ADVANCE
            return self._advance_plan(base)
        if self._phase is V2Phase.PITCH_ADVANCE:
            if imu_phase not in {"YAW_STEP_REACHED", "PITCH_GUARD"}:
                return self._latch_stop(base, f"V2 pitch guard 상태가 올바르지 않습니다: {imu_phase}.")
            return self._advance_plan(base)
        return self._latch_stop(base, "V2 알 수 없는 상태로 안전 정지합니다.")

    def _yaw_configuration_matches_contract(self, imu: dict[str, object]) -> bool:
        reported_step = imu.get("yawStepDeg")
        reported_limit = imu.get("yawLimitDeg")
        if reported_step is None and reported_limit is None:
            return True
        if (
            not isinstance(reported_step, (int, float))
            or isinstance(reported_step, bool)
        ):
            return False
        step = float(reported_step)
        return isfinite(step) and step == 10.0 and reported_limit is None

    def _yaw_step_request(self, base: LocalPlan, current_target_deg: float) -> LocalPlan:
        direction = "LEFT" if self._direction < 0 else "RIGHT"
        signed_step = self._yaw_step_deg if self._direction < 0 else -self._yaw_step_deg
        next_target = current_target_deg + signed_step
        actual_step = abs(next_target - current_target_deg)
        return base.model_copy(deep=True, update={
            "action": f"V2_YAW_STEP_{direction}", "steering": 0.0, "throttle": 0.0,
            "selected_sector": 1 if self._direction < 0 else 7,
            "reason": f"Pi에 {direction} 방향 누적 yaw {next_target:g}° target의 다음 "
            f"{actual_step:g}° 제자리회전을 요청합니다.",
        })

    def _advance_plan(self, base: LocalPlan) -> LocalPlan:
        prefix = "정지 상태 카메라 재판단에서 회전 회피가 확인되어"
        return base.model_copy(deep=True, update={
            "action": "V2_PITCH_ADVANCE", "steering": 0.0,
            "throttle": self._advance_throttle, "selected_sector": 4,
            "reason": f"{prefix} 좌우 {self._advance_throttle:.2f}로 직진합니다. "
            "Pi pitch 35° 도달 시 정지 후 반드시 경로 복귀합니다.",
        })

    def _reset_direction_assessment(self) -> None:
        self._direction_frames = 0
        self._left_free_sum = 0.0
        self._right_free_sum = 0.0

    def _cruise_plan(self, base: LocalPlan, ultrasonic_distance_m: float) -> LocalPlan:
        return base.model_copy(deep=True, update={
            "action": "AI_LOCAL_PLAN", "steering": 0.0,
            "throttle": self._cruise_throttle, "selected_sector": 4,
            "reason": (
                f"V2 정상주행: 전방 초음파 {ultrasonic_distance_m:.3f}m가 "
                f"회피 trigger {self._obstacle_trigger_m:.2f}m보다 멀어 좌우 "
                f"{self._cruise_throttle:.2f}로 직진합니다."
            ),
        })

    def _latch_stop(self, base: LocalPlan, reason: str) -> LocalPlan:
        stopped = base.model_copy(
            deep=True,
            update={
                "action": "STOP",
                "steering": 0.0,
                "throttle": 0.0,
                "selected_sector": 4,
                "reason": reason,
            },
        )
        self._terminal = stopped
        return stopped


class NavigationRouter:
    """Single-runtime wrapper for the production V2 navigation policy."""

    def __init__(self, *, base: Planner) -> None:
        self._v2 = V2ImuNavigationPlanner(base)
        self._lock = RLock()

    @property
    def active_version(self) -> NavigationVersion:
        return NavigationVersion.V2

    @property
    def decision_model(self) -> str:
        return "depth-anything-plus-pi-imu-v2"

    def status(self) -> NavigationStatus:
        return NavigationStatus()

    def reset(self) -> None:
        with self._lock:
            self._v2.reset()

    def resume_v2_after_recovery(self) -> None:
        with self._lock:
            self._v2.resume_after_recovery()

    def should_preempt_v2_obstacle(self, ultrasonic_distance_m: float | None) -> bool:
        with self._lock:
            return self._v2.should_preempt_for_obstacle(ultrasonic_distance_m)

    def should_trigger_v2_yaw_drift_recovery(
        self,
        yaw_deg: float | None,
        *,
        imu_phase: str,
    ) -> bool:
        with self._lock:
            return self._v2.should_trigger_yaw_drift_recovery(yaw_deg, imu_phase=imu_phase)

    def begin_v2_yaw_drift_recovery(self) -> None:
        with self._lock:
            self._v2.begin_yaw_drift_recovery()

    def plan(
        self,
        perception: PerceptionResult,
        *,
        ultrasonic_distance_m: float | None,
        imu_status: dict[str, object] | None = None,
    ) -> LocalPlan:
        with self._lock:
            return self._v2.plan(
                perception,
                ultrasonic_distance_m=ultrasonic_distance_m,
                imu_status=imu_status,
            )
