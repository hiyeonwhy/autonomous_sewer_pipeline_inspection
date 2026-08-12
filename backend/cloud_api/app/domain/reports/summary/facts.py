"""Mongo 문서를 프롬프트에 넣어도 안전한 사실로 정제하고 상태등급을 계산한다.

숫자는 코드가, 문장은 모델이 만든다는 원칙을 여기서 지킨다. 등급은 보고서의
근거값이므로 모델이 판정하게 두지 않는다.
"""

from __future__ import annotations

import math
from typing import Any

from .models import DefectFact, DefectType, SessionFacts, Severity, StateGrades

# 프롬프트에 넣는 결함 수 상한. 초과분은 `dropped_defect_count`로 세어 보고하고
# 조용히 잘라내지 않는다.
MAX_PROMPT_DEFECTS = 30


def bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return minimum
    if not math.isfinite(number):
        return minimum
    return min(maximum, max(minimum, number))


def _enum_or_default(value: object, enum_type: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return enum_type(value.strip().upper())
    except ValueError:
        return default


def sanitize_snapshot_path(value: object) -> str | None:
    """Cloud API 자신의 media 경로만 통과시킨다.

    `snapshot_path`는 DB에서 오므로, 절대 URL이나 상위 경로 세그먼트가 섞이면 사진
    읽기를 임의 위치로 돌릴 수 있다. 사진을 열기 전에 `/media/` 하위로 제한한다.
    실제 디스크 경로 해석은 `document.resolve_media_file`이 한 번 더 검증한다.
    """
    if not isinstance(value, str):
        return None
    path = value.strip()
    if not path.startswith("/media/") or ".." in path or "//" in path[1:]:
        return None
    return path


def sanitize_defect(document: dict[str, Any]) -> DefectFact:
    """Mongo 결함 문서 1건을 허용 목록·범위로 죈 사실로 줄인다."""
    raw_id = document.get("id")
    return DefectFact(
        defect_id=int(raw_id) if isinstance(raw_id, int) else None,
        defect_type=_enum_or_default(document.get("defect_type"), DefectType, DefectType.OTHER),
        severity=_enum_or_default(document.get("severity"), Severity, Severity.NORMAL),
        confidence=round(bounded_number(document.get("confidence"), minimum=0.0, maximum=1.0), 3),
        position_m=round(
            bounded_number(document.get("approx_position_m"), minimum=0.0, maximum=10000.0),
            1,
        ),
        snapshot_path=sanitize_snapshot_path(document.get("snapshot_path")),
        photo_description=(
            document.get("photo_description")
            if isinstance(document.get("photo_description"), str)
            and document.get("photo_description").strip()
            else None
        ),
    )


def build_session_facts(
    report: dict[str, Any],
    defects: list[dict[str, Any]],
    *,
    max_defects: int = MAX_PROMPT_DEFECTS,
) -> SessionFacts:
    """보고서 문서와 그 세션의 결함으로 프롬프트용 사실을 만든다.

    결함은 위치 순으로 정렬해 서술이 관로를 기점부터 훑을 수 있게 하고,
    `max_defects`에서 자른 뒤 나머지는 개수만 남긴다.
    """
    sanitized = sorted(
        (sanitize_defect(defect) for defect in defects),
        key=lambda defect: defect.position_m,
    )
    kept = sanitized[:max_defects]

    return SessionFacts(
        report_id=int(bounded_number(report.get("id"), minimum=0, maximum=1e12)),
        session_id=int(bounded_number(report.get("session_id"), minimum=0, maximum=1e12)),
        region=str(report.get("region") or "점검 구역"),
        survey_location=(
            str(report["survey_location"]) if report.get("survey_location") else None
        ),
        duration=str(report["duration"]) if report.get("duration") else None,
        defects=kept,
        dropped_defect_count=len(sanitized) - len(kept),
    )


def compute_grades(
    facts: SessionFacts, *, pipe_type: str | None = None, pipe_length_m: float | None = None
) -> StateGrades:
    """환경부 지침 제6장 절차로 상태등급을 판정한다.

    1단계 단위구간(결함점수 최대값) → 2단계 조사구간(단위구간 등급 평균).
    실제 계산은 `grading.py`에 있다 — 지침 조항과 1:1로 대응시켜 두려는 것이다.
    """
    from .grading import average_grade, build_segments, is_flexible_pipe

    segments = build_segments(facts, pipe_type=pipe_type, pipe_length_m=pipe_length_m)
    structural = average_grade([segment.structural_grade for segment in segments])
    operational = average_grade([segment.operational_grade for segment in segments])

    return StateGrades(
        structural=structural,
        operational=operational,
        # 요약 문장과 정비시급성에는 정수 등급이 필요하다. 점수를 그대로 환산하지 않고
        # 반올림하면 2.5가 3등급이 되어 지침의 점수 구간과 어긋나므로, 조사구간 점수를
        # 그대로 등급 구간에 대입하지 않고 가장 가까운 정수 등급으로 올림한다.
        structural_grade=max(1, min(5, round(structural))),
        operational_grade=max(1, min(5, round(operational))),
        segment_count=len(segments),
        segment_length_m=segments[0].end_m - segments[0].start_m if segments else 2.5,
        # 보고서 각주에 그대로 실린다. 표 번호(표 5.6 등)는 넣지 않는다 — 지침을 읽지
        # 않은 사람에게는 아무 정보가 아니고, 배점이 관종에 따라 갈린다는 사실만이
        # 보고서를 읽는 데 필요하다. 표 번호는 `grading.py` 주석에 있다.
        score_basis="연성관" if is_flexible_pipe(pipe_type) else "강성관",
        # 자동 탐지에서는 관경·관둘레를 재지 못해 결함 등급이 언제나 근사다.
        uses_estimated_scores=True,
    )


def allowed_distances(facts: SessionFacts, grades: StateGrades | None = None) -> set[str]:
    """서술이 인용해도 되는 거리 값.

    환각 위치를 걸러내는 데 쓴다. **모델에게 준 값은 환각이 아니다** — 결함 위치와,
    입력에 들어가는 단위구간 길이(`segmentLengthM`)가 여기 포함된다. 구간 길이를
    빼두면 모델이 "구간당 4.0m"라고 정직하게 인용해도 위치 환각으로 걷어차이고
    요약이 규칙 기반으로 강등된다(실측에서 3회 연속 발생).
    """
    allowed: set[str] = set()

    def add(value: float) -> None:
        allowed.add(f"{value:g}")
        allowed.add(f"{value:.1f}")
        allowed.add(str(int(value)))

    for defect in facts.defects:
        # 위치 실측이 없으면(늘 0m) 프롬프트에도 안 주므로 허용 목록에서도 뺀다.
        # 그래야 모델이 그래도 「0.0m 지점」이라고 쓰면 환각으로 걸린다.
        if defect.position_m > 0:
            add(defect.position_m)
    if grades is not None:
        add(grades.segment_length_m)
    return allowed
