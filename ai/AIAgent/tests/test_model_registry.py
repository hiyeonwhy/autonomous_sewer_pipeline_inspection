from pathlib import Path

import pytest

from pipecheckkk_ai.model_registry import MODEL_REGISTRY, ModelRole, get_model_spec


def test_v2_models_are_grouped_by_stable_names_and_roles() -> None:
    depth = get_model_spec("depth-anything-v2-small")
    decision = get_model_spec("qwen2.5-vl-3b-instruct-awq")

    assert depth.role is ModelRole.PERCEPTION
    assert depth.repo_id == "depth-anything/Depth-Anything-V2-Small-hf"
    assert depth.local_dir == Path("models/depth-anything-v2-small/weights")

    assert decision.role is ModelRole.DECISION
    assert decision.repo_id == "Qwen/Qwen2.5-VL-3B-Instruct-AWQ"
    assert decision.local_dir == Path("models/qwen2.5-vl-3b-instruct-awq/weights")
    assert set(MODEL_REGISTRY) == {
        "depth-anything-v2-small",
        "qwen2.5-vl-3b-instruct-awq",
    }


def test_unknown_model_name_is_rejected() -> None:
    with pytest.raises(KeyError, match="등록되지 않은 모델"):
        get_model_spec("unknown-model")
