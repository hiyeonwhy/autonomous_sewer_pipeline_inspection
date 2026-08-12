from pipecheckkk_ai.agent import IntegratedAgent
from pipecheckkk_ai.models import AutonomyAction, MissionState, TelemetryObservation


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_start_mission_moves_agent_to_running_and_records_reason() -> None:
    agent = IntegratedAgent()

    snapshot = agent.start_mission(area="원통형 시험 관로")

    assert snapshot.state is MissionState.RUNNING
    assert snapshot.area == "원통형 시험 관로"
    assert snapshot.mission_id is not None
    assert snapshot.agent_action == "FOLLOW_PIPE"
    assert snapshot.agent_reason == "자체 점검을 통과해 관로 중심 추종을 시작합니다."
    assert [event.category for event in agent.events] == ["MISSION", "MISSION", "DECISION"]


def test_blocked_mission_stops_and_ends_without_automatic_return() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="원통형 시험 관로")

    snapshot = agent.fail_blocked(
        reason="좌우 회피 공간이 모두 안전 기준보다 작습니다.",
        retry_count=3,
    )

    assert snapshot.state is MissionState.FAILED_BLOCKED
    assert snapshot.agent_action == "STOP"
    assert snapshot.reason_code == "PATH_BLOCKED"
    assert snapshot.ended_at is not None
    assert snapshot.report_available is False
    assert [event.category for event in agent.events[-4:]] == [
        "SCENE",
        "DECISION",
        "SAFETY",
        "MISSION",
    ]
    assert agent.events[-1].data["retryCount"] == 3


def test_complete_mission_stops_then_marks_completed_without_fake_metrics() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="원통형 시험 관로")

    snapshot = agent.complete_mission()

    assert snapshot.state is MissionState.COMPLETED
    assert snapshot.agent_action == "STOP"
    assert snapshot.reason_code == "OPERATOR_COMPLETED"
    assert snapshot.distance_m is None
    assert snapshot.battery_percent is None
    assert snapshot.report_available is False
    assert snapshot.ended_at is not None


def test_reset_returns_terminal_mission_to_idle() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="원통형 시험 관로")
    agent.complete_mission()

    snapshot = agent.reset()

    assert snapshot.state is MissionState.IDLE
    assert snapshot.mission_id is None
    assert agent.events == []


def test_agent_selects_open_side_without_ending_mission() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="원통형 시험 관로")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.16,
            path_blocked_probability=0.91,
            left_free_score=0.72,
            right_free_score=0.18,
            recovery_attempts=0,
        )
    )

    assert decision.action is AutonomyAction.AVOID_LEFT
    assert decision.mission_state is MissionState.RUNNING


def test_agent_avoids_camera_obstacle_before_ultrasonic_contact() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="일반 실내 시험 공간")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.80,
            path_blocked_probability=0.90,
            left_free_score=0.78,
            right_free_score=0.22,
            recovery_attempts=0,
        )
    )

    assert decision.action is AutonomyAction.AVOID_LEFT
    assert decision.mission_state is MissionState.RUNNING
    assert agent.snapshot.state is MissionState.RUNNING
    assert agent.events[-1].category == "DECISION"


def test_agent_uses_camera_side_scores_when_ultrasonic_is_close() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="일반 실내 시험 공간")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.04,
            path_blocked_probability=0.40,
            left_free_score=0.25,
            right_free_score=0.76,
            recovery_attempts=0,
        )
    )

    assert decision.action is AutonomyAction.AVOID_RIGHT
    assert decision.mission_state is MissionState.RUNNING


def test_agent_starts_avoidance_before_obstacle_is_closer_than_forty_five_centimeters() -> None:
    agent = IntegratedAgent(obstacle_trigger_m=0.45)
    agent.start_mission(area="초음파 조기 회피 시험")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.40,
            path_blocked_probability=0.10,
            left_free_score=0.70,
            right_free_score=0.30,
            recovery_attempts=0,
        )
    )

    assert decision.action is AutonomyAction.AVOID_LEFT
    assert "초음파" in decision.reason


def test_agent_avoids_moderate_camera_obstacle_instead_of_stopping() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="일반 실내 시험 공간")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=None,
            path_blocked_probability=0.65,
            left_free_score=0.62,
            right_free_score=0.38,
            recovery_attempts=0,
        )
    )

    assert decision.action is AutonomyAction.AVOID_LEFT


def test_agent_turns_bypasses_obstacle_then_counter_steers() -> None:
    clock = FakeClock()
    agent = IntegratedAgent(
        monotonic_clock=clock,
        minimum_avoidance_s=0.6,
        bypass_duration_s=1.0,
    )
    agent.start_mission(area="회피 상태 머신 시험")

    first = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.30,
            path_blocked_probability=0.90,
            left_free_score=0.80,
            right_free_score=0.20,
            recovery_attempts=0,
        )
    )
    clear_observation = TelemetryObservation(
        camera_connected=True,
        frame_age_s=0.1,
        ultrasonic_distance_m=0.60,
        path_blocked_probability=0.10,
        left_free_score=0.80,
        right_free_score=0.80,
        recovery_attempts=0,
    )
    still_turning = agent.evaluate_telemetry(clear_observation)
    clock.advance(0.6)
    bypass_1 = agent.evaluate_telemetry(clear_observation)
    clock.advance(0.5)
    bypass_2 = agent.evaluate_telemetry(clear_observation)
    clock.advance(0.5)
    recover = agent.evaluate_telemetry(clear_observation)
    clock.advance(0.6)
    straight = agent.evaluate_telemetry(clear_observation)

    assert first.action is AutonomyAction.AVOID_LEFT
    assert still_turning.action is AutonomyAction.AVOID_LEFT
    assert bypass_1.action.value == "BYPASS_OBSTACLE"
    assert bypass_2.action.value == "BYPASS_OBSTACLE"
    assert recover.action is AutonomyAction.RECOVER_RIGHT
    assert straight.action is AutonomyAction.CONTINUE


def test_obstacle_reappearing_during_bypass_cancels_recovery_and_turns_away() -> None:
    clock = FakeClock()
    agent = IntegratedAgent(
        monotonic_clock=clock,
        minimum_avoidance_s=0.6,
        bypass_duration_s=1.0,
    )
    agent.start_mission(area="회피 중 재감지 시험")
    blocked = TelemetryObservation(
        camera_connected=True,
        frame_age_s=0.1,
        ultrasonic_distance_m=0.30,
        path_blocked_probability=0.90,
        left_free_score=0.80,
        right_free_score=0.20,
        recovery_attempts=0,
    )
    clear = blocked.model_copy(
        update={"ultrasonic_distance_m": 0.60, "path_blocked_probability": 0.10}
    )

    assert agent.evaluate_telemetry(blocked).action is AutonomyAction.AVOID_LEFT
    clock.advance(0.6)
    assert agent.evaluate_telemetry(clear).action.value == "BYPASS_OBSTACLE"
    clock.advance(0.2)
    repeated = agent.evaluate_telemetry(blocked)

    assert repeated.action is AutonomyAction.AVOID_LEFT
    assert "재감지" in repeated.reason


def test_repeated_avoidance_deduplicates_semantic_decision_events() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="로그 중복 억제 시험")
    observation = TelemetryObservation(
        camera_connected=True,
        frame_age_s=0.1,
        ultrasonic_distance_m=0.30,
        path_blocked_probability=0.90,
        left_free_score=0.80,
        right_free_score=0.20,
        recovery_attempts=0,
    )
    before = len(agent.events)

    agent.evaluate_telemetry(observation)
    agent.evaluate_telemetry(observation)

    assert len(agent.events) == before + 1


def test_agent_aborts_when_obstacle_blocks_both_sides() -> None:
    agent = IntegratedAgent()
    agent.start_mission(area="원통형 시험 관로")

    decision = agent.evaluate_telemetry(
        TelemetryObservation(
            camera_connected=True,
            frame_age_s=0.1,
            ultrasonic_distance_m=0.14,
            path_blocked_probability=0.96,
            left_free_score=0.12,
            right_free_score=0.19,
            recovery_attempts=3,
        )
    )

    assert decision.action is AutonomyAction.ABORT_MISSION
    assert decision.mission_state is MissionState.FAILED_BLOCKED
    assert agent.snapshot.agent_action == "STOP"
    assert "좌우" in agent.snapshot.agent_reason


def test_records_v2_recovery_trigger_as_durable_decision_event() -> None:
    agent = IntegratedAgent()
    agent.start_mission("원통형 시험 관로")

    agent.record_event(
        "DECISION",
        "V2 경로 복귀 시작: ROLL",
        {"trigger": "ROLL", "pitchDeg": 12.0, "rollDeg": -45.0},
    )

    event = agent.events[-1]
    assert event.message == "V2 경로 복귀 시작: ROLL"
    assert event.data["trigger"] == "ROLL"
    assert event.data["rollDeg"] == -45.0


def test_mission_events_keep_only_latest_500_and_reset_for_new_inspection() -> None:
    agent = IntegratedAgent(maximum_events=500)
    agent.start_mission("장시간 점검 관로")

    for index in range(510):
        agent.record_event("SENSOR", f"센서 이벤트 {index}")

    assert len(agent.events) == 500
    assert agent.events[0].message == "센서 이벤트 10"
    assert agent.events[-1].message == "센서 이벤트 509"
    assert agent.event_cursor == 513

    agent.complete_mission()
    agent.reset()

    assert agent.events == []
    assert agent.event_cursor == 0
