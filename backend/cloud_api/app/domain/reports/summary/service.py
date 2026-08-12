"""보고서 요약 오케스트레이션.

분업은 의도적이다. 결함 수와 상태등급은 코드가 계산하고, 문장만 모델이 쓴다.
모델·사진 실패는 그 조각만 규칙 기반으로 강등하고 요약 생성 자체는 막지 않는다.
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domain.reports.document import resolve_media_file

from .facts import build_session_facts, compute_grades
from .fallback import (
    describe_photo_failure,
    render_detection_description,
    render_fallback_narrative,
    render_fallback_observation,
)
from .models import (
    DefectFact,
    DefectObservation,
    ObservationFailure,
    ReportNarrative,
    ReportSummary,
    SessionFacts,
    StateGrades,
)
from .prereading import MIN_VISIBLE_FRACTION, NEVER_REJECTED_TYPES, visible_fraction
from .prompts import (
    NARRATIVE_SYSTEM_PROMPT,
    PHOTO_SYSTEM_PROMPT,
    build_narrative_payload,
    build_photo_payload,
    parse_narrative_text,
    parse_photo_description_text,
)
from .provider import ChatProvider, build_provider

logger = logging.getLogger(__name__)

# 사진 판독은 결함 1건마다 별도 호출이라 밀리초가 아니라 초 단위로 든다.
# 몇 건까지 읽을지 묶고, 나머지는 세어서 보고한다.
DEFAULT_MAX_VISION_DEFECTS = 8

# 사진 1장을 몇 번까지 읽어 볼지. 게이트웨이의 응답 잘림 때문에 나는 실패는
# 다시 걸면 대개 통과한다. 2회면 관측된 실패율(5회 중 2회)을 4% 아래로 낮춘다.
# 더 늘려 봐야 보고서만 느려진다.
PHOTO_READ_ATTEMPTS = 2

# 종합 평가 문단을 몇 번까지 써 보게 할지. 사진과 달리 문단은 하나뿐이라, 한 번
# 걸렸다고 떨구면 보고서 첫 장이 통째로 규칙 기반 문장이 된다.
NARRATIVE_ATTEMPTS = 2

# 2회까지 다시 걸어도 안 되면 **그 사진 한 장의 문제**로 본다 — 응답이 계속 잘렸거나,
# 검증기가 되돌렸거나, 파일을 못 읽었다. 이런 건은 보고서에서 뺀다. 읽지도 못한 사진에
# 「판독 미수행」만 달아 싣는 것은 검토자에게 줄 게 없다.
#
# 나머지(MODEL_UNAVAILABLE, BUDGET_EXCEEDED, NO_SNAPSHOT)는 사진 탓이 아니라 설비
# 탓이라 빼지 않는다. 그걸로 빼면 판독 서버가 죽은 날 보고서가 통째로 비어 버린다.
PER_PHOTO_FAILURES = frozenset(
    {
        ObservationFailure.VALIDATION_REJECTED,
        ObservationFailure.INFERENCE_FAILED,
        ObservationFailure.IMAGE_FETCH_FAILED,
    }
)

# 게이트웨이가 받아 주는 요청 본문 상한에서 역산한 사진 1장의 예산.
# SSAFY GMS는 본문 약 100KB에서 400을 내고, base64가 약 1.37배로 부풀린다.
DEFAULT_IMAGE_BUDGET_BYTES = 60 * 1024

# 결함 사진으로 받아들일 형식. 여기 없는 확장자는 판독하지 않는다.
_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# 디스크에서 읽어들일 사진의 상한. 이보다 크면 애초에 우리 스냅샷이 아니다.
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024

# 축소를 시도할 순서. 균열 판독은 512px에서도 쓸 만하다.
_SHRINK_EDGES = (1024, 768, 640, 512, 384)
_SHRINK_QUALITIES = (80, 70, 60, 50)


def shrink_to_budget(raw: bytes, media_type: str, budget: int) -> tuple[str, bytes]:
    """사진을 `budget` 바이트 이하로 줄인다. 이미 작으면 그대로 돌려준다.

    SSAFY GMS 게이트웨이는 요청 본문이 약 100KB를 넘으면 400을 낸다(에러 메시지는
    `Model not found in request`라 오해하기 쉽다). base64가 약 1.37배로 부풀리므로
    원본을 그보다 훨씬 작게 만들어야 한다.
    """
    if len(raw) <= budget:
        return media_type, raw

    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - 배포 이미지에는 항상 있다
        raise RuntimeError(
            f"사진이 {len(raw) // 1024}KB로 전송 한계({budget // 1024}KB)를 넘는데 "
            "Pillow가 없어 축소할 수 없음"
        ) from error

    with Image.open(io.BytesIO(raw)) as opened:
        # JPEG로 다시 인코딩하므로 알파 채널은 버린다.
        source = opened.convert("RGB")

        for max_edge in _SHRINK_EDGES:
            resized = source.copy()
            resized.thumbnail((max_edge, max_edge))
            for quality in _SHRINK_QUALITIES:
                buffer = io.BytesIO()
                resized.save(buffer, format="JPEG", quality=quality, optimize=True)
                data = buffer.getvalue()
                if len(data) <= budget:
                    logger.info(
                        "사진 축소: %dKB -> %dKB (%dpx, q%d)",
                        len(raw) // 1024,
                        len(data) // 1024,
                        max_edge,
                        quality,
                    )
                    return "image/jpeg", data

    raise RuntimeError(
        f"사진을 전송 한계({budget // 1024}KB) 이하로 줄이지 못함 (원본 {len(raw) // 1024}KB)"
    )


def to_update_payload(summary: ReportSummary) -> dict[str, Any]:
    """`reports` 문서에 `$set`할 형태로 만든다.

    저장하는 필드만 명시해서 보낸다. 모델 전체를 덤프하지 않는다.
    """
    return {
        "assessment": summary.assessment,
        "recommendations": list(summary.recommendations),
        "observations": [
            {
                "defect_id": observation.defect_id,
                "position_m": observation.position_m,
                "defect_type": observation.defect_type.value,
                "severity": observation.severity.value,
                "description": observation.description,
                "photo_description": observation.photo_description,
                "source": observation.source,
                "failure_code": (
                    observation.failure_code.value if observation.failure_code else None
                ),
                # 양식에는 싣지 않는다 — 발주처가 보는 문서에 예외 문구를 넣을 이유가
                # 없다. API 로만 나가 원인을 되짚는 데 쓴다.
                "failure_detail": observation.failure_detail,
            }
            for observation in summary.observations
        ],
        # 환경부 지침 제6장: 조사구간 값은 소수 상태점수다. 정수 등급은 함께 저장해
        # 양식과 집계표가 둘 다 쓸 수 있게 한다.
        "structural_grade": summary.grades.structural,
        "operational_grade": summary.grades.operational,
        "structural_grade_class": summary.grades.structural_grade,
        "operational_grade_class": summary.grades.operational_grade,
        "segment_count": summary.grades.segment_count,
        "segment_length_m": summary.grades.segment_length_m,
        "grade_score_basis": summary.grades.score_basis,
        "grade_scores_estimated": summary.grades.uses_estimated_scores,
        "summary_model": summary.source_model,
    }


def read_snapshot(snapshot_path: str | None, *, budget: int) -> tuple[str, bytes]:
    """결함 사진을 디스크에서 읽어 (media type, bytes)로 돌려준다.

    `snapshot_path`는 이미 `facts.sanitize_snapshot_path`를 통과한 값이어야 하고,
    `resolve_media_file`이 media_root 밖으로 나가지 않는지 한 번 더 검증한다.
    게이트웨이 본문 한계에 맞춰 필요하면 축소한다.
    """
    resolved: Path | None = resolve_media_file(snapshot_path)
    if resolved is None:
        raise RuntimeError(f"미디어 경로를 찾을 수 없음: {snapshot_path}")

    media_type = _IMAGE_MEDIA_TYPES.get(resolved.suffix.lower())
    if media_type is None:
        raise RuntimeError(f"지원하지 않는 사진 형식: {resolved.suffix}")

    size = resolved.stat().st_size
    if size > MAX_SOURCE_IMAGE_BYTES:
        raise RuntimeError(f"사진이 {MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)}MB 상한을 넘음")
    if size == 0:
        raise RuntimeError("사진 파일이 비어 있음")

    return shrink_to_budget(resolved.read_bytes(), media_type, budget)


class ReportSummaryService:
    """보고서 하나의 서술형 요약을 만든다."""

    def __init__(
        self,
        *,
        provider: ChatProvider | None = None,
        describe_images: bool = True,
        max_vision_defects: int = DEFAULT_MAX_VISION_DEFECTS,
        image_budget_bytes: int = DEFAULT_IMAGE_BUDGET_BYTES,
    ) -> None:
        self.provider = provider
        self.describe_images = describe_images
        self.max_vision_defects = max_vision_defects
        self.image_budget_bytes = image_budget_bytes

    def build(self, report: dict[str, Any], defects: list[dict[str, Any]]) -> ReportSummary:
        facts = build_session_facts(report, defects)
        # 단위구간 길이는 관종에서, 구간 개수는 관로 연장에서 나온다(환경부 지침 1.1.1).
        grades = compute_grades(
            facts,
            pipe_type=report.get("pipe_type"),
            pipe_length_m=report.get("pipe_length_m") or report.get("total_distance_m"),
        )

        if facts.dropped_defect_count:
            logger.warning(
                "report %s: 결함 %d건이 요약 프롬프트에서 제외됨",
                facts.report_id,
                facts.dropped_defect_count,
            )

        narrative, narrative_latency, status, source_model = self._narrate(facts, grades)
        observations, vision_latency, dropped_defect_ids = self._observe(facts)
        vision_count = sum(1 for item in observations if item.source == "vision")

        return ReportSummary(
            assessment=narrative.assessment,
            recommendations=narrative.recommendations,
            observations=observations,
            grades=grades,
            vision_described_count=vision_count,
            dropped_defect_ids=dropped_defect_ids,
            latency_ms=narrative_latency + vision_latency,
            source_model=source_model,
            status=status,
        )

    def _narrate(
        self, facts: SessionFacts, grades: StateGrades
    ) -> tuple[ReportNarrative, float, str, str]:
        if self.provider is None:
            return render_fallback_narrative(facts, grades), 0.0, "fallback", "rule-based"

        started = time.perf_counter()
        # 검증기(위치 환각·문체)에 한 번 걸렸다고 문단 전체를 규칙 기반으로 떨구면
        # 손해가 크다 — 한 번 더 걸면 대개 통과한다. 실패할 때만 드는 비용이다.
        for attempt in range(1, NARRATIVE_ATTEMPTS + 1):
            try:
                response = self.provider.complete(
                    system_prompt=NARRATIVE_SYSTEM_PROMPT,
                    user_payload=build_narrative_payload(facts, grades),
                    # 사진 판독과 같은 이유로 넉넉히 준다. 추론 모델은 이 한도를
                    # 추론 토큰과 나눠 쓰기 때문에 빠듯하면 본문이 잘려 온다.
                    max_tokens=2000,
                )
                narrative = parse_narrative_text(response, facts=facts, grades=grades)
            except Exception as error:
                logger.warning(
                    "report %s: 서술형 요약 %d회차 실패 (%s)",
                    facts.report_id,
                    attempt,
                    error,
                )
                continue

            latency_ms = (time.perf_counter() - started) * 1000.0
            return narrative, latency_ms, "ready", self.provider.name

        logger.warning("report %s: 규칙 기반 요약으로 강등", facts.report_id)
        return render_fallback_narrative(facts, grades), 0.0, "fallback", "rule-based"

    def _observe(
        self, facts: SessionFacts
    ) -> tuple[list[DefectObservation], float, list[int]]:
        """결함 사진을 한 장씩 읽되, 세션 단위가 아니라 결함 단위로 강등한다."""
        observations: list[DefectObservation] = []
        dropped_defect_ids: list[int] = []
        total_latency = 0.0
        vision_budget = self.max_vision_defects if self.describe_images else 0
        skipped_for_budget = 0

        for defect in facts.defects:

            def degrade(
                code: ObservationFailure,
                detail: str | None = None,
                _defect: DefectFact = defect,
            ) -> None:
                if detail:
                    logger.warning(
                        "report %s: %sm 지점 사진 미판독 [%s] %s",
                        facts.report_id,
                        _defect.position_m,
                        code.value,
                        detail,
                    )
                # 빼도 되는 실패인지 볼 때 **사전 판독과 같은 예외**를 건다. 여기만
                # 빠져 있어서, 사전 판독이 지켜 준 막힘이 보고서 생성 때 조용히
                # 사라졌다(세션 222 — 판독문도 없이 REJECTED 가 됐다).
                if (
                    code in PER_PHOTO_FAILURES
                    and _defect.defect_type.value not in NEVER_REJECTED_TYPES
                    and visible_fraction(_defect.snapshot_path) >= MIN_VISIBLE_FRACTION
                ):
                    dropped_defect_ids.append(_defect.defect_id)
                    return
                observations.append(
                    render_fallback_observation(
                        _defect, failure_code=code, failure_detail=detail
                    )
                )

            # 점검 중에 미리 읽어 둔 판독이 있으면 그대로 쓴다. 이게 보고서를
            # 「끝나자마자」 낼 수 있게 하는 지점이다 — 판독 한 건이 11.7초라
            # 여기서 다시 부르면 결함 수만큼 그대로 기다려야 한다.
            if defect.photo_description:
                observations.append(
                    DefectObservation(
                        defect_id=defect.defect_id,
                        position_m=defect.position_m,
                        defect_type=defect.defect_type,
                        severity=defect.severity,
                        description=render_detection_description(defect),
                        photo_description=defect.photo_description,
                        source="vision",
                    )
                )
                continue

            if defect.snapshot_path is None:
                degrade(ObservationFailure.NO_SNAPSHOT)
                continue
            if self.provider is None or not self.provider.supports_vision:
                degrade(ObservationFailure.MODEL_UNAVAILABLE)
                continue
            # 막힘은 예산에서 빼 준다. 결함 순서가 시간순이라 막힘은 대개 **맨 끝**에
            # 오고, 앞쪽이 예산을 다 쓰면 정작 점검을 멈춰 세운 결함이 판독 없이
            # 실린다(실측에서 실제로 BUDGET_EXCEEDED 로 잘렸다). 많아야 한 건이라
            # 보고서 시간에 주는 부담도 11.7초뿐이다.
            if vision_budget <= 0 and defect.defect_type.value not in NEVER_REJECTED_TYPES:
                skipped_for_budget += 1
                degrade(ObservationFailure.BUDGET_EXCEEDED)
                continue

            # 실패 경로를 나눠서 무엇이 깨졌는지 보고서에 이름으로 남긴다.
            try:
                image = read_snapshot(defect.snapshot_path, budget=self.image_budget_bytes)
            except Exception as error:
                degrade(ObservationFailure.IMAGE_FETCH_FAILED, str(error))
                continue

            # 사진 한 장을 최대 PHOTO_READ_ATTEMPTS번까지 읽어 본다.
            #
            # 게이트웨이가 비전 응답을 매번 잘라 보내기 때문에(실측 10/10) 복구가
            # 빗나가는 날이 있다. 그러면 호출이 예외로 끝나거나(INFERENCE_FAILED),
            # 살아남더라도 문장이 중간에 끊겨 검증기가 되돌린다(VALIDATION_REJECTED).
            # 둘 다 사진이나 프롬프트 탓이 아니라 그 한 번의 응답 탓이므로 다시
            # 걸면 대개 통과한다. 보고서 전체를 다시 만들 필요 없이 실패한 사진만
            # 다시 읽는다 — 결과는 같고 비용은 결함 1건치다.
            started = time.perf_counter()
            photo_description = None
            failure: tuple[ObservationFailure, str] | None = None

            for read_attempt in range(1, PHOTO_READ_ATTEMPTS + 1):
                try:
                    response = self.provider.complete(
                        system_prompt=PHOTO_SYSTEM_PROMPT,
                        user_payload=build_photo_payload(defect),
                        image=image,
                        # 추론 모델(gpt-5.x)은 이 한도에 추론 토큰까지 포함시킨다.
                        # 512로 두면 추론에 다 쓰고 본문이 비어 오는 경우가 생긴다.
                        max_tokens=1500,
                    )
                except Exception as error:
                    failure = (
                        ObservationFailure.INFERENCE_FAILED,
                        f"{type(error).__name__}: {error}",
                    )
                else:
                    try:
                        photo_description = parse_photo_description_text(
                            response, defect=defect
                        )
                    except ValueError as error:
                        failure = (ObservationFailure.VALIDATION_REJECTED, str(error))
                    else:
                        failure = None
                        break

                if read_attempt < PHOTO_READ_ATTEMPTS:
                    logger.warning(
                        "report %s: %sm 지점 사진 판독 %d회차 실패 [%s] — 다시 읽음",
                        facts.report_id,
                        defect.position_m,
                        read_attempt,
                        failure[0].value,
                    )

            if failure is not None:
                degrade(*failure)
                continue
            assert photo_description is not None

            vision_budget -= 1
            total_latency += (time.perf_counter() - started) * 1000.0
            observations.append(
                DefectObservation(
                    defect_id=defect.defect_id,
                    position_m=defect.position_m,
                    defect_type=defect.defect_type,
                    severity=defect.severity,
                    # 탐지 기반 서술과 사진 묘사를 나란히 담는다. 합치지 않는다.
                    description=render_detection_description(defect),
                    photo_description=photo_description,
                    source="vision",
                )
            )

        if skipped_for_budget:
            reason = (
                f"판독 상한 {self.max_vision_defects}건 도달"
                if self.describe_images
                else "사진 판독 비활성(LLM_DESCRIBE_IMAGES=false)"
            )
            logger.warning(
                "report %s: 사진 %d건 미판독 — %s",
                facts.report_id,
                skipped_for_budget,
                reason,
            )

        if dropped_defect_ids:
            logger.warning(
                "report %s: 사진 판독에 실패한 결함 %d건을 보고서에서 제외",
                facts.report_id,
                len(dropped_defect_ids),
            )
        return observations, total_latency, dropped_defect_ids


def generate_report_summary(
    report: dict[str, Any], defects: list[dict[str, Any]]
) -> ReportSummary:
    """설정에 맞춰 서비스를 조립하고 요약 하나를 만든다(블로킹).

    라우터에서 부를 때는 `run_in_threadpool`로 감싼다 — 이벤트 루프를 막으면
    요약이 도는 동안 API 전체가 멈춘다.
    """
    settings = get_settings()
    service = ReportSummaryService(
        provider=build_provider(),
        describe_images=settings.llm_describe_images,
        max_vision_defects=settings.llm_max_vision_defects,
        image_budget_bytes=settings.llm_image_budget_bytes,
    )
    return service.build(report, defects)
