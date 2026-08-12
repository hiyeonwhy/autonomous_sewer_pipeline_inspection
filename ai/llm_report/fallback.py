"""Deterministic 개조식 text for when the model is unavailable or rejected.

This is the floor, not the target: it stays strictly factual and never invents a
finding, but it reads as assembled text rather than an inspector's prose.
"""

from __future__ import annotations

from .models import (
    DEFECT_TYPE_LABELS,
    FAILURE_LABELS,
    SEVERITY_LABELS,
    DefectFact,
    DefectObservation,
    ObservationFailure,
    ReportNarrative,
    SessionFacts,
    Severity,
    StateGrades,
)

GRADE_LABELS: dict[int, str] = {
    0: "결함 미확인",
    1: "양호",
    2: "경미한 결함 확인",
    3: "보통",
    4: "불량",
    5: "매우 불량",
}


def grade_label(grade: int) -> str:
    return GRADE_LABELS.get(grade, "판정 불가")


def _describe(defect: DefectFact) -> str:
    kind = DEFECT_TYPE_LABELS[defect.defect_type]
    level = SEVERITY_LABELS[defect.severity]
    return f"{defect.position_m:g}m 지점 {kind}({level})"


def render_fallback_narrative(facts: SessionFacts, grades: StateGrades) -> ReportNarrative:
    if facts.defect_total == 0:
        return ReportNarrative(
            assessment=(
                f"{facts.district_id}번 점검 구역의 이번 탐사에서 결함 미확인. "
                "구조적·운영적 상태등급 모두 결함 미확인으로 산정됨."
            ),
            recommendations=["정기 점검 주기에 따른 재점검 필요."],
        )

    critical = [defect for defect in facts.defects if defect.severity is Severity.CRITICAL]
    warning = [defect for defect in facts.defects if defect.severity is Severity.WARNING]

    sentences = [
        f"{facts.district_id}번 점검 구역의 이번 탐사에서 결함 {facts.defect_total}건 확인됨."
    ]
    if critical:
        listed = ", ".join(_describe(defect) for defect in critical[:3])
        sentences.append(f"심각 등급 결함 {len(critical)}건으로 {listed} 등이 해당됨.")
    if warning:
        sentences.append(f"주의 등급 결함 {len(warning)}건 확인됨.")
    sentences.append(
        f"구조적 상태등급 {grades.structural}등급({grade_label(grades.structural)}), "
        f"운영적 상태등급 {grades.operational}등급({grade_label(grades.operational)})으로 산정됨."
    )
    if facts.dropped_defect_count:
        sentences.append(
            f"결함 {facts.dropped_defect_count}건은 요약 대상에서 제외되었으며 상세 목록에서 확인 가능."
        )

    recommendations: list[str] = []
    if critical:
        nearest = min(critical, key=lambda defect: defect.position_m)
        kind = DEFECT_TYPE_LABELS[nearest.defect_type]
        recommendations.append(
            f"{nearest.position_m:g}m 지점 {kind}에 대한 정밀 조사 및 보수 우선 검토 필요."
        )
    if warning:
        recommendations.append("주의 등급 결함 구간은 차기 점검 시 상태 변화 재확인 필요.")
    if grades.operational >= 3:
        recommendations.append("통수 능력 저해 구간에 대한 고압 세정 검토 필요.")
    if not recommendations:
        recommendations.append("확인된 결함은 경미하며 정기 점검 주기 내 관찰 유지.")

    return ReportNarrative(assessment=" ".join(sentences), recommendations=recommendations)


def render_fallback_observation(
    defect: DefectFact,
    *,
    image_url: str | None = None,
    failure_code: ObservationFailure = ObservationFailure.MODEL_UNAVAILABLE,
    failure_detail: str | None = None,
) -> DefectObservation:
    """Metadata-only observation — used when no snapshot is available or vision fails.

    The failure reason is written into the description so a reviewer reading the
    rendered report (not the JSON) can still tell why the photo was not read.
    """
    kind = DEFECT_TYPE_LABELS[defect.defect_type]
    level = SEVERITY_LABELS[defect.severity]

    parts = [f"{defect.position_m:g}m 지점에서 {kind} 확인됨. 심각도 {level} 등급으로 분류됨."]
    if defect.blockage_ratio is not None:
        parts.append(f"단면 폐색률 {defect.blockage_ratio * 100:.0f}%로 측정됨.")
    parts.append(f"탐지 신뢰도 {defect.confidence * 100:.0f}%.")

    reason = FAILURE_LABELS[failure_code]
    trailer = f"사진 판독 미수행({failure_code.value}: {reason})"
    if failure_detail:
        trailer += f" — {failure_detail.strip()[:120]}"
    parts.append(f"{trailer}. 메타데이터 기준 소견임.")

    return DefectObservation(
        defect_id=defect.defect_id,
        position_m=defect.position_m,
        defect_type=defect.defect_type,
        severity=defect.severity,
        image_url=image_url,
        description=" ".join(parts),
        source="metadata",
        failure_code=failure_code,
        failure_detail=failure_detail,
    )
