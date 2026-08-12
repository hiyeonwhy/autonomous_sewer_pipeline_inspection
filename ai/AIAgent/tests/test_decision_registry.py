import pytest

from pipecheckkk_ai.decision.qwen2_5_vl_3b_instruct_awq.adapter import parse_proposal_text
from pipecheckkk_ai.decision.registry import create_decision_provider
from pipecheckkk_ai.model_registry import model_files_present


def test_qwen_json_proposal_is_restricted_to_action_schema() -> None:
    proposal = parse_proposal_text(
        '판단 결과: {"action":"AVOID_LEFT","confidence":0.82,'
        '"reason":"왼쪽 공간이 더 넓음","fallback":"STOP"}',
        latency_ms=123.0,
    )

    assert proposal.action == "AVOID_LEFT"
    assert proposal.confidence == 0.82
    assert proposal.fallback == "STOP"
    assert proposal.model_name == "qwen2.5-vl-3b-instruct-awq"


def test_decision_provider_is_selected_by_stable_model_name() -> None:
    provider = create_decision_provider("qwen2.5-vl-3b-instruct-awq")

    assert provider.name == "qwen2.5-vl-3b-instruct-awq"
    assert provider.model_dir.name == "weights"
    assert provider.is_downloaded() is model_files_present(provider.name)


def test_unknown_decision_provider_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown decision provider"):
        create_decision_provider("made-up-model")
