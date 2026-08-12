from __future__ import annotations

from math import exp, isfinite

from pipecheckkk_ai.models import LocalPlan, PerceptionResult


class LocalFreeSpacePlanner:
    """Receding-horizon local planner over an AI-generated free-space profile.

    The depth model owns the traversability evidence. This planner only optimizes
    bounded steering/throttle candidates and keeps short temporal memory to avoid
    left/right oscillation. Raspberry Pi safety remains an independent final gate.
    """

    def __init__(
        self,
        *,
        ema_alpha: float = 0.40,
        maximum_steering_step: float = 0.25,
        maximum_throttle: float = 0.50,
        cruise_throttle: float = 0.40,
        minimum_moving_throttle: float = 0.12,
        escape_risk_threshold: float = 0.50,
        escape_throttle: float = 0.50,
        ultrasonic_stop_m: float = 0.05,
    ) -> None:
        if not 0 < ema_alpha <= 1:
            raise ValueError("ema_alpha must be in (0, 1]")
        if not 0 < maximum_steering_step <= 2:
            raise ValueError("maximum_steering_step must be in (0, 2]")
        if not 0 < cruise_throttle <= maximum_throttle:
            raise ValueError("cruise_throttle must be in (0, maximum_throttle]")
        self.ema_alpha = ema_alpha
        self.maximum_steering_step = maximum_steering_step
        self.maximum_throttle = maximum_throttle
        self.cruise_throttle = cruise_throttle
        self.minimum_moving_throttle = minimum_moving_throttle
        self.escape_risk_threshold = escape_risk_threshold
        self.escape_throttle = escape_throttle
        self.ultrasonic_stop_m = ultrasonic_stop_m
        self._smoothed_profile: list[float] | None = None
        self._previous_steering = 0.0

    def reset(self) -> None:
        self._smoothed_profile = None
        self._previous_steering = 0.0

    def plan(
        self,
        perception: PerceptionResult,
        *,
        ultrasonic_distance_m: float | None,
    ) -> LocalPlan:
        profile = self._profile(perception)
        smoothed = self._smooth(profile)
        candidate_count = len(smoothed)
        if ultrasonic_distance_m is not None and (
            not isfinite(ultrasonic_distance_m)
            or ultrasonic_distance_m < 0.0
            or ultrasonic_distance_m <= self.ultrasonic_stop_m
        ):
            return LocalPlan(
                action="STOP",
                steering=0.0,
                throttle=0.0,
                risk=1.0,
                confidence=perception.confidence,
                selected_sector=candidate_count // 2,
                candidate_scores=[0.0] * candidate_count,
                smoothed_free_space=smoothed,
                reason=(
                    "초음파 값이 invalid이거나 "
                    f"{self.ultrasonic_stop_m:.2f}m hard-stop 범위여서 안전 정지합니다."
                ),
            )
        positions = [
            -1.0 + (2.0 * index / (candidate_count - 1))
            for index in range(candidate_count)
        ]

        scores: list[float] = []
        for position, clearance in zip(positions, smoothed, strict=True):
            forward_progress = 1.0 - abs(position)
            steering_continuity = 1.0 - min(1.0, abs(position - self._previous_steering) / 2.0)
            scores.append(
                0.68 * clearance
                + 0.18 * forward_progress
                + 0.14 * steering_continuity
            )

        vision_risk = max(
            perception.path_blocked_probability,
            1.0 - max(smoothed),
        )
        escape_mode = vision_risk >= self.escape_risk_threshold
        if escape_mode:
            side_candidates = [*range(0, candidate_count // 3), *range(candidate_count * 2 // 3, candidate_count)]
            selected_sector = max(side_candidates, key=scores.__getitem__)
            steering = -1.0 if positions[selected_sector] < 0 else 1.0
        else:
            selected_sector = max(range(candidate_count), key=scores.__getitem__)
            peak = max(scores)
            weights = [exp((score - peak) * 7.0) for score in scores]
            raw_steering = sum(position * weight for position, weight in zip(positions, weights, strict=True)) / sum(weights)
            steering = self._slew_steering(raw_steering)

        ultrasonic_risk = 0.0
        if ultrasonic_distance_m is not None:
            if ultrasonic_distance_m < 0.45:
                ultrasonic_risk = (0.45 - ultrasonic_distance_m) / 0.40

        risk = min(1.0, max(vision_risk, ultrasonic_risk))
        if perception.safe_for_control and perception.confidence >= 0.60:
            throttle = self.escape_throttle if escape_mode else self.cruise_throttle
        else:
            throttle = 0.0

        self._previous_steering = steering
        direction = "좌측" if steering < -0.08 else "우측" if steering > 0.08 else "중앙"
        mode = "강제 회피" if escape_mode else "재계획"
        return LocalPlan(
            steering=steering,
            throttle=min(self.maximum_throttle, max(0.0, throttle)),
            risk=risk,
            confidence=perception.confidence,
            selected_sector=selected_sector,
            candidate_scores=scores,
            smoothed_free_space=smoothed,
            reason=(
                f"Depth AI 9방향 후보 중 {selected_sector + 1}번을 선택해 {direction}으로 {mode}했습니다. "
                f"위험도 {risk:.2f}, 인식 신뢰도 {perception.confidence:.2f}."
            ),
        )

    def _profile(self, perception: PerceptionResult) -> list[float]:
        profile = perception.free_space_profile
        if len(profile) < 3:
            profile = [
                perception.left_free_score,
                perception.center_free_score,
                perception.right_free_score,
            ]
        if len(profile) % 2 == 0:
            raise ValueError("free-space profile must have an odd number of sectors")
        return [min(1.0, max(0.0, float(value))) for value in profile]

    def _smooth(self, profile: list[float]) -> list[float]:
        if self._smoothed_profile is None or len(self._smoothed_profile) != len(profile):
            self._smoothed_profile = list(profile)
        else:
            alpha = self.ema_alpha
            self._smoothed_profile = [
                alpha * current + (1.0 - alpha) * previous
                for current, previous in zip(profile, self._smoothed_profile, strict=True)
            ]
        return list(self._smoothed_profile)

    def _slew_steering(self, target: float) -> float:
        lower = self._previous_steering - self.maximum_steering_step
        upper = self._previous_steering + self.maximum_steering_step
        return min(1.0, max(-1.0, min(upper, max(lower, target))))
