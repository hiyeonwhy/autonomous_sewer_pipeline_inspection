"""모델을 못 쓸 때의 규칙 기반 개조식 문장.

바닥값이지 목표가 아니다. 없는 사실을 만들지 않는 대신 조사자의 문장이라기보다
조립된 문장으로 읽힌다. 모델 호출이나 검증이 실패해도 보고서가 비지 않게 한다.
"""

from __future__ import annotations

from app.domain.reports.document import DEFECT_TYPE_LABELS, FAILURE_LABELS, SEVERITY_LABELS

from .grading import grade_state, grade_urgency
from .models import (
    DefectFact,
    DefectObservation,
    ObservationFailure,
    ReportNarrative,
    SessionFacts,
    Severity,
    StateGrades,
)

def grade_label(grade: int) -> str:
    """환경부 지침 표 6.3 / 6.4의 상태 표현. 0등급은 지침에 없다 — 최저가 1등급이다."""
    return grade_state(grade)


def _describe(defect: DefectFact) -> str:
    kind = DEFECT_TYPE_LABELS[defect.defect_type.value]
    level = SEVERITY_LABELS[defect.severity.value]
    return f"{defect.position_m:g}m 지점 {kind}({level})"


def _grade_phrase(score: float, grade: int) -> str:
    """조사구간 상태점수와 환산 등급을 함께 쓴다.

    지침 표 6.1이 조사구간 값을 "별도의 등급 없이 상태점수 형태로 제시"하라고 하므로
    점수를 앞세우고, 읽는 쪽이 시급성을 판단할 수 있게 등급과 상태를 덧붙인다.
    """
    return f"{score:g}점({grade}등급, {grade_state(grade)})"


def render_fallback_narrative(facts: SessionFacts, grades: StateGrades) -> ReportNarrative:
    if facts.defect_total == 0:
        return ReportNarrative(
            assessment="\n".join(
                [
                    f"{facts.region}의 이번 탐사에서 결함 미확인.",
                    f"단위구간 {grades.segment_count}개소 전부 결함점수 0점으로, "
                    "구조적·운영적 조사구간 상태점수 모두 1점(1등급, 매우 좋음)으로 산정됨.",
                ]
            ),
            recommendations=["정기 점검 주기에 따른 재점검 필요."],
        )

    critical = [defect for defect in facts.defects if defect.severity is Severity.CRITICAL]
    warning = [defect for defect in facts.defects if defect.severity is Severity.WARNING]

    sentences = [f"{facts.region}의 이번 탐사에서 결함 {facts.defect_total}건 확인됨."]
    if critical:
        listed = ", ".join(_describe(defect) for defect in critical[:3])
        sentences.append(f"심각 등급 결함 {len(critical)}건으로 {listed} 등이 해당됨.")
    if warning:
        sentences.append(f"주의 등급 결함 {len(warning)}건 확인됨.")
    sentences.append(
        f"단위구간 {grades.segment_count}개소(구간당 {grades.segment_length_m:g}m) 기준으로 "
        f"구조적 조사구간 상태점수 {_grade_phrase(grades.structural, grades.structural_grade)}, "
        f"운영적 조사구간 상태점수 {_grade_phrase(grades.operational, grades.operational_grade)}로 산정됨."
    )
    if facts.dropped_defect_count:
        sentences.append(
            f"결함 {facts.dropped_defect_count}건은 요약 대상에서 제외되었으며 상세 목록에서 확인 가능."
        )

    recommendations: list[str] = []
    if critical:
        nearest = min(critical, key=lambda defect: defect.position_m)
        kind = DEFECT_TYPE_LABELS[nearest.defect_type.value]
        recommendations.append(
            f"{nearest.position_m:g}m 지점 {kind}에 대한 정밀 조사 및 보수 우선 검토 필요."
        )
    # 지침 제6장 <상태등급 활용 예시>: 조사구간 구조적 상태등급 4.0 이상이면
    # 조사구간 전체에 대해 갱신 또는 교체를 진행해야 한다.
    if grades.structural >= 4.0:
        recommendations.append(
            f"구조적 조사구간 상태점수 {grades.structural:g}점으로 4.0 이상에 해당하여 "
            "조사구간 전체에 대한 갱신 또는 교체 진행 필요."
        )
    if warning:
        recommendations.append("주의 등급 결함 구간은 차기 점검 시 상태 변화 재확인 필요.")
    if grades.operational_grade >= 3:
        recommendations.append("통수 능력 저해 구간에 대한 고압 세정 검토 필요.")
    if grades.structural_grade >= 3 or grades.operational_grade >= 3:
        recommendations.append(
            f"정비시급성 구조적 {grade_urgency(grades.structural_grade)} · "
            f"운영적 {grade_urgency(grades.operational_grade)} 수준으로 판단됨."
        )
    if not recommendations:
        recommendations.append("확인된 결함은 경미하며 정기 점검 주기 내 관찰 유지.")

    # 줄바꿈으로 잇는다 — 양식이 줄마다 한 항목으로 쌓으므로 LLM 요약과 같은 모양이
    # 되어야 한다. 공백으로 이으면 규칙 기반일 때만 한 덩어리로 나온다.
    return ReportNarrative(assessment="\n".join(sentences), recommendations=recommendations)


def render_detection_description(defect: DefectFact) -> str:
    """탐지 결과(JSON) 기반 서술. 모델이 관여하지 않는 결정적 문장이다.

    사진을 읽었든 못 읽었든 항상 이 문장이 들어간다. 사진 묘사와 나란히 놓고
    읽는 쪽이 둘을 비교할 수 있게 하려는 것이다.
    """
    kind = DEFECT_TYPE_LABELS[defect.defect_type.value]
    level = SEVERITY_LABELS[defect.severity.value]

    # 로봇에 주행계가 없어 위치가 늘 0m 로 온다. 「0m 지점에서」라고 쓰면 관 입구에서
    # 발견했다는 없는 사실이 보고서에 남는다 — 실측이 있을 때만 위치를 적는다.
    where = f"{defect.position_m:g}m 지점에서 " if defect.position_m > 0 else ""
    return (
        f"{where}{kind} 탐지됨. "
        f"심각도 {level} 등급으로 분류됨. "
        f"탐지 신뢰도 {defect.confidence * 100:.0f}%."
    )


def describe_photo_failure(
    failure_code: ObservationFailure, failure_detail: str | None = None
) -> str:
    """사진을 읽지 못한 이유를 사람이 읽을 문장으로.

    JSON이 아니라 렌더된 보고서만 보는 검토자도 왜 묘사가 비었는지 알아야 한다.
    """
    reason = FAILURE_LABELS.get(failure_code.value, "판독 미수행")
    text = f"사진 판독 미수행({failure_code.value}: {reason})"
    if failure_detail:
        text += f" — {failure_detail.strip()[:120]}"
    return f"{text}."


def render_fallback_observation(
    defect: DefectFact,
    *,
    failure_code: ObservationFailure = ObservationFailure.MODEL_UNAVAILABLE,
    failure_detail: str | None = None,
) -> DefectObservation:
    """사진이 없거나 판독이 실패했을 때 — 탐지 기반 서술만 남는다."""
    return DefectObservation(
        defect_id=defect.defect_id,
        position_m=defect.position_m,
        defect_type=defect.defect_type,
        severity=defect.severity,
        description=render_detection_description(defect),
        photo_description=None,
        source="metadata",
        failure_code=failure_code,
        # 길면 잘라 담는다. 게이트웨이가 응답 본문을 그대로 실어 보내면 수백 바이트가
        # 되는데, 원인을 가리는 앞부분만 있으면 충분하다.
        failure_detail=failure_detail[:300] if failure_detail else None,
    )
