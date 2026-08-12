from __future__ import annotations

from pathlib import Path


class RuleDecisionProvider:
    name = "rule-policy"
    model_dir = Path("built-in")

    def is_downloaded(self) -> bool:
        return True
