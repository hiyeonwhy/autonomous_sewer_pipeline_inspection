from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from uuid import uuid4

from pipecheckkk_ai.models import (
    AgentDecision,
    AgentEvent,
    AutonomyAction,
    MissionSnapshot,
    MissionState,
    TelemetryObservation,
    utc_now,
)


class IntegratedAgent:
    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = monotonic,
        minimum_avoidance_s: float = 0.6,
        bypass_duration_s: float = 1.0,
        maximum_avoidance_s: float = 2.0,
        obstacle_trigger_m: float = 0.45,
        maximum_events: int = 500,
    ) -> None:
        if maximum_events < 1:
            raise ValueError("maximum_events must be positive")
        self.snapshot = MissionSnapshot()
        self.events: list[AgentEvent] = []
        self._event_cursor = 0
        self._maximum_events = maximum_events
        self._monotonic = monotonic_clock
        self._minimum_avoidance_s = minimum_avoidance_s
        self._bypass_duration_s = bypass_duration_s
        self._maximum_avoidance_s = maximum_avoidance_s
        self._obstacle_trigger_m = obstacle_trigger_m
        self._avoidance_action: AutonomyAction | None = None
        self._avoidance_started_at: float | None = None
        self._avoidance_turn_duration_s = 0.0
        self._bypass_until: float | None = None
        self._recovery_action: AutonomyAction | None = None
        self._recovery_until: float | None = None

    def _reset_avoidance_state(self) -> None:
        self._avoidance_action = None
        self._avoidance_started_at = None
        self._avoidance_turn_duration_s = 0.0
        self._bypass_until = None
        self._recovery_action = None
        self._recovery_until = None

    def _event(
        self,
        category: str,
        level: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> None:
        if (
            category == "DECISION"
            and self.events
            and self.events[-1].category == category
            and self.events[-1].message == message
        ):
            return
        self.events.append(
            AgentEvent(
                id=f"evt-{uuid4().hex}",
                category=category,
                level=level,
                message=message,
                mission_id=self.snapshot.mission_id,
                data=data or {},
            )
        )
        self._event_cursor += 1
        if len(self.events) > self._maximum_events:
            del self.events[: len(self.events) - self._maximum_events]

    @property
    def event_cursor(self) -> int:
        return self._event_cursor

    def record_event(
        self,
        category: str,
        message: str,
        data: dict[str, object] | None = None,
        *,
        level: str = "info",
    ) -> None:
        self._event(category, level, message, data)

    def start_mission(self, area: str) -> MissionSnapshot:
        mission_id = f"mission-{uuid4().hex[:12]}"
        self.snapshot = MissionSnapshot(
            mission_id=mission_id,
            state=MissionState.PREPARING,
            area=area,
            message="카메라·센서·안전 상태를 확인하고 있습니다.",
            agent_action="SELF_CHECK",
            agent_reason="주행 전에 필수 연결과 안전 상태를 확인합니다.",
            started_at=utc_now(),
        )
        self.events = []
        self._event_cursor = 0
        self._reset_avoidance_state()
        self._event("MISSION", "info", "임무 자체 점검을 시작했습니다.")
        self.snapshot.state = MissionState.RUNNING
        self.snapshot.message = "관로 자율주행 임무가 진행 중입니다."
        self.snapshot.agent_action = "FOLLOW_PIPE"
        self.snapshot.agent_reason = "자체 점검을 통과해 관로 중심 추종을 시작합니다."
        self._event("MISSION", "success", "임무 상태가 RUNNING으로 변경됐습니다.")
        self._event("DECISION", "info", self.snapshot.agent_reason)
        return self.snapshot.model_copy(deep=True)

    def fail_system(self, reason: str) -> MissionSnapshot:
        self.snapshot.state = MissionState.ERROR
        self.snapshot.reason_code = "SYSTEM_PREFLIGHT_FAILED"
        self.snapshot.message = "필수 안전 점검에 실패하여 임무를 시작하지 않았습니다."
        self.snapshot.agent_action = "STOP"
        self.snapshot.agent_reason = reason
        self.snapshot.ended_at = utc_now()
        self._event("SAFETY", "alert", reason)
        self._event("MISSION", "alert", "임무 상태가 ERROR로 변경됐습니다.")
        return self.snapshot.model_copy(deep=True)

    def fail_blocked(self, reason: str, retry_count: int) -> MissionSnapshot:
        if self.snapshot.state is not MissionState.RUNNING:
            raise ValueError("진행 중인 임무만 막힘 상태로 종료할 수 있습니다.")

        self._event("SCENE", "warn", "관로 진행 불가 후보가 감지됐습니다.")
        self._event("DECISION", "alert", reason, {"retryCount": retry_count})
        self._event("SAFETY", "alert", "모터 STOP 유지가 요청됐습니다.")
        self.snapshot.state = MissionState.FAILED_BLOCKED
        self.snapshot.reason_code = "PATH_BLOCKED"
        self.snapshot.message = "장애물 우회에 실패하여 자율 임무를 종료했습니다."
        self.snapshot.agent_action = "STOP"
        self.snapshot.agent_reason = reason
        self.snapshot.ended_at = utc_now()
        self.snapshot.report_available = False
        self._event(
            "MISSION",
            "alert",
            "임무 상태가 FAILED_BLOCKED로 변경됐습니다.",
            {"retryCount": retry_count},
        )
        return self.snapshot.model_copy(deep=True)

    def evaluate_telemetry(self, observation: TelemetryObservation) -> AgentDecision:
        if self.snapshot.state is not MissionState.RUNNING:
            raise ValueError("진행 중인 임무에서만 telemetry를 판단할 수 있습니다.")

        if not observation.camera_connected or observation.frame_age_s > 3.0:
            reason = "카메라 영상이 끊겼거나 최신 프레임이 아니므로 감속 후 정지가 필요합니다."
            self._event("SAFETY", "alert", reason)
            return AgentDecision(
                action=AutonomyAction.SLOW_DOWN,
                reason=reason,
                mission_state=self.snapshot.state,
            )

        now = self._monotonic()
        close_obstacle = (
            observation.ultrasonic_distance_m is not None
            and observation.ultrasonic_distance_m <= self._obstacle_trigger_m
        )
        blocked_scene = observation.path_blocked_probability >= 0.60

        if blocked_scene or close_obstacle:
            obstacle_reappeared = self._bypass_until is not None or self._recovery_action is not None
            if self._recovery_action is not None:
                self._avoidance_action = None
            self._bypass_until = None
            self._recovery_action = None
            self._recovery_until = None
            best_free_score = max(observation.left_free_score, observation.right_free_score)
            if (
                blocked_scene
                and best_free_score < 0.35
                and observation.recovery_attempts >= 3
            ):
                reason = "전방 장애물이 가깝고 좌우 회피 공간이 모두 안전 기준보다 작습니다."
                snapshot = self.fail_blocked(reason, observation.recovery_attempts)
                return AgentDecision(
                    action=AutonomyAction.ABORT_MISSION,
                    reason=reason,
                    mission_state=snapshot.state,
                )

            if self._avoidance_action in {
                AutonomyAction.AVOID_LEFT,
                AutonomyAction.AVOID_RIGHT,
            }:
                action = self._avoidance_action
            elif observation.left_free_score >= observation.right_free_score:
                action = AutonomyAction.AVOID_LEFT
                self._avoidance_action = action
                self._avoidance_started_at = now
            else:
                action = AutonomyAction.AVOID_RIGHT
                self._avoidance_action = action
                self._avoidance_started_at = now
            assert action is not None
            if obstacle_reappeared or self._avoidance_started_at is None:
                self._avoidance_started_at = now
            direction = "왼쪽" if action is AutonomyAction.AVOID_LEFT else "오른쪽"
            if obstacle_reappeared:
                reason = f"장애물 통과·복귀 중 전방 장애물을 재감지해 {direction} 회피를 다시 시작합니다."
            elif close_obstacle:
                reason = (
                    f"초음파가 {observation.ultrasonic_distance_m:.2f}m 전방 장애물을 감지했고 "
                    f"{direction} 회피 공간이 더 안전합니다."
                )
            else:
                reason = f"카메라가 전방 장애물을 확인했고 {direction} 회피 공간이 더 안전합니다."
            self._event(
                "DECISION",
                "warn",
                reason,
                {
                    "leftFreeScore": observation.left_free_score,
                    "rightFreeScore": observation.right_free_score,
                },
            )
            return AgentDecision(
                action=action,
                reason=reason,
                mission_state=self.snapshot.state,
            )

        if (
            self._avoidance_action is not None
            and self._bypass_until is None
            and self._recovery_action is None
        ):
            started_at = self._avoidance_started_at if self._avoidance_started_at is not None else now
            elapsed = min(max(0.0, now - started_at), self._maximum_avoidance_s)
            if elapsed < self._minimum_avoidance_s:
                direction = (
                    "왼쪽" if self._avoidance_action is AutonomyAction.AVOID_LEFT else "오른쪽"
                )
                reason = f"장애물이 시야에서 사라져도 안전 회전 시간을 채우도록 {direction} 회피를 유지합니다."
                self._event("DECISION", "warn", reason)
                return AgentDecision(
                    action=self._avoidance_action,
                    reason=reason,
                    mission_state=self.snapshot.state,
                )
            self._avoidance_turn_duration_s = max(self._minimum_avoidance_s, elapsed)
            self._bypass_until = now + self._bypass_duration_s
            reason = "회피 방향을 유지한 채 저속 직진하여 장애물 옆을 충분히 통과합니다."
            self._event("DECISION", "warn", reason)
            return AgentDecision(
                action=AutonomyAction.BYPASS_OBSTACLE,
                reason=reason,
                mission_state=self.snapshot.state,
            )

        if self._bypass_until is not None:
            if now < self._bypass_until:
                reason = "원래 진행 방향으로 복귀하기 전에 저속으로 장애물 옆을 통과하고 있습니다."
                self._event("DECISION", "warn", reason)
                return AgentDecision(
                    action=AutonomyAction.BYPASS_OBSTACLE,
                    reason=reason,
                    mission_state=self.snapshot.state,
                )
            avoided_left = self._avoidance_action is AutonomyAction.AVOID_LEFT
            self._recovery_action = (
                AutonomyAction.RECOVER_RIGHT if avoided_left else AutonomyAction.RECOVER_LEFT
            )
            self._recovery_until = now + self._avoidance_turn_duration_s
            self._bypass_until = None

        if self._recovery_action is not None:
            if self._recovery_until is not None and now >= self._recovery_until:
                self._reset_avoidance_state()
                reason = "장애물 통과와 방향 복원이 완료되어 전방 주행을 재개합니다."
                self._event("DECISION", "success", reason)
                return AgentDecision(
                    action=AutonomyAction.CONTINUE,
                    reason=reason,
                    mission_state=self.snapshot.state,
                )
            action = self._recovery_action
            direction = "왼쪽" if action is AutonomyAction.RECOVER_LEFT else "오른쪽"
            reason = f"장애물을 충분히 통과한 뒤 처음 주행 방향을 복원하기 위해 {direction}으로 반대 조향합니다."
            self._event("DECISION", "success", reason)
            return AgentDecision(
                action=action,
                reason=reason,
                mission_state=self.snapshot.state,
            )

        reason = "전방 경로와 관로 중심 추종 상태가 정상 범위입니다."
        self._event("DECISION", "info", reason)
        return AgentDecision(
            action=AutonomyAction.CONTINUE,
            reason=reason,
            mission_state=self.snapshot.state,
        )

    def complete_mission(self) -> MissionSnapshot:
        if self.snapshot.state is not MissionState.RUNNING:
            raise ValueError("진행 중인 임무만 완료할 수 있습니다.")

        self.snapshot.state = MissionState.STOPPING
        self.snapshot.agent_action = "STOP"
        self.snapshot.agent_reason = "운영자가 점검 종료를 요청했습니다."
        self._event("SAFETY", "info", "임무 완료 전에 모터 STOP을 요청했습니다.")
        self.snapshot.state = MissionState.COMPLETED
        self.snapshot.reason_code = "OPERATOR_COMPLETED"
        self.snapshot.message = "실시간 탐사 임무가 안전하게 종료됐습니다."
        self.snapshot.ended_at = utc_now()
        self.snapshot.report_available = False
        self._event("MISSION", "success", "임무 상태가 COMPLETED로 변경됐습니다.")
        return self.snapshot.model_copy(deep=True)

    def reset(self) -> MissionSnapshot:
        if self.snapshot.state not in {
            MissionState.IDLE,
            MissionState.COMPLETED,
            MissionState.FAILED_BLOCKED,
            MissionState.EMERGENCY_STOP,
            MissionState.ERROR,
        }:
            raise ValueError("진행 중인 임무는 초기화할 수 없습니다.")
        self.snapshot = MissionSnapshot()
        self.events = []
        self._event_cursor = 0
        self._reset_avoidance_state()
        return self.snapshot.model_copy(deep=True)
