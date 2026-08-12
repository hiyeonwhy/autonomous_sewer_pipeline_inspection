"""보고서 서술형 요약에 쓰는 내부 모델.

`ai/llm_report/models.py`를 백엔드로 옮긴 것이다. Cloud API는 GPU 머신에 닿을 수
없으므로, 모델 호출만 원격 API로 바꾸고 사실 정제·등급 산정·출력 검증은 여기서
그대로 수행한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DefectType(StrEnum):
    CRACK = "CRACK"
    DEBRIS = "DEBRIS"
    ROOT = "ROOT"
    BLOCKAGE = "BLOCKAGE"
    SURFACE_DAMAGE = "SURFACE_DAMAGE"
    OTHER = "OTHER"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    NORMAL = "NORMAL"


class ObservationFailure(StrEnum):
    """사진을 읽지 못한 이유. 보고서에 그대로 남겨 원인을 추적할 수 있게 한다."""

    NO_SNAPSHOT = "NO_SNAPSHOT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    IMAGE_FETCH_FAILED = "IMAGE_FETCH_FAILED"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    INFERENCE_FAILED = "INFERENCE_FAILED"


class DefectFact(BaseModel):
    """프롬프트에 넣어도 안전한 필드만 남긴 결함 1건."""

    defect_id: int | None = None
    defect_type: DefectType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    position_m: float = Field(ge=0.0, le=10000.0)
    snapshot_path: str | None = None
    # 점검 중에 미리 읽어 둔 사진 판독. 있으면 보고서를 만들 때 다시 부르지 않는다.
    # 이게 있어야 점검이 끝나자마자 보고서가 나온다 — 판독 한 건이 11.7초라
    # 종료 후에 몰아서 읽으면 결함 20건에 4분이 걸린다.
    photo_description: str | None = None


class SessionFacts(BaseModel):
    """한 보고서를 서술하는 데 필요한, 애플리케이션이 확정한 사실."""

    report_id: int
    session_id: int
    region: str
    survey_location: str | None = None
    duration: str | None = None
    defects: list[DefectFact] = Field(default_factory=list)
    dropped_defect_count: int = Field(default=0, ge=0)

    @property
    def defect_total(self) -> int:
        return len(self.defects) + self.dropped_defect_count

    def count_by_severity(self, severity: Severity) -> int:
        return sum(1 for defect in self.defects if defect.severity is severity)


class StateGrades(BaseModel):
    """구조적 / 운영적 상태등급 — 환경부 지침 제6장.

    `structural`/`operational`은 **조사구간(맨홀 대 맨홀) 상태점수**다. 단위구간 등급의
    평균이라 2.75처럼 소수가 나오며, 지침 표 6.1이 "별도의 등급 없이 상태점수 형태로
    제시"하라고 정한 값이다.

    `structural_grade`/`operational_grade`는 그 점수를 정수 등급으로 환산한 것으로,
    보고서 요약 문장과 정비시급성 판단에 쓴다. 코드가 계산하며 모델은 인용만 한다.
    """

    structural: float = Field(ge=1.0, le=5.0)
    operational: float = Field(ge=1.0, le=5.0)
    structural_grade: int = Field(ge=1, le=5)
    operational_grade: int = Field(ge=1, le=5)
    segment_count: int = Field(default=1, ge=1)
    segment_length_m: float = Field(default=2.5, gt=0.0)
    # 쓴 배점표. 보고서 각주에 그대로 실어 근거를 밝힌다. 예: "표 5.6 연성관 · 표 5.8"
    score_basis: str = ""
    # 결함 등급(대/중/소)을 지침 1.1.2의 관경·관둘레 기준이 아니라 바운딩박스 면적
    # 비율로 정했는지. 자동 탐지에서는 관경을 재지 못하므로 지금은 항상 참이고,
    # 사람이 등급을 직접 입력하는 경로가 생기면 그때 거짓이 된다.
    uses_estimated_scores: bool = False


class ReportNarrative(BaseModel):
    """모델이 작성해도 되는 세션 단위 서술."""

    assessment: str
    recommendations: list[str] = Field(default_factory=list)


class DefectObservation(BaseModel):
    """결함 1건에 대한 두 갈래 서술.

    둘을 합치지 않고 나란히 둔다. 합치면 모델이 탐지 라벨에 맞춰 사진 묘사를
    지어내기 때문이다(같은 사진에 라벨만 바꿔 주면 균열도 뿌리 침투도 "관찰됨"으로
    나왔다). 비교는 보고서를 읽는 사람이 한다.

    - `description` — 탐지 결과(JSON) 기반. 코드가 만들며 모델이 관여하지 않는다.
    - `photo_description` — 사진을 보고 쓴 묘사. 탐지 결과를 참고로만 주고,
      사진에서 확인되는지 판단하게 한다. 확인되지 않으면 그렇게 쓰도록 허용한다.
      사진을 못 읽었으면 `None`이고 `failure_code`에 이유가 담긴다.
    """

    defect_id: int | None = None
    position_m: float = Field(ge=0.0, le=10000.0)
    defect_type: DefectType
    severity: Severity
    description: str
    photo_description: str | None = None
    source: Literal["vision", "metadata"]
    failure_code: ObservationFailure | None = None
    # 실패 원인 원문(예외 종류와 메시지). 코드만 저장하면 서버 로그를 볼 수 없는
    # 사람은 「INFERENCE_FAILED」만 보고 원인을 알 수 없다. 실제로 그 코드가 한 번
    # 떴을 때 컨테이너가 재배포돼 로그가 사라져 원인을 특정하지 못했다.
    failure_detail: str | None = Field(default=None, max_length=300)


class ReportSummary(BaseModel):
    """`ReportSummaryUpdateRequest`로 그대로 옮겨 담을 수 있는 생성 결과."""

    assessment: str
    recommendations: list[str] = Field(default_factory=list)
    observations: list[DefectObservation] = Field(default_factory=list)
    grades: StateGrades
    vision_described_count: int = Field(default=0, ge=0)
    # 사진 판독이 실패해 보고서에서 뺀 결함들. 호출부가 이걸 받아 DB 에서도
    # 제외 표시를 해 둔다 — 안 그러면 사진 면에는 없는데 결함 수에는 남는다.
    dropped_defect_ids: list[int] = Field(default_factory=list)
    latency_ms: float = Field(ge=0.0)
    source_model: str
    status: Literal["ready", "fallback"]
