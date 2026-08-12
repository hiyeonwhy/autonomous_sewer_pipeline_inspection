from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .models import DefectFact, DefectType, SessionFacts, Severity, StateGrades

# A 3B model degrades quickly on long inputs. Cap what enters the prompt and
# report the remainder in `dropped_defect_count` rather than truncating silently.
MAX_PROMPT_DEFECTS = 30

# 구조적 결함은 관 자체의 손상, 운영적 결함은 통수 능력 저해로 분류한다.
STRUCTURAL_DEFECTS = frozenset({DefectType.CRACK, DefectType.SURFACE_DAMAGE})
OPERATIONAL_DEFECTS = frozenset({DefectType.BLOCKAGE, DefectType.DEBRIS, DefectType.ROOT})


def bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return minimum
    if not math.isfinite(number):
        return minimum
    return min(maximum, max(minimum, number))


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # Python 3.11+ fromisoformat accepts a trailing "Z" directly.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _enum_or_default(value: object, enum_type: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return enum_type(value.strip().upper())
    except ValueError:
        return default


def sanitize_snapshot_path(value: object) -> str | None:
    """Accept only the Cloud API's own media paths.

    `snapshot_path` comes from the database, so an absolute URL or a traversal
    segment there would let a poisoned record point the image fetch at an
    arbitrary host. Restrict it to `/media/...` before anything downloads it.
    """
    if not isinstance(value, str):
        return None
    path = value.strip()
    if not path.startswith("/media/") or ".." in path or "//" in path[1:]:
        return None
    return path


def sanitize_defect(document: dict[str, object]) -> DefectFact:
    """Reduce one Cloud API defect document to bounded, allowlisted facts."""
    raw_ratio = document.get("blockage_ratio")
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
        blockage_ratio=(
            None
            if raw_ratio is None
            else round(bounded_number(raw_ratio, minimum=0.0, maximum=1.0), 3)
        ),
        snapshot_path=sanitize_snapshot_path(document.get("snapshot_path")),
    )


def build_session_facts(
    session: dict[str, object],
    defects: list[dict[str, object]],
    *,
    max_defects: int = MAX_PROMPT_DEFECTS,
) -> SessionFacts:
    """Build prompt-safe facts from raw Cloud API payloads.

    Defects are ordered by position so the narrative can walk the pipe from the
    start point, then capped at `max_defects` with the remainder counted.
    """
    sanitized = sorted(
        (sanitize_defect(defect) for defect in defects),
        key=lambda defect: defect.position_m,
    )
    kept = sanitized[:max_defects]
    started_at = _parse_timestamp(session.get("started_at"))
    ended_at = _parse_timestamp(session.get("ended_at"))

    duration_seconds: int | None = None
    if started_at is not None and ended_at is not None:
        elapsed = (ended_at - started_at).total_seconds()
        if math.isfinite(elapsed) and elapsed >= 0:
            duration_seconds = int(elapsed)

    return SessionFacts(
        session_id=int(bounded_number(session.get("id"), minimum=0, maximum=1e12)),
        district_id=int(bounded_number(session.get("district_id"), minimum=0, maximum=1e12)),
        mode=str(session.get("mode", "UNKNOWN")),
        status=str(session.get("status", "UNKNOWN")),
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        defects=kept,
        dropped_defect_count=len(sanitized) - len(kept),
    )


def _grade_for(defects: list[DefectFact], categories: frozenset[DefectType]) -> int:
    """상태등급 0(결함 없음) ~ 5(매우 불량).

    NOTE: 환경부 하수관로 상태평가 기준으로 검증되지 않은 임시 규칙이다.
    실제 등급 산정 기준이 확정되면 이 함수만 교체하면 된다.
    """
    matched = [defect for defect in defects if defect.defect_type in categories]
    if not matched:
        return 0
    critical = sum(1 for defect in matched if defect.severity is Severity.CRITICAL)
    warning = sum(1 for defect in matched if defect.severity is Severity.WARNING)
    if critical:
        return 5 if critical >= 2 else 4
    if warning:
        return 3 if warning >= 2 else 2
    return 1


def compute_grades(facts: SessionFacts) -> StateGrades:
    return StateGrades(
        structural=_grade_for(facts.defects, STRUCTURAL_DEFECTS),
        operational=_grade_for(facts.defects, OPERATIONAL_DEFECTS),
    )


def allowed_distances(facts: SessionFacts) -> set[str]:
    """Distance literals the narrative is permitted to mention.

    Used to reject hallucinated positions: the model may only cite defect
    positions that actually appear in the facts.
    """
    allowed: set[str] = set()
    for defect in facts.defects:
        allowed.add(f"{defect.position_m:g}")
        allowed.add(f"{defect.position_m:.1f}")
        allowed.add(str(int(defect.position_m)))
    return allowed
