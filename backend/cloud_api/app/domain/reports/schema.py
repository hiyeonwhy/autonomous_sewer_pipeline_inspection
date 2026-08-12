from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 조사목적은 현재 단일 사업으로 고정한다. 사업이 늘어나면 세션 입력값으로 옮긴다.
DEFAULT_SURVEY_PURPOSE = "노후 하수관로 점검"

# 관로 제원과 주행 거리는 원래 세션의 `pipe`/`survey`에서 스냅샷으로 가져온다.
# 그 정보 없이 만들어진 세션도 있어서 양식에 빈칸이 생기는데, 조사보고서 양식은
# 빈칸을 두면 읽히지 않으므로 아래 값으로 메운다.
#
# 저장값을 바꾸지 않고 응답에서만 메운다(`convert_report`). 세션에 실제 제원이
# 있으면 그것이 그대로 쓰이고, 여기 값은 절대 덮어쓰지 않는다.
DEFAULT_PIPE_FIELDS: dict[str, object] = {
    "survey_location": "광주광역시 광산구 오선동",
    "inspection_section": "광주광역시 광산구 오선동 구간1",
    "pipe_no": "3021547",
    "pipe_category": "관로",
    "pipe_type": "PVC관(VG1)",
    "diameter_mm": 375,
    "drainage_type": "오수",
    "pipe_length_m": 2.0,
    "total_distance_m": 2.0,
    "undriven_distance_m": 0.0,
}

# 양식 1면에 들어가는 결함 사진 수.
#
# 사진 상자(67.3×50.5mm)만 보면 4행이 들어가지만, 행 높이를 정하는 것은 사진이 아니라
# **판독 소견 문장**이다. 소견이 6~8줄이면 한 행이 60~70mm가 되어 4행은 한 면을 넘긴다.
# 실측에서 3행이 A4 한 면을 거의 꽉 채웠으므로 3으로 둔다.
DEFECTS_PER_PAGE = 3


class DefectObservation(BaseModel):
    """ai/llm_report가 생성한 결함별 판독 소견."""

    defect_id: int | None = None
    position_m: float = Field(ge=0, le=10000)
    defect_type: str
    severity: str
    # 탐지 결과(JSON) 기반 서술. 코드가 만든다.
    description: str
    # 사진을 보고 쓴 묘사. 탐지 결과는 참고로만 주고 확인 여부를 판단하게 한다.
    photo_description: str | None = None
    source: Literal["vision", "metadata"] = "metadata"
    failure_code: str | None = None
    # 실패 원인 원문. 서버 로그를 못 보는 사람도 API 로 원인을 볼 수 있게 남긴다.
    failure_detail: str | None = Field(default=None, max_length=300)


class ReportSummaryUpdateRequest(BaseModel):
    """llm_report가 생성한 서술형 요약을 보고서에 붙인다."""

    assessment: str | None = Field(default=None, max_length=4000)
    recommendations: list[str] | None = Field(default=None, max_length=20)
    observations: list[DefectObservation] | None = Field(default=None, max_length=200)
    # 환경부 지침 표 6.1: 조사구간(맨홀 대 맨홀) 상태는 정수 등급이 아니라 상태점수다.
    # 단위구간 등급의 평균이라 2.75 같은 값이 나오고, 최저값은 1.0이다(0등급 없음).
    structural_grade: float | None = Field(default=None, ge=1.0, le=5.0)
    operational_grade: float | None = Field(default=None, ge=1.0, le=5.0)
    # 위 점수를 환산한 정수 등급(표 6.3 / 6.4). 정비시급성 판단과 집계표에 쓴다.
    structural_grade_class: int | None = Field(default=None, ge=1, le=5)
    operational_grade_class: int | None = Field(default=None, ge=1, le=5)
    segment_count: int | None = Field(default=None, ge=1)
    segment_length_m: float | None = Field(default=None, gt=0.0)
    # 배점을 가른 관종 구분. "연성관" 또는 "강성관".
    grade_score_basis: str | None = Field(default=None, max_length=64)
    # 결함 등급(대/중/소)을 관경·관둘레 대신 면적 비율로 정했는지. 각주에 밝힌다.
    grade_scores_estimated: bool | None = None
    summary_model: str | None = Field(default=None, max_length=64)


class ReportResponse(BaseModel):
    id: int
    report_no: str
    session_id: int
    # 서술형 요약의 주어로만 쓰인다. 양식에 실리는 것은 아래 두 필드다.
    region: str
    # 양식 「점검 위치」 — 동·주소
    survey_location: str | None = None
    # 양식 「점검 구간」 — 동 안의 구간. 프론트에서 고른 값이 세션을 거쳐 온다.
    inspection_section: str | None = None
    survey_purpose: str = DEFAULT_SURVEY_PURPOSE
    robot_name: str
    robot_model: str
    operator_name: str
    operator_role: str
    inspection_date: str
    duration: str

    # 조사 시점의 관로 제원 스냅샷 — 이후 관로 대장이 바뀌어도 보고서는 당시 값을 유지한다.
    pipe_no: str | None = None
    pipe_category: str | None = None
    pipe_type: str | None = None
    diameter_mm: int | None = None
    drainage_type: str | None = None
    pipe_length_m: float | None = None
    total_distance_m: float | None = None
    undriven_distance_m: float | None = None

    total_defect_count: int
    critical_defect_count: int
    summary: str

    # llm_report가 채우는 서술형 부분. 요약을 돌리기 전에는 비어 있다.
    assessment: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    observations: list[DefectObservation] = Field(default_factory=list)
    # 조사구간 상태점수(소수) + 환산 정수 등급. 환경부 지침 제6장 참조.
    structural_grade: float | None = None
    operational_grade: float | None = None
    structural_grade_class: int | None = None
    operational_grade_class: int | None = None
    segment_count: int | None = None
    segment_length_m: float | None = None
    grade_score_basis: str | None = None
    grade_scores_estimated: bool | None = None
    summary_model: str | None = None

    generated_at: datetime
