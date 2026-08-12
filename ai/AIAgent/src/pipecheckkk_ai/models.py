from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MissionState(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ERROR = "ERROR"


class ProgressEventType(StrEnum):
    ROBOT_ACTION = "ROBOT_ACTION"
    DEFECT_DETECTED = "DEFECT_DETECTED"
    OBSTACLE_DETECTED = "OBSTACLE_DETECTED"
    OBSTACLE_AVOIDANCE_COMPLETED = "OBSTACLE_AVOIDANCE_COMPLETED"
    EXPLORATION_UNAVAILABLE = "EXPLORATION_UNAVAILABLE"


class AutonomyAction(StrEnum):
    FOLLOW_PIPE = "FOLLOW_PIPE"
    CONTINUE = "CONTINUE"
    SLOW_DOWN = "SLOW_DOWN"
    STOP = "STOP"
    AVOID_LEFT = "AVOID_LEFT"
    AVOID_RIGHT = "AVOID_RIGHT"
    BYPASS_OBSTACLE = "BYPASS_OBSTACLE"
    RECOVER_LEFT = "RECOVER_LEFT"
    RECOVER_RIGHT = "RECOVER_RIGHT"
    REACQUIRE_CENTER = "REACQUIRE_CENTER"
    ABORT_MISSION = "ABORT_MISSION"
    REQUEST_OPERATOR = "REQUEST_OPERATOR"


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TelemetryObservation(ApiModel):
    camera_connected: bool
    frame_age_s: float = Field(ge=0)
    ultrasonic_distance_m: float | None = Field(default=None, gt=0)
    path_blocked_probability: float = Field(ge=0, le=1)
    left_free_score: float = Field(ge=0, le=1)
    right_free_score: float = Field(ge=0, le=1)
    recovery_attempts: int = Field(default=0, ge=0)


class AgentDecision(ApiModel):
    action: AutonomyAction
    reason: str
    mission_state: MissionState


class DecisionProposal(ApiModel):
    action: AutonomyAction
    confidence: float = Field(ge=0, le=1)
    reason: str
    fallback: AutonomyAction = AutonomyAction.STOP
    model_name: str
    latency_ms: float = Field(ge=0)


class ProgressAnnouncement(ApiModel):
    revision: int = Field(default=0, ge=0)
    mission_id: str | None = None
    mission_state: MissionState = MissionState.IDLE
    event_type: ProgressEventType | None = None
    message: str = Field(default="", max_length=1500)
    source_model: str = "qwen2.5-vl-3b-instruct-awq"
    generated_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    status: Literal["idle", "pending", "ready", "unavailable", "error"] = "idle"


class ModelRuntimeStatus(ApiModel):
    name: str
    role: str
    repo_id: str
    runtime: str
    downloaded: bool
    loaded: bool = False
    note: str


class ActiveModelStatus(ApiModel):
    perception: ModelRuntimeStatus
    decision: ModelRuntimeStatus


class PerceptionResult(ApiModel):
    model_name: str
    backend: str
    center_error: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    latency_ms: float = Field(ge=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    safe_for_control: bool
    path_blocked_probability: float = Field(default=0.0, ge=0, le=1)
    left_free_score: float = Field(default=0.0, ge=0, le=1)
    center_free_score: float = Field(default=0.0, ge=0, le=1)
    right_free_score: float = Field(default=0.0, ge=0, le=1)
    free_space_profile: list[float] = Field(default_factory=list)
    top_label: str | None = None
    note: str


class LocalPlan(ApiModel):
    action: str = "AI_LOCAL_PLAN"
    steering: float = Field(ge=-1, le=1)
    throttle: float = Field(ge=0, le=0.90)
    risk: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    selected_sector: int = Field(ge=0)
    candidate_scores: list[float]
    smoothed_free_space: list[float]
    reason: str


class DriveObservation(ApiModel):
    center_error: float = Field(ge=-1, le=1)
    perception_confidence: float = Field(ge=0, le=1)
    frame_age_s: float = Field(ge=0)
    ultrasonic_distance_m: float | None = Field(default=None, gt=0)
    perception_model: str = "depth-freespace"


class DriveCommand(ApiModel):
    command_id: str
    action: str
    left_speed: float = Field(ge=0, le=1)
    right_speed: float = Field(ge=0, le=1)
    ttl_ms: int = Field(gt=0)
    issued_at: datetime
    expires_at: datetime
    safety_approved: bool
    reason: str
    control_mode: str = "simulation-no-motor"
    perception_model: str
    decision_model: str = "rule-policy"


class EdgeCommandAck(ApiModel):
    command_id: str
    sequence: int
    accepted: bool
    reason: str
    applied_left_speed: float = 0.0
    applied_right_speed: float = 0.0
    timestamp: datetime | None = None


class CommandBridgeStatus(ApiModel):
    connected: bool = False
    armed: bool = False
    emergency_stop: bool = False
    mission_id: str | None = None
    latest_sequence: int = 0
    heartbeat_age_ms: float | None = None
    control_mode: str = "real-motor"
    reason: str = "EDGE_NOT_CONNECTED"


class MotorOutput(ApiModel):
    left: float = Field(ge=-1, le=1)
    right: float = Field(ge=-1, le=1)


class ImuTelemetry(ApiModel):
    pitch_deg: float
    roll_deg: float
    yaw_deg: float


class DriveStatus(ApiModel):
    loop_mode: str = "manual-step"
    control_mode: str = "simulation-no-motor"
    perception_model: str = "depth-freespace"
    decision_model: str = "rule-policy"
    latest_observation: DriveObservation | None = None
    latest_perception: PerceptionResult | None = None
    latest_plan: LocalPlan | None = None
    latest_command: DriveCommand | None = None
    motor_output: MotorOutput | None = None
    imu: ImuTelemetry | None = None


class AgentEvent(ApiModel):
    id: str
    timestamp: datetime = Field(default_factory=utc_now)
    category: str
    level: str
    message: str
    mission_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MissionSnapshot(ApiModel):
    mission_id: str | None = None
    state: MissionState = MissionState.IDLE
    area: str | None = None
    reason_code: str | None = None
    message: str = "새 임무를 기다리고 있습니다."
    agent_action: str = "WAIT"
    agent_reason: str = "임무가 시작되지 않았습니다."
    started_at: datetime | None = None
    ended_at: datetime | None = None
    distance_m: float | None = None
    battery_percent: float | None = None
    report_available: bool = False
    control_mode: str = "simulation-no-motor"


class YoloDetection(ApiModel):
    """YOLO defect detection result."""

    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[float]
