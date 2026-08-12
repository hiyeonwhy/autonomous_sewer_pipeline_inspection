from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SessionMode(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class SessionStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class DefectSummary(BaseModel):
    total: int = Field(default=0, ge=0)
    critical: int = Field(default=0, ge=0)
    warning: int = Field(default=0, ge=0)
    normal: int = Field(default=0, ge=0)


class PipeSpec(BaseModel):
    """조사 대상 관로의 제원.

    관로 대장에서 옮겨 적는 참조 정보이므로 앱이 관리하는 상태값(mode, status)과
    달리 자유 문자열로 둔다. 관로 대장을 별도 컬렉션(pipes)으로 분리하게 되면
    이 필드들이 그대로 옮겨가도록 이름을 맞춰 두었다.
    """

    pipe_no: str | None = Field(default=None, max_length=64)  # 관로번호
    pipe_category: str | None = Field(default=None, max_length=32)  # 구분 (관로/암거 등)
    pipe_type: str | None = Field(default=None, max_length=64)  # 관종 (PVC관, 흄관(HP) 등)
    diameter_mm: int | None = Field(default=None, ge=1, le=10000)  # 규격
    drainage_type: str | None = Field(default=None, max_length=32)  # 배수방식 (오수/우수/합류)
    pipe_length_m: float | None = Field(default=None, ge=0, le=100000)  # 연장


class SurveyRun(BaseModel):
    """주행 결과. 미주행거리는 연장 − 총주행거리로 계산해 저장한다."""

    survey_location: str | None = Field(default=None, max_length=256)  # 점검 위치(동·주소)
    # 그 동 안에서 실제로 점검한 구간. 「광주광역시 광산구 하남동 구간2」
    # 프론트의 「점검 기본 설정」에서 고른 값이 여기로 들어와 보고서에 실린다.
    inspection_section: str | None = Field(default=None, max_length=256)  # 점검 구간
    # 점검 방향(FORWARD/REVERSE/BOTH). 점검 시작 팝업에서 고른 값이다.
    #
    # 예전에는 브라우저 안에만 있어서, 점검을 시작하지 않은 화면(다른 PC, 새로 연 탭)
    # 에서는 「점검 방향 --」으로 보였다. 세션에 실어야 누가 보든 같은 값이 나온다.
    inspection_direction: str | None = Field(default=None, max_length=16)  # 점검 방향
    total_distance_m: float | None = Field(default=None, ge=0, le=100000)  # 총주행거리
    undriven_distance_m: float | None = Field(default=None, ge=0, le=100000)  # 미주행거리


class SessionCreateRequest(BaseModel):
    district_id: int
    priority_score_id: int | None = None
    robot_id: int
    approved_by: int | None = None
    mode: SessionMode
    # 승인 운영자의 확인 코드. **서버가 검증한다.** 화면에서만 확인하면 개발자 도구로
    # 곧장 이 API 를 부르는 것을 못 막는다.
    operator_code: str | None = Field(default=None, max_length=128)
    # 운영자가 「점검 시작」을 누른 시각. 화면이 보여 준 시각과 기록이 어긋나지
    # 않도록 클라이언트가 보낸다. 안 보내면 서버 시계를 쓴다.
    #
    # **믿고 그대로 쓰지는 않는다.** 클라이언트 시계는 틀릴 수 있고, 점검일자와
    # 보고서 발번이 여기서 갈라져 나오므로 엉뚱한 값이 들어오면 기록 전체가 틀어진다.
    # 서버 시계와 크게 어긋나면 라우터가 버리고 자기 시계를 쓴다.
    started_at: datetime | None = None
    pipe: PipeSpec | None = None
    survey: SurveyRun | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class SessionUpdateRequest(BaseModel):
    status: SessionStatus | None = None
    ended_at: datetime | None = None
    defect_summary: DefectSummary | None = None
    pipe: PipeSpec | None = None
    survey: SurveyRun | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class SessionResponse(BaseModel):
    id: int
    district_id: int
    priority_score_id: int | None
    robot_id: int
    approved_by: int | None
    mode: SessionMode
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    defect_summary: DefectSummary
    pipe: PipeSpec = Field(default_factory=PipeSpec)
    survey: SurveyRun = Field(default_factory=SurveyRun)

    @field_validator("started_at", "ended_at", mode="after")
    @classmethod
    def stamp_utc(cls, value: datetime | None) -> datetime | None:
        """시각을 UTC 로 명시해 내보낸다.

        Mongo 는 시각을 UTC 로 저장하지만 드라이버가 **시간대 없는**(naive) datetime
        으로 돌려준다. 그대로 직렬화하면 `2026-08-09T07:55:33.913000` 처럼 오프셋
        없는 문자열이 나가는데, 이건 UTC 라는 표시가 어디에도 없다.

        받는 쪽이 이걸 로컬시각으로 읽어 버리면(자바스크립트 `new Date()` 의 표준
        동작이다) KST 기준 9시간 이른 시각이 된다 — 실제로 16시 55분 시작이 화면에
        7시 55분으로 나왔고, 경과 시간도 그만큼 부풀었다.

        여기서 시간대를 붙이면 오프셋이 실려 나가므로 클라이언트가 해석을 추측할
        일이 없다. 값 자체는 바뀌지 않는다 — 같은 순간에 이름표만 붙인다.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)