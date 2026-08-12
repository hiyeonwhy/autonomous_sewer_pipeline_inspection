from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class DefectType(str, Enum):
    BLOCKAGE = "BLOCKAGE"
    CRACK = "CRACK"
    SURFACE_DAMAGE = "SURFACE_DAMAGE"
    DEBRIS = "DEBRIS"
    ROOT = "ROOT"
    OTHER = "OTHER"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    NORMAL = "NORMAL"


class RepairStatus(str, Enum):
    BEFORE = "BEFORE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NOT_NEEDED = "NOT_NEEDED"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    # 사진 판독에서 그 결함을 확인하지 못한 것. 탐지기가 잡았지만 사진에는 없거나
    # 관 내부가 찍히지 않은 경우다. 기록은 남기고 목록·보고서에서만 뺀다.
    REJECTED = "REJECTED"


class DefectCaptureRequest(BaseModel):
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    detection_index: int | None = Field(default=None, ge=0)
    defect_type: DefectType
    severity: Severity
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    approx_position_m: float | None = None
    snapshot_path: str
    detected_at: datetime | None = None

    @field_validator("defect_type", "severity", mode="before")
    @classmethod
    def normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class BoundingBox(BaseModel):
    """탐지 모델이 준 결함 영역 — 원본 프레임의 픽셀 좌표."""

    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)
    x2: float = Field(gt=0.0)
    y2: float = Field(gt=0.0)

    @model_validator(mode="after")
    def coordinates_ordered(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("x2는 x1보다, y2는 y1보다 커야 합니다.")
        return self


class DetectionItem(BaseModel):
    """한 프레임에서 탐지된 결함 1건."""

    defect_type: DefectType
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BoundingBox
    # YOLO는 심각도를 내지 않는다. 없으면 요청의 `default_severity`를 쓴다.
    severity: Severity | None = None

    @field_validator("defect_type", "severity", mode="before")
    @classmethod
    def normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class DetectionCaptureRequest(BaseModel):
    """한 프레임의 탐지 결과를 전부 등록한다.

    결함마다 그 결함만 담긴 크롭을 만들어 `snapshot_path`로 쓴다. 원본 프레임은
    `source_snapshot_path`에 남겨 근거자료로 보관한다.

    결함을 한 건씩 보내면 탐지기가 여러 개를 찾아도 나머지가 조용히 사라진다.
    환경부 지침 표 8.7은 「결함 항목 누락」을 1등급 오류로 보고, 상태등급은 구간 내
    결함점수의 **최대값**으로 정해지므로 누락이 곧 등급 왜곡이 된다.
    """

    # 같은 operation의 같은 frame/event를 재전송해도 한 번만 저장하기 위한 안정 키.
    operation_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    # `POST /api/media/captures`로 먼저 올린 원본 프레임 경로.
    snapshot_path: str
    approx_position_m: float | None = None
    detected_at: datetime | None = None
    detections: list[DetectionItem] | None = Field(default=None, max_length=50)
    # YOLO 결과 텍스트를 그대로 보내도 된다: `LABEL CONF x1 y1 x2 y2` 한 줄에 결함 하나.
    detections_text: str | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> "DetectionCaptureRequest":
        has_list = bool(self.detections)
        has_text = bool(self.detections_text and self.detections_text.strip())
        if has_list == has_text:
            raise ValueError("detections 또는 detections_text 중 하나만 보내야 합니다.")
        return self


class DefectReviewUpdateRequest(BaseModel):
    repair_status: RepairStatus | None = None
    action_type: str | None = None
    review_status: ReviewStatus | None = None

    @field_validator("repair_status", "review_status", mode="before")
    @classmethod
    def normalize_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class DefectResponse(BaseModel):
    id: int
    session_id: int
    operation_id: str | None = None
    event_id: str | None = None
    detection_index: int | None = None
    defect_type: DefectType
    severity: Severity
    confidence: float
    approx_position_m: float | None
    # 결함별 크롭. 보고서 양식과 판독 모델이 쓰는 사진이다.
    snapshot_path: str
    # 크롭을 떠 온 원본 프레임. 근거자료이며 크롭을 다시 뜰 때도 쓴다.
    source_snapshot_path: str | None = None
    bbox: BoundingBox | None = None
    repair_status: RepairStatus
    action_type: str | None = None
    review_status: ReviewStatus
    detected_at: datetime
    # 사진 판독이 끝났는가. 결함 통계는 이게 참인 것만 센다 — 판독 전에 세면 나중에
    # 반려될 때 화면의 결함 수가 줄어들어 계산이 틀린 것처럼 보인다.
    photo_reviewed: bool = False
