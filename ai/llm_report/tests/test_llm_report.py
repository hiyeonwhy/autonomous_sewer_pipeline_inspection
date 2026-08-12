from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_report.facts import build_session_facts, compute_grades, sanitize_snapshot_path
from llm_report.fallback import render_fallback_narrative, render_fallback_observation
from llm_report.models import DefectType, ObservationFailure, Severity
from llm_report.qwen_provider import (
    MODEL_PREFERENCE,
    QwenReportProvider,
    build_defect_payload,
    build_prompt_payload,
    has_weights,
    parse_defect_description_text,
    parse_narrative_text,
    resolve_model_dir,
)
from llm_report.service import ReportSummaryService

SESSION = {
    "id": 4,
    "district_id": 7,
    "mode": "AUTO",
    "status": "COMPLETED",
    "started_at": "2026-07-31T01:00:00+00:00",
    "ended_at": "2026-07-31T01:45:30+00:00",
}

DEFECTS = [
    {
        "id": 11,
        "defect_type": "CRACK",
        "severity": "CRITICAL",
        "confidence": 0.91,
        "approx_position_m": 25.8,
        "blockage_ratio": None,
        "snapshot_path": "/media/defects/crack-11.jpg",
    },
    {
        "id": 12,
        "defect_type": "DEBRIS",
        "severity": "WARNING",
        "confidence": 0.64,
        "approx_position_m": 12.4,
        "blockage_ratio": 0.35,
        "snapshot_path": "/media/defects/debris-12.jpg",
    },
]


class StubClient:
    """Stands in for HttpCloudClient without touching the network."""

    base_url = "https://example.test"

    def __init__(self, *, fail_paths: set[str] | None = None) -> None:
        self.fail_paths = fail_paths or set()
        self.fetched: list[str] = []

    def media_url(self, snapshot_path: str) -> str:
        return f"{self.base_url}{snapshot_path}"

    def read_media(self, snapshot_path: str) -> bytes:
        if snapshot_path in self.fail_paths:
            raise RuntimeError("snapshot connection failed")
        self.fetched.append(snapshot_path)
        return b"\xff\xd8\xff\xd9"


class StubVisionProvider:
    name = "stub-model"

    def __init__(self) -> None:
        self.described: list[float] = []

    def generate_narrative(self, *, facts, grades, max_new_tokens=512):
        from llm_report.models import ReportNarrative

        return (
            ReportNarrative(
                assessment="관로 전반에 부분적인 보수가 필요한 상태로 확인됨.",
                recommendations=["정밀 조사 검토 필요."],
            ),
            120.0,
        )

    def describe_defect(self, *, defect, image_bytes, image_url=None, max_new_tokens=192):
        from llm_report.models import DefectObservation

        self.described.append(defect.position_m)
        label = {DefectType.CRACK: "균열", DefectType.DEBRIS: "퇴적물"}[defect.defect_type]
        return (
            DefectObservation(
                defect_id=defect.defect_id,
                position_m=defect.position_m,
                defect_type=defect.defect_type,
                severity=defect.severity,
                image_url=image_url,
                description=f"관 하부에 {label}이 폭 방향으로 관찰됨.",
                source="vision",
            ),
            30.0,
        )


# ---------------------------------------------------------------- facts


def test_facts_are_sorted_by_position_and_duration_is_derived() -> None:
    facts = build_session_facts(SESSION, DEFECTS)

    assert [defect.position_m for defect in facts.defects] == [12.4, 25.8]
    assert facts.duration_seconds == 2730
    assert facts.defect_total == 2
    assert facts.dropped_defect_count == 0


def test_unknown_enum_and_out_of_range_values_are_clamped() -> None:
    facts = build_session_facts(
        SESSION,
        [
            {
                "defect_type": "GARBAGE",
                "severity": "EXTREME",
                "confidence": 4.2,
                "approx_position_m": -18.0,
                "blockage_ratio": 9.9,
            }
        ],
    )

    defect = facts.defects[0]
    assert defect.defect_type is DefectType.OTHER
    assert defect.severity is Severity.NORMAL
    assert defect.confidence == 1.0
    assert defect.position_m == 0.0
    assert defect.blockage_ratio == 1.0


def test_defects_beyond_the_cap_are_counted_not_dropped_silently() -> None:
    many = [dict(DEFECTS[0], approx_position_m=float(index)) for index in range(40)]
    facts = build_session_facts(SESSION, many, max_defects=30)

    assert len(facts.defects) == 30
    assert facts.dropped_defect_count == 10
    assert facts.defect_total == 40


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/steal.jpg",
        "/media/../../etc/passwd",
        "/etc/passwd",
        "media/defects/a.jpg",
        None,
        42,
    ],
)
def test_snapshot_paths_outside_the_media_root_are_rejected(value: object) -> None:
    assert sanitize_snapshot_path(value) is None


def test_snapshot_path_inside_the_media_root_is_kept() -> None:
    assert sanitize_snapshot_path("/media/defects/crack-11.jpg") == "/media/defects/crack-11.jpg"


def test_grades_split_structural_from_operational() -> None:
    grades = compute_grades(build_session_facts(SESSION, DEFECTS))

    # One CRITICAL crack → structural 4; one WARNING debris → operational 2.
    assert grades.structural == 4
    assert grades.operational == 2


def test_grades_are_zero_without_defects() -> None:
    grades = compute_grades(build_session_facts(SESSION, []))

    assert grades.structural == 0
    assert grades.operational == 0


# ---------------------------------------------------------------- model selection


def _make_snapshot(root, name: str, *, sharded: bool = False) -> None:
    weights = root / name / "weights"
    weights.mkdir(parents=True)
    (weights / "config.json").write_text("{}")
    (weights / "preprocessor_config.json").write_text("{}")
    if sharded:
        (weights / "model-00001-of-00002.safetensors").write_bytes(b"")
        (weights / "model-00002-of-00002.safetensors").write_bytes(b"")
    else:
        (weights / "model.safetensors").write_bytes(b"")


def test_sharded_weights_count_as_downloaded(tmp_path) -> None:
    """7B는 weight가 여러 파일로 쪼개져 오므로 단일 파일을 요구하면 안 된다."""
    _make_snapshot(tmp_path, "qwen2.5-vl-7b-instruct-awq", sharded=True)

    assert has_weights(tmp_path / "qwen2.5-vl-7b-instruct-awq" / "weights")


def test_incomplete_snapshot_is_not_downloaded(tmp_path) -> None:
    weights = tmp_path / "qwen2.5-vl-7b-instruct-awq" / "weights"
    weights.mkdir(parents=True)
    (weights / "config.json").write_text("{}")

    assert has_weights(weights) is False


def test_seven_b_is_preferred_over_three_b(tmp_path) -> None:
    _make_snapshot(tmp_path, "qwen2.5-vl-3b-instruct-awq")
    _make_snapshot(tmp_path, "qwen2.5-vl-7b-instruct-awq", sharded=True)

    resolved = resolve_model_dir(tmp_path)

    assert resolved.parent.name == "qwen2.5-vl-7b-instruct-awq"


def test_three_b_is_used_when_seven_b_is_absent(tmp_path) -> None:
    _make_snapshot(tmp_path, "qwen2.5-vl-3b-instruct-awq")

    resolved = resolve_model_dir(tmp_path)

    assert resolved.parent.name == "qwen2.5-vl-3b-instruct-awq"


def test_no_snapshot_resolves_to_none(tmp_path) -> None:
    assert resolve_model_dir(tmp_path) is None


def test_provider_reports_the_model_that_will_load(tmp_path) -> None:
    _make_snapshot(tmp_path, "qwen2.5-vl-3b-instruct-awq")
    provider = QwenReportProvider(tmp_path / "qwen2.5-vl-3b-instruct-awq" / "weights")

    assert provider.name == "qwen2.5-vl-3b-instruct-awq"
    assert provider.is_downloaded()


def test_provider_without_weights_points_at_the_preferred_model() -> None:
    """받은 게 없으면 오류 메시지가 무엇을 받아야 하는지 가리켜야 한다."""
    provider = QwenReportProvider(Path("/nonexistent/weights"))

    assert provider.is_downloaded() is False
    assert MODEL_PREFERENCE[0] == "qwen2.5-vl-7b-instruct-awq"


# ---------------------------------------------------------------- prompts


def test_prompt_payload_excludes_identifiers_and_timestamps() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    payload = build_prompt_payload(facts, compute_grades(facts))

    assert "sessionId" not in payload
    assert "startedAt" not in payload
    assert payload["structuralGrade"] == 4
    assert payload["defects"][0]["type"] == "퇴적물"


def test_defect_payload_carries_only_confirmed_facts() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    payload = build_defect_payload(facts.defects[0])

    assert payload == {
        "positionM": 12.4,
        "type": "퇴적물",
        "severity": "주의",
        "confidence": 0.64,
        "blockageRatio": 0.35,
    }
    assert "snapshotPath" not in payload


# ---------------------------------------------------------------- narrative parser


def test_parser_accepts_a_well_formed_gaejosik_narrative() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    narrative = parse_narrative_text(
        json.dumps(
            {
                "assessment": (
                    "7번 점검 구역에서 결함 2건 확인됨. "
                    "25.8m 지점 균열은 심각 등급으로 판정됨. "
                    "구조적 상태등급 4등급으로 산정됨."
                ),
                "recommendations": ["25.8m 지점 균열에 대한 정밀 조사 우선 검토 필요."],
            },
            ensure_ascii=False,
        ),
        facts=facts,
    )

    assert narrative.recommendations == ["25.8m 지점 균열에 대한 정밀 조사 우선 검토 필요."]


def test_parser_rejects_declarative_endings() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    with pytest.raises(ValueError, match="개조식"):
        parse_narrative_text(
            json.dumps(
                {
                    "assessment": (
                        "7번 점검 구역에서 결함 2건이 확인되었다. "
                        "25.8m 지점 균열은 심각 등급이다. "
                        "구조적 상태등급은 4등급으로 산정되었다."
                    ),
                    "recommendations": [],
                },
                ensure_ascii=False,
            ),
            facts=facts,
        )


def test_parser_rejects_a_hallucinated_position() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    with pytest.raises(ValueError, match="unknown positions"):
        parse_narrative_text(
            json.dumps(
                {
                    "assessment": (
                        "7번 점검 구역에서 결함 확인됨. "
                        "88.0m 지점에서 추가 균열 관찰됨. "
                        "구조적 상태등급 4등급으로 산정됨."
                    ),
                    "recommendations": [],
                },
                ensure_ascii=False,
            ),
            facts=facts,
        )


def test_parser_rejects_imperative_phrasing() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    with pytest.raises(ValueError, match="imperative"):
        parse_narrative_text(
            json.dumps(
                {
                    "assessment": (
                        "7번 점검 구역에서 결함 2건 확인됨. "
                        "지금 즉시 보수를 진행하세요. "
                        "구조적 상태등급 4등급으로 산정됨."
                    ),
                    "recommendations": [],
                },
                ensure_ascii=False,
            ),
            facts=facts,
        )


def test_parser_rejects_non_json_output() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    with pytest.raises(ValueError, match="valid JSON narrative"):
        parse_narrative_text("보고서를 작성했습니다.", facts=facts)


# ---------------------------------------------------------------- description parser


def test_description_parser_accepts_a_grounded_reading() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    observation = parse_defect_description_text(
        json.dumps(
            {
                "description": (
                    "관 상부 12시 방향에 종방향 균열이 연속적으로 관찰됨. "
                    "균열이 화면 면적의 15%(육안 추정)를 점유함. 이물질 부착은 미확인."
                )
            },
            ensure_ascii=False,
        ),
        defect=crack,
        image_url="https://example.test/media/defects/crack-11.jpg",
    )

    assert observation.source == "vision"
    assert observation.position_m == 25.8
    assert observation.defect_id == 11
    assert observation.failure_code is None
    assert observation.image_url.endswith("crack-11.jpg")


def test_description_parser_requires_a_percentage() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    with pytest.raises(ValueError, match="percentage"):
        parse_defect_description_text(
            json.dumps(
                {"description": "관 상부 12시 방향에 종방향 균열이 연속적으로 관찰됨."},
                ensure_ascii=False,
            ),
            defect=crack,
            image_url=None,
        )


@pytest.mark.parametrize("word", ["상당", "대부분", "다수", "약간", "조금"])
def test_description_parser_rejects_vague_quantifiers(word: str) -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    with pytest.raises(ValueError, match="vague quantifiers"):
        parse_defect_description_text(
            json.dumps(
                {"description": f"관 상부에 균열이 {word} 관찰되며 화면 면적의 15%를 점유함."},
                ensure_ascii=False,
            ),
            defect=crack,
            image_url=None,
        )


def test_percent_figures_do_not_trip_the_distance_guard() -> None:
    """A bare '40%' must not be read as a distance the model invented."""
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    observation = parse_defect_description_text(
        json.dumps(
            {"description": "관 상부에 균열이 관찰되며 화면 면적의 40%(육안 추정)를 점유함."},
            ensure_ascii=False,
        ),
        defect=crack,
        image_url=None,
    )

    assert "40%" in observation.description


def test_description_parser_rejects_a_different_defect_type() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    with pytest.raises(ValueError, match="confirmed type"):
        parse_defect_description_text(
            json.dumps({"description": "관 하부에 퇴적물이 다량 관찰됨."}, ensure_ascii=False),
            defect=crack,
            image_url=None,
        )


def test_description_parser_rejects_a_position_from_another_defect() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    with pytest.raises(ValueError, match="unknown positions"):
        parse_defect_description_text(
            json.dumps(
                {"description": "12.4m 지점의 균열이 화면 면적의 15%를 점유함."},
                ensure_ascii=False,
            ),
            defect=crack,
            image_url=None,
        )


def test_description_parser_rejects_declarative_endings() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    crack = facts.defects[1]

    with pytest.raises(ValueError, match="개조식"):
        parse_defect_description_text(
            json.dumps(
                {"description": "관 상부에 균열이 화면 면적의 15%를 점유하며 관찰되었다."},
                ensure_ascii=False,
            ),
            defect=crack,
            image_url=None,
        )


# ---------------------------------------------------------------- fallback


def test_fallback_narrative_stays_factual_and_gaejosik() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    narrative = render_fallback_narrative(facts, compute_grades(facts))

    assert "결함 2건 확인됨" in narrative.assessment
    assert "25.8m" in narrative.assessment
    assert "되었다" not in narrative.assessment
    assert narrative.recommendations


def test_fallback_observation_reports_metadata_only() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    observation = render_fallback_observation(facts.defects[0], image_url=None)

    assert observation.source == "metadata"
    assert "퇴적물 확인됨" in observation.description
    assert "35%" in observation.description
    assert observation.failure_code is ObservationFailure.MODEL_UNAVAILABLE


def test_fallback_observation_writes_the_failure_code_into_the_text() -> None:
    """A reviewer reading the rendered report, not the JSON, still sees why."""
    facts = build_session_facts(SESSION, DEFECTS)
    observation = render_fallback_observation(
        facts.defects[0],
        image_url="https://example.test/media/defects/debris-12.jpg",
        failure_code=ObservationFailure.IMAGE_FETCH_FAILED,
        failure_detail="snapshot request rejected: HTTP 404",
    )

    assert "IMAGE_FETCH_FAILED" in observation.description
    assert "결함 사진 내려받기 실패" in observation.description
    assert "HTTP 404" in observation.description
    assert observation.failure_detail == "snapshot request rejected: HTTP 404"


def test_fallback_observation_truncates_a_long_failure_detail() -> None:
    facts = build_session_facts(SESSION, DEFECTS)
    observation = render_fallback_observation(
        facts.defects[0],
        failure_code=ObservationFailure.INFERENCE_FAILED,
        failure_detail="x" * 400,
    )

    assert len(observation.description) < 400


# ---------------------------------------------------------------- service


def test_service_without_a_provider_uses_the_rule_based_narrative() -> None:
    summary = ReportSummaryService(provider=None, client=StubClient()).build(SESSION, DEFECTS)

    assert summary.status == "fallback"
    assert summary.source_model == "rule-based"
    assert summary.critical_count == 1
    assert summary.warning_count == 1
    assert summary.grades.structural == 4
    assert summary.vision_described_count == 0
    assert [item.source for item in summary.observations] == ["metadata", "metadata"]


def test_service_reads_every_snapshot_when_vision_is_available() -> None:
    provider = StubVisionProvider()
    client = StubClient()
    summary = ReportSummaryService(provider=provider, client=client).build(SESSION, DEFECTS)

    assert summary.status == "ready"
    assert summary.vision_described_count == 2
    assert provider.described == [12.4, 25.8]
    assert client.fetched == [
        "/media/defects/debris-12.jpg",
        "/media/defects/crack-11.jpg",
    ]
    assert summary.latency_ms == 180.0  # 120 narrative + 30 + 30 vision


def test_service_degrades_only_the_defect_whose_image_failed() -> None:
    client = StubClient(fail_paths={"/media/defects/crack-11.jpg"})
    summary = ReportSummaryService(provider=StubVisionProvider(), client=client).build(
        SESSION, DEFECTS
    )

    by_position = {item.position_m: item for item in summary.observations}
    assert by_position[12.4].source == "vision"
    assert by_position[25.8].source == "metadata"
    assert by_position[25.8].failure_code is ObservationFailure.IMAGE_FETCH_FAILED
    assert "snapshot connection failed" in by_position[25.8].failure_detail
    assert summary.vision_described_count == 1
    assert summary.status == "ready"


def test_validation_and_inference_failures_get_distinct_codes() -> None:
    """A rejected reading and a crashed model must not look the same in the report."""

    class RejectingProvider(StubVisionProvider):
        def describe_defect(self, *, defect, image_bytes, image_url=None, max_new_tokens=192):
            raise ValueError("Qwen description did not quantify the affected area")

    class CrashingProvider(StubVisionProvider):
        def describe_defect(self, *, defect, image_bytes, image_url=None, max_new_tokens=192):
            raise RuntimeError("CUDA out of memory")

    rejected = ReportSummaryService(provider=RejectingProvider(), client=StubClient()).build(
        SESSION, DEFECTS
    )
    crashed = ReportSummaryService(provider=CrashingProvider(), client=StubClient()).build(
        SESSION, DEFECTS
    )

    assert rejected.observations[0].failure_code is ObservationFailure.VALIDATION_REJECTED
    assert crashed.observations[0].failure_code is ObservationFailure.INFERENCE_FAILED
    assert "RuntimeError" in crashed.observations[0].failure_detail


def test_service_honors_the_vision_budget() -> None:
    provider = StubVisionProvider()
    summary = ReportSummaryService(
        provider=provider, client=StubClient(), max_vision_defects=1
    ).build(SESSION, DEFECTS)

    assert summary.vision_described_count == 1
    assert provider.described == [12.4]
    skipped = [item for item in summary.observations if item.source == "metadata"]
    assert skipped[0].failure_code is ObservationFailure.BUDGET_EXCEEDED


def test_service_skips_images_when_asked() -> None:
    provider = StubVisionProvider()
    summary = ReportSummaryService(
        provider=provider, client=StubClient(), describe_images=False
    ).build(SESSION, DEFECTS)

    assert summary.vision_described_count == 0
    assert provider.described == []
    assert summary.status == "ready"


def test_service_falls_back_when_the_narrative_provider_raises() -> None:
    class BrokenProvider(StubVisionProvider):
        name = "broken"

        def generate_narrative(self, *, facts, grades, max_new_tokens=512):
            raise RuntimeError("CUDA out of memory")

    summary = ReportSummaryService(provider=BrokenProvider(), client=StubClient()).build(
        SESSION, DEFECTS
    )

    assert summary.status == "fallback"
    assert summary.assessment
    # A narrative failure must not disable snapshot reading.
    assert summary.vision_described_count == 2


def test_defects_without_a_snapshot_get_a_metadata_observation() -> None:
    without_image = [dict(DEFECTS[0], snapshot_path=None)]
    summary = ReportSummaryService(provider=StubVisionProvider(), client=StubClient()).build(
        SESSION, without_image
    )

    assert summary.observations[0].source == "metadata"
    assert summary.observations[0].image_url is None
    assert summary.observations[0].failure_code is ObservationFailure.NO_SNAPSHOT


def test_service_without_a_provider_reports_model_unavailable() -> None:
    summary = ReportSummaryService(provider=None, client=StubClient()).build(SESSION, DEFECTS)

    assert {item.failure_code for item in summary.observations} == {
        ObservationFailure.MODEL_UNAVAILABLE
    }
