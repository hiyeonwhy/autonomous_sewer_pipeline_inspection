from __future__ import annotations

import logging
from typing import Protocol

from pipecheckkk_ai.models import utc_now

from .cloud_client import HttpCloudClient
from .facts import build_session_facts, compute_grades
from .fallback import render_fallback_narrative, render_fallback_observation
from .models import (
    DefectFact,
    DefectObservation,
    ObservationFailure,
    ReportNarrative,
    ReportSummary,
    SessionFacts,
    Severity,
    StateGrades,
)

logger = logging.getLogger(__name__)

# Each snapshot reading is a separate vision pass, so it costs seconds rather
# than milliseconds. Bound how many run and report the remainder.
MAX_VISION_DEFECTS = 8


def to_cloud_payload(summary: ReportSummary) -> dict[str, object]:
    """Shape the summary for `PATCH /api/reports/{id}`.

    The Cloud API takes snake_case and only the fields it stores, so send those
    explicitly rather than dumping the whole model.
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
                "source": observation.source,
                "failure_code": (
                    observation.failure_code.value if observation.failure_code else None
                ),
            }
            for observation in summary.observations
        ],
        "structural_grade": summary.grades.structural,
        "operational_grade": summary.grades.operational,
        "summary_model": summary.source_model,
    }


class NarrativeProvider(Protocol):
    name: str

    def generate_narrative(
        self, *, facts: SessionFacts, grades: StateGrades, max_new_tokens: int = ...
    ) -> tuple[ReportNarrative, float]: ...

    def describe_defect(
        self,
        *,
        defect: DefectFact,
        image_bytes: bytes,
        image_url: str | None = ...,
        max_new_tokens: int = ...,
    ) -> tuple[DefectObservation, float]: ...


class ReportSummaryService:
    """Builds a report summary for one session.

    The split is deliberate: counts and 상태등급 are computed in code, and only
    the prose comes from the model. Any model or image failure downgrades that
    piece to the deterministic fallback — it never blocks the summary.
    """

    def __init__(
        self,
        *,
        provider: NarrativeProvider | None = None,
        client: HttpCloudClient | None = None,
        describe_images: bool = True,
        max_vision_defects: int = MAX_VISION_DEFECTS,
    ) -> None:
        self.provider = provider
        self.client = client or HttpCloudClient()
        self.describe_images = describe_images
        self.max_vision_defects = max_vision_defects

    def build_from_cloud(self, session_id: int) -> ReportSummary:
        session = self.client.read_session(session_id)
        defects = self.client.read_defects(session_id)
        return self.build(session, defects)

    def build(self, session: dict[str, object], defects: list[dict[str, object]]) -> ReportSummary:
        facts = build_session_facts(session, defects)
        grades = compute_grades(facts)

        if facts.dropped_defect_count:
            logger.warning(
                "session %s: %d defect(s) omitted from the summary prompt",
                facts.session_id,
                facts.dropped_defect_count,
            )

        narrative, narrative_latency, status, source_model = self._narrate(facts, grades)
        observations, vision_latency = self._observe(facts)
        vision_count = sum(1 for item in observations if item.source == "vision")

        return ReportSummary(
            session_id=facts.session_id,
            assessment=narrative.assessment,
            recommendations=narrative.recommendations,
            observations=observations,
            grades=grades,
            defect_total=facts.defect_total,
            critical_count=facts.count_by_severity(Severity.CRITICAL),
            warning_count=facts.count_by_severity(Severity.WARNING),
            dropped_defect_count=facts.dropped_defect_count,
            vision_described_count=vision_count,
            generated_at=utc_now(),
            latency_ms=narrative_latency + vision_latency,
            source_model=source_model,
            status=status,
        )

    def _narrate(
        self, facts: SessionFacts, grades: StateGrades
    ) -> tuple[ReportNarrative, float, str, str]:
        if self.provider is None:
            return render_fallback_narrative(facts, grades), 0.0, "fallback", "rule-based"
        try:
            narrative, latency_ms = self.provider.generate_narrative(facts=facts, grades=grades)
        except Exception as error:
            logger.warning(
                "session %s: falling back to the rule-based narrative (%s)",
                facts.session_id,
                error,
            )
            return render_fallback_narrative(facts, grades), 0.0, "fallback", "rule-based"
        return narrative, latency_ms, "ready", self.provider.name

    def _observe(self, facts: SessionFacts) -> tuple[list[DefectObservation], float]:
        """Read each defect snapshot, degrading per defect rather than per session."""
        observations: list[DefectObservation] = []
        total_latency = 0.0
        vision_budget = self.max_vision_defects if self.describe_images else 0
        skipped_for_budget = 0

        for defect in facts.defects:
            image_url = (
                self.client.media_url(defect.snapshot_path) if defect.snapshot_path else None
            )

            def degrade(
                code: ObservationFailure, detail: str | None = None, _defect=defect, _url=image_url
            ) -> None:
                if detail:
                    logger.warning(
                        "session %s: snapshot at %sm not read [%s] %s",
                        facts.session_id,
                        _defect.position_m,
                        code.value,
                        detail,
                    )
                observations.append(
                    render_fallback_observation(
                        _defect, image_url=_url, failure_code=code, failure_detail=detail
                    )
                )

            if defect.snapshot_path is None:
                degrade(ObservationFailure.NO_SNAPSHOT)
                continue
            if self.provider is None:
                degrade(ObservationFailure.MODEL_UNAVAILABLE)
                continue
            if vision_budget <= 0:
                skipped_for_budget += 1
                degrade(ObservationFailure.BUDGET_EXCEEDED)
                continue

            # Split the failure paths so the report can name what actually broke.
            try:
                image_bytes = self.client.read_media(defect.snapshot_path)
            except Exception as error:
                degrade(ObservationFailure.IMAGE_FETCH_FAILED, str(error))
                continue

            try:
                observation, latency_ms = self.provider.describe_defect(
                    defect=defect, image_bytes=image_bytes, image_url=image_url
                )
            except ValueError as error:
                degrade(ObservationFailure.VALIDATION_REJECTED, str(error))
                continue
            except Exception as error:
                degrade(ObservationFailure.INFERENCE_FAILED, f"{type(error).__name__}: {error}")
                continue

            vision_budget -= 1
            total_latency += latency_ms
            observations.append(observation)

        if skipped_for_budget:
            logger.warning(
                "session %s: %d snapshot(s) not read — vision budget of %d reached",
                facts.session_id,
                skipped_for_budget,
                self.max_vision_defects,
            )

        return observations, total_latency
