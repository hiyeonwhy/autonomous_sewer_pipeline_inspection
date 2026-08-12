from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from pipecheckkk_ai.models import (
    DriveCommand,
    DriveObservation,
    LocalPlan,
    utc_now,
)


class DriveController:
    def __init__(self, *, max_speed: float = 0.90, ttl_ms: int = 1000) -> None:
        self.max_speed = max_speed
        self.ttl_ms = ttl_ms

    def reset(self) -> None:
        """V2 local-plan conversion is stateless between missions."""

    def compute_local_plan(
        self,
        observation: DriveObservation,
        plan: LocalPlan,
        *,
        decision_model: str = "depth-local-planner",
    ) -> DriveCommand:
        if observation.frame_age_s > 3.0:
            return self._stop(
                observation,
                "Camera frame is stale; AI local plan was rejected.",
                decision_model=decision_model,
            )
        if observation.perception_confidence < 0.60:
            return self._stop(
                observation,
                "Perception confidence is too low; AI local plan was rejected.",
                decision_model=decision_model,
            )
        if plan.action == "STOP" or plan.throttle <= 0:
            return self._stop(observation, plan.reason, decision_model=decision_model)

        steering = min(1.0, max(-1.0, plan.steering))
        throttle = min(self.max_speed, max(0.0, plan.throttle))
        if plan.action == "V2_PITCH_ADVANCE":
            left_speed = throttle
            right_speed = throttle
        else:
            left_speed = self._clamp(throttle * (1.0 + steering))
            right_speed = self._clamp(throttle * (1.0 - steering))
        now = utc_now()
        return DriveCommand(
            command_id=f"cmd-{uuid4().hex[:12]}",
            action=plan.action if plan.action == "V2_PITCH_ADVANCE" else "AI_LOCAL_PLAN",
            left_speed=left_speed,
            right_speed=right_speed,
            ttl_ms=self.ttl_ms,
            issued_at=now,
            expires_at=now + timedelta(milliseconds=self.ttl_ms),
            safety_approved=True,
            reason=plan.reason,
            perception_model=observation.perception_model,
            decision_model=decision_model,
        )

    def _clamp(self, speed: float) -> float:
        return min(self.max_speed, max(0.0, speed))

    def _stop(
        self,
        observation: DriveObservation,
        reason: str,
        *,
        decision_model: str = "rule-policy",
    ) -> DriveCommand:
        now = utc_now()
        return DriveCommand(
            command_id=f"cmd-{uuid4().hex[:12]}",
            action="STOP",
            left_speed=0.0,
            right_speed=0.0,
            ttl_ms=self.ttl_ms,
            issued_at=now,
            expires_at=now + timedelta(milliseconds=self.ttl_ms),
            safety_approved=False,
            reason=reason,
            perception_model=observation.perception_model,
            decision_model=decision_model,
        )
