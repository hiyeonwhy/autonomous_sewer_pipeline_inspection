from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DecisionProvider(Protocol):
    name: str
    model_dir: Path

    def is_downloaded(self) -> bool: ...
