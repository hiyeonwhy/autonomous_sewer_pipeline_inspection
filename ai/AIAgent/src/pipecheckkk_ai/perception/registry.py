from __future__ import annotations

from pathlib import Path

from pipecheckkk_ai.model_registry import get_model_spec
from pipecheckkk_ai.perception.base import PerceptionProvider


def create_perception_provider() -> PerceptionProvider:
    from pipecheckkk_ai.perception.depth_freespace.adapter import DepthFreeSpaceProvider

    spec = get_model_spec("depth-anything-v2-small")
    model_dir = Path(__file__).parents[3] / spec.local_dir
    return DepthFreeSpaceProvider(model_dir=model_dir)
