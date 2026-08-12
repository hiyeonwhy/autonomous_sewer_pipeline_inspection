from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ModelRole(StrEnum):
    PERCEPTION = "perception"
    DECISION = "decision"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    role: ModelRole
    repo_id: str
    local_dir: Path
    runtime: str


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "depth-anything-v2-small": ModelSpec(
        name="depth-anything-v2-small",
        role=ModelRole.PERCEPTION,
        repo_id="depth-anything/Depth-Anything-V2-Small-hf",
        local_dir=Path("models/depth-anything-v2-small/weights"),
        runtime="transformers-depth",
    ),
    "qwen2.5-vl-3b-instruct-awq": ModelSpec(
        name="qwen2.5-vl-3b-instruct-awq",
        role=ModelRole.DECISION,
        repo_id="Qwen/Qwen2.5-VL-3B-Instruct-AWQ",
        local_dir=Path("models/qwen2.5-vl-3b-instruct-awq/weights"),
        runtime="transformers-awq",
    ),
}


def get_model_spec(name: str) -> ModelSpec:
    try:
        return MODEL_REGISTRY[name]
    except KeyError as error:
        raise KeyError(f"등록되지 않은 모델입니다: {name}") from error


def model_files_present(name: str) -> bool:
    spec = get_model_spec(name)
    model_dir = Path(__file__).parents[2] / spec.local_dir
    if name == "depth-anything-v2-small":
        required = ("config.json", "preprocessor_config.json", "model.safetensors")
    elif spec.role is ModelRole.PERCEPTION:
        required = ("config.json", "preprocessor_config.json", "pytorch_model.bin")
    else:
        required = ("config.json", "preprocessor_config.json", "model.safetensors")
    return all((model_dir / filename).is_file() for filename in required)
