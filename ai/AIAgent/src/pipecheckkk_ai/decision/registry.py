from __future__ import annotations

from pipecheckkk_ai.decision.base import DecisionProvider


def create_decision_provider(name: str) -> DecisionProvider:
    if name == "qwen2.5-vl-3b-instruct-awq":
        from pipecheckkk_ai.decision.qwen2_5_vl_3b_instruct_awq.adapter import (
            Qwen25Vl3BAwqProvider,
        )

        return Qwen25Vl3BAwqProvider()
    if name == "rule-policy":
        from pipecheckkk_ai.decision.rules.adapter import RuleDecisionProvider

        return RuleDecisionProvider()
    raise ValueError(f"unknown decision provider: {name}")
