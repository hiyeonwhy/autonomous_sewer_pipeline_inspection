"""상태등급 판정 — 환경부 「하수관로 CCTV 조사 지침」 제6장 기준.

이전에는 심각도 개수를 세는 임시 규칙이었다. 이 모듈은 지침의 실제 절차를 따른다.

    [1단계] 단위구간(본, pipe segment) — 구간 내 결함점수의 **최대값**으로 등급 판정
    [2단계] 조사구간(맨홀 대 맨홀)   — 단위구간 등급의 **평균값**, 소수 둘째자리 반올림

핵심 차이 세 가지.
- 결함점수의 **최대값**을 쓴다. 합산하지 않는다(지침 1.1.1).
- 조사구간 값은 **정수 등급이 아니라 점수 형태**다(지침 표 6.1). 2.75 같은 값이 나온다.
- 최저 등급은 **1등급**이다. 결함이 없으면 0점이고 0~9점은 1등급(매우 좋음)이다.
  0등급은 지침에 존재하지 않는다.

결함점수 배점은 지침 **제5장 표 5.6(구조적) · 표 5.8(운영적)** 을 그대로 옮겼다.
표 5.6은 **강성관과 연성관 배점이 다르므로** 관종으로 갈라 쓴다.

한 가지 주의 — 제6장 표 6.5 산정 **예시**에는 표면손상(SD) 중 등급이 30점으로 적혀
있는데, 제5장 배점표는 강성관 35점 / 연성관 20점이다. 지침 안에서 어긋나므로 배점표인
제5장을 따랐다. (예전에 이 30점을 「확인값」으로 잘못 넣어 두었다.)

⚠️ 아직 지침과 다른 곳이 하나 남아 있다. 지침 1.1.2는 결함 등급(대/중/소)을
**관경(D) 변화율 또는 관둘레(C) 손실율 25% / 10%** 로 판정한다. 우리는 그 값을 재지
못해 바운딩박스가 화면에서 차지하는 면적 비율로 대신한다(`defects/severity.py`).
등급을 고르는 기준이 다르므로, 배점표가 맞아도 최종 등급은 근사치다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import DefectFact, DefectType, SessionFacts, Severity

# 지침 표 6.3(구조적) · 표 6.4(운영적). 두 표의 점수 구간은 동일하다.
# (하한 점수, 등급, 상태, 정비시급성)
GRADE_BANDS: tuple[tuple[int, int, str, str], ...] = (
    (70, 5, "매우 나쁨", "매우높음"),
    (40, 4, "나쁨", "높음"),
    (20, 3, "보통", "보통"),
    (10, 2, "좋음", "낮음"),
    (0, 1, "매우 좋음", "낮음"),
)

MIN_GRADE = 1
MAX_GRADE = 5
MAX_DEFECT_SCORE = 100

# 우리 결함 종류를 지침 결함코드에 대응시킨 것. 대응이 곧 배점을 정하므로 근거를 남긴다.
#
#   CRACK          → CL 균열-길이 (표 5.6). YOLO 가 잡는 균열은 축방향 선형이고,
#                    CL 은 연속결함으로 분류돼 우리 탐지 형태와 맞는다. 원주방향(CC)이나
#                    복합(CM)은 방향을 구분해야 하는데 지금 탐지기는 구분하지 않는다.
#   SURFACE_DAMAGE → SD 표면손상 (표 5.6)
#   DEBRIS         → DS 토사퇴적 (표 5.8)
#   ROOT           → RT 뿌리침입 (표 5.8)
#   BLOCKAGE       → PB 막힘 (표 5.8). 아래 주석 참고 — 대 등급만 존재한다.
#   OTHER          → 대응 코드 없음. 0점.
#
# 표 5.6 구조적 결함은 강성관/연성관 배점이 다르다. (대, 중, 소) 순서로 적는다.
_RIGID_STRUCTURAL_SCORES: dict[DefectType, tuple[int, int, int]] = {
    DefectType.CRACK: (35, 12, 4),            # 표 5.6 CL 강성관
    DefectType.SURFACE_DAMAGE: (60, 35, 20),  # 표 5.6 SD 강성관
}
_FLEXIBLE_STRUCTURAL_SCORES: dict[DefectType, tuple[int, int, int]] = {
    DefectType.CRACK: (40, 15, 5),            # 표 5.6 CL 연성관
    DefectType.SURFACE_DAMAGE: (45, 20, 10),  # 표 5.6 SD 연성관
}

# 표 5.8 운영적 결함은 관종 구분이 없다.
#
# 막힘(PB)은 「통수능 50% 이상 저하」 하나뿐이고 대 등급 100점만 있다. 그보다 약한
# 장애물은 지침이 임시장애물(TO, 대 50 / 중 25 / 소 5)로 따로 다룬다. 우리 탐지기는
# 둘을 구분하지 않으므로, BLOCKAGE 의 중·소는 TO 배점을 쓴다. 100점을 그대로 세 등급에
# 쓰면 작은 장애물 하나가 구간을 5등급으로 만든다.
_OPERATIONAL_SCORES: dict[DefectType, tuple[int, int, int]] = {
    DefectType.DEBRIS: (45, 15, 3),      # 표 5.8 DS
    DefectType.ROOT: (65, 33, 13),       # 표 5.8 RT
    DefectType.BLOCKAGE: (100, 25, 5),   # 표 5.8 PB 대 + TO 중·소
}

_SEVERITY_ORDER: tuple[Severity, ...] = (Severity.CRITICAL, Severity.WARNING, Severity.NORMAL)


def _expand(table: dict[DefectType, tuple[int, int, int]]) -> dict[tuple[DefectType, Severity], int]:
    return {
        (defect_type, severity): score
        for defect_type, scores in table.items()
        for severity, score in zip(_SEVERITY_ORDER, scores)
    }


# 관종을 모를 때 쓰는 표. 국내 하수관로에 흄관·콘크리트관이 가장 흔하므로 강성관을
# 기본으로 둔다 (`DEFAULT_SEGMENT_LENGTH_M` 이 흄관 기준인 것과 같은 이유).
DEFECT_SCORES: dict[tuple[DefectType, Severity], int] = {
    **_expand(_RIGID_STRUCTURAL_SCORES),
    **_expand(_OPERATIONAL_SCORES),
}
FLEXIBLE_DEFECT_SCORES: dict[tuple[DefectType, Severity], int] = {
    **_expand(_FLEXIBLE_STRUCTURAL_SCORES),
    **_expand(_OPERATIONAL_SCORES),
}

# 연성관 관종. 표 5.6 의 연성관 열을 쓴다.
FLEXIBLE_PIPE_KEYWORDS: tuple[str, ...] = ("PVC", "VG", "PE", "PP", "연성")


def is_flexible_pipe(pipe_type: str | None) -> bool:
    """관종 문자열이 연성관인지. 「PVC관(VG1)」처럼 표기가 섞여 오므로 부분 일치로 본다."""
    if not pipe_type:
        return False
    upper = pipe_type.upper()
    return any(keyword in upper for keyword in FLEXIBLE_PIPE_KEYWORDS)


def scores_for_pipe(pipe_type: str | None) -> dict[tuple[DefectType, Severity], int]:
    return FLEXIBLE_DEFECT_SCORES if is_flexible_pipe(pipe_type) else DEFECT_SCORES

# 지침 1.1.1: 구조적 / 운영적 결함을 나눠 각각 판정한다.
STRUCTURAL_DEFECTS = frozenset({DefectType.CRACK, DefectType.SURFACE_DAMAGE})
OPERATIONAL_DEFECTS = frozenset({DefectType.BLOCKAGE, DefectType.DEBRIS, DefectType.ROOT})

# 단위구간(본) 길이. 지침 1.1.1은 본단위를 쓰되, 이음부를 육안으로 못 찾으면
# "관종과 관경을 확인하고 해당 규격에서 일반적으로 사용하는 길이를 차용"하라고 한다.
# 우리 CCTV에는 이음부 검출이 없으므로 관종으로 추정한다. **표준 길이는 확인이 필요하다.**
SEGMENT_LENGTH_BY_PIPE_TYPE: dict[str, float] = {
    "흄관": 2.5,
    "HP": 2.5,
    "콘크리트": 2.5,
    "PVC": 4.0,
    "VG1": 4.0,
    "PE": 6.0,
}
DEFAULT_SEGMENT_LENGTH_M = 2.5


def defect_score(defect: DefectFact, pipe_type: str | None = None) -> int:
    """결함 1건의 결함점수. 표에 없으면 0으로 두어 등급을 부풀리지 않는다.

    `OTHER`가 0점이 되는 것은 의도한 결과다. 지침에 대응 코드가 없는 결함에 점수를
    지어내면 등급이 근거 없이 오르내린다. 지침도 판단할 수 없는 이상항목은 상태항목
    (특이사항 SC)으로 기록하고 점수를 주지 않는다.
    """
    return scores_for_pipe(pipe_type).get((defect.defect_type, defect.severity), 0)


def grade_for_score(score: float) -> int:
    """결함점수 최대값 → 상태등급 1~5 (지침 표 6.3 / 6.4)."""
    bounded = max(0.0, min(float(MAX_DEFECT_SCORE), float(score)))
    for threshold, grade, _state, _urgency in GRADE_BANDS:
        if bounded >= threshold:
            return grade
    return MIN_GRADE


def grade_state(grade: int) -> str:
    for _threshold, value, state, _urgency in GRADE_BANDS:
        if value == grade:
            return state
    return "판정 불가"


def grade_urgency(grade: int) -> str:
    for _threshold, value, _state, urgency in GRADE_BANDS:
        if value == grade:
            return urgency
    return "판정 불가"


def segment_length_for(pipe_type: str | None) -> float:
    """관종 문자열에서 단위구간 길이를 추정한다. 예: "흄관(HP)" → 2.5"""
    if not pipe_type:
        return DEFAULT_SEGMENT_LENGTH_M
    for keyword, length in SEGMENT_LENGTH_BY_PIPE_TYPE.items():
        if keyword in pipe_type:
            return length
    return DEFAULT_SEGMENT_LENGTH_M


class SegmentGrade(BaseModel):
    """단위구간(본) 하나의 판정 결과."""

    index: int = Field(ge=1)
    start_m: float = Field(ge=0.0)
    end_m: float = Field(ge=0.0)
    structural_score: int = Field(ge=0, le=MAX_DEFECT_SCORE)
    structural_grade: int = Field(ge=MIN_GRADE, le=MAX_GRADE)
    operational_score: int = Field(ge=0, le=MAX_DEFECT_SCORE)
    operational_grade: int = Field(ge=MIN_GRADE, le=MAX_GRADE)
    defect_count: int = Field(default=0, ge=0)


def build_segments(
    facts: SessionFacts, *, pipe_type: str | None = None, pipe_length_m: float | None = None
) -> list[SegmentGrade]:
    """결함을 단위구간으로 묶고 구간별 등급을 판정한다(지침 1단계).

    각 구간은 그 구간 안 결함점수의 **최대값**으로 등급을 받는다. 결함이 없는 구간도
    1등급으로 포함해야 2단계 평균이 맞는다 — 결함 있는 구간만 평균하면 값이 부풀려진다.
    """
    length = segment_length_for(pipe_type)

    # 관로 연장이 있으면 그것으로 구간 수를 정한다. 없으면 결함이 나온 곳까지만 본다.
    span = pipe_length_m if pipe_length_m and pipe_length_m > 0 else None
    if span is None:
        furthest = max((defect.position_m for defect in facts.defects), default=0.0)
        span = max(furthest, length)
    total = max(1, int((span - 1e-9) // length) + 1)

    buckets: list[list[DefectFact]] = [[] for _ in range(total)]
    for defect in facts.defects:
        index = min(total - 1, int(defect.position_m // length))
        buckets[index].append(defect)

    segments: list[SegmentGrade] = []
    for index, defects in enumerate(buckets):
        structural = max(
            (
                defect_score(d, pipe_type)
                for d in defects
                if d.defect_type in STRUCTURAL_DEFECTS
            ),
            default=0,
        )
        operational = max(
            (
                defect_score(d, pipe_type)
                for d in defects
                if d.defect_type in OPERATIONAL_DEFECTS
            ),
            default=0,
        )
        segments.append(
            SegmentGrade(
                index=index + 1,
                start_m=round(index * length, 2),
                end_m=round((index + 1) * length, 2),
                structural_score=structural,
                structural_grade=grade_for_score(structural),
                operational_score=operational,
                operational_grade=grade_for_score(operational),
                defect_count=len(defects),
            )
        )
    return segments


def average_grade(values: list[int]) -> float:
    """조사구간 상태점수 — 단위구간 등급의 평균, 소수 둘째자리 반올림(지침 표 6.1)."""
    if not values:
        return float(MIN_GRADE)
    return round(sum(values) / len(values), 2)
