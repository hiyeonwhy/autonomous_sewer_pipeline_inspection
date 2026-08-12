from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "start_agent_auto_pi.py"
SPEC = importlib.util.spec_from_file_location("start_agent_auto_pi", SCRIPT)
assert SPEC and SPEC.loader
start_agent_auto_pi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(start_agent_auto_pi)


def test_detects_existing_pipecheckkk_agent_from_health() -> None:
    calls: list[tuple[str, float]] = []

    def fetch(url: str, timeout: float) -> object:
        calls.append((url, timeout))
        return {"ok": True, "service": "pipecheckkk-ai"}

    assert start_agent_auto_pi.agent_already_running(fetch=fetch) is True
    assert calls == [("http://127.0.0.1:8000/health", 1.0)]
