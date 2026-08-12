from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class BatchStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ScoreSummary(BaseModel):
    # 목록용. features·contributions 는 상세 조회에서만 내려갑니다.

    district_id: int
    sgg_code: str
    full_name: str
    sido_code: str | None = None
    sido_name: str | None = None
    score: float
    rank: int
    grade: int
    grade_label: str
    percentile: float
    sido_rank: int | None = None
    sido_count: int | None = None
    pipe_total_length_km: float | None = None

    # 최근 강수를 반영한 점수 (score × 최대 1.2). 순위·등급은 이 값을 쓰지
    # 않습니다 — 강수는 시점 의존이라 계획을 흔들면 안 되기 때문입니다.
    # 화면은 "이번 달만 보면 몇 위"를 참고로 보여주는 데만 씁니다.
    triggered_score: float | None = None


class ScoreDetail(ScoreSummary):
    # 산정 근거까지. 기획서 Ⅹ의 '근거(입력 특성) 확인 가능'이 이 응답입니다.

    batch_id: str
    model_version: str
    calculated_at: datetime
    features: dict[str, float | None] = Field(default_factory=dict)
    contributions: dict[str, float] = Field(default_factory=dict)
    contribution_shares: dict[str, float] = Field(default_factory=dict)

    # 결측 지표는 전 구역이 같은 중앙값 패딩을 받으므로 프론트에서 회색 처리해야
    # 합니다. 그대로 그리면 패딩이 정보처럼 보입니다.
    missing_features: list[str] = Field(default_factory=list)

    # 지표 한글명. 프론트가 하드코딩하지 않도록 응답에 실어 보냅니다.
    display_names: dict[str, str] = Field(default_factory=dict)


class GradeBand(BaseModel):
    label: str
    share: float
    description: str


class ImpactSpec(BaseModel):
    features: list[str]
    floor: float
    span: float


class RainTriggerSpec(BaseModel):
    # 강수 트리거 계약. applies_to_score=False 가 "순위·등급에는 안 들어간다"는
    # 선언이고, 화면이 그 문구를 근거로 참고값임을 표시합니다.
    span: float
    applies_to_score: bool
    field: str


class MetaResponse(BaseModel):
    model_version: str
    generated_at: str
    batch_id: str
    input_digest: str
    district_count: int
    admin_basis: str
    occurrence_weights: dict[str, float]
    impact: ImpactSpec
    rain_trigger: RainTriggerSpec
    display_names: dict[str, str]
    grades: dict[str, GradeBand]
    coverage: dict[str, int]
    sources: dict[str, int]


class DistrictListResponse(BaseModel):
    meta: MetaResponse | None = None
    total: int
    districts: list[ScoreSummary]


class SidoSummaryResponse(BaseModel):
    sido_code: str
    sido_name: str | None = None
    rank: int
    district_count: int

    # 단순 평균이 아니라 관로 연장 가중 평균입니다. 시군구 수가 다를 때 단순
    # 평균은 왜곡되고, 관로가 긴 시군구가 시도를 대표하는 것이 타당합니다.
    weighted_score: float
    top_district: str | None = None
    grade_counts: dict[str, int] = Field(default_factory=dict)


class RecalculateRequest(BaseModel):
    # 기본은 엔진이 이미 만들어 둔 산출물을 적재만 합니다. 엔진 재실행은 raw/ 원본
    # 28 MB 가 있어야 하므로 명시적으로 켜야 합니다.
    run_engine: bool = False
    reason: str | None = Field(default=None, max_length=200)


class RecalculateAccepted(BaseModel):
    # 202 Accepted 응답.
    #
    # 산정은 외부 API 를 여러 번 호출해 수십 초 걸립니다. 동기로 만들면 504 가
    # 나므로 batch_id 만 먼저 돌려주고 진행 상태를 따로 조회하게 합니다.

    batch_id: str
    status: BatchStatus
    status_url: str
    queued_at: datetime


class BatchResponse(BaseModel):
    batch_id: str
    # 완료 후 채워지는 엔진 batch_id (입력 지문 기반). 점수 조회는 이 id 를 씁니다.
    engine_batch_id: str | None = None
    status: BatchStatus
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model_version: str | None = None
    input_digest: str | None = None
    district_count: int | None = None
    run_engine: bool = False
    reason: str | None = None
    error: str | None = None

    # 입력 지문이 직전 배치와 달랐는지. False 면 순위가 같은 게 정상입니다.
    input_changed: bool | None = None

    # 직전 배치 대비 순위 변화. "왜 지난주랑 순위가 다르죠?"에 답하는 필드입니다.
    changed_ranks: int | None = None
    max_rank_shift: int | None = None
    previous_batch_id: str | None = None


class HistoryPoint(BaseModel):
    batch_id: str
    calculated_at: datetime
    model_version: str
    score: float
    rank: int
    grade: int


class InspectionSummary(BaseModel):
    # 이 구역에서 실제 점검(로봇 세션)이 찾아낸 결함. 기획서 Ⅶ의
    # '과거 산정 결과와 실제 결함 발견 이력 비교'가 이 필드입니다.
    session_id: int
    started_at: datetime | None = None
    status: str | None = None
    defect_count: int = 0


class HistoryResponse(BaseModel):
    district_id: int
    sgg_code: str
    full_name: str
    points: list[HistoryPoint]
    # 산정(예측) 옆에 실측을 나란히 — 아직 점검 이력이 없으면 빈 목록.
    inspections: list[InspectionSummary] = Field(default_factory=list)


class AdminDongResponse(BaseModel):
    adm_code: str
    adm_name: str
    sgg_code: str

    # Level 2 는 점수화하지 않습니다. 읍면동 단위로 확보되는 데이터가 침하 이력
    # 하나뿐이라 운영자 판단 대상입니다.
    subsidence_count: int = 0
    sewer_cause_count: int = 0


class DongListResponse(BaseModel):
    sgg_code: str
    full_name: str
    total: int

    # 이 시군구의 침하 중 **동을 특정하지 못한** 건수. 화면이 이걸 말하지 않으면
    # "이력 없음"이 거짓이 됩니다 — 없는 게 아니라 어느 동인지 모르는 것입니다.
    unlocated_count: int = 0

    dongs: list[AdminDongResponse]
