from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_operator_terminal.py"
spec = importlib.util.spec_from_file_location("run_operator_terminal", SCRIPT)
assert spec is not None
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
OperatorClient = module.OperatorClient


class FakeRequest:
    def __init__(self, mission_state: str = "IDLE") -> None:
        self.mission_state = mission_state
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, path: str, *, method: str = "GET", payload=None):
        self.calls.append((path, method, payload))
        if path == "/api/mission":
            return {"state": self.mission_state}
        if path == "/api/mission/start":
            self.mission_state = "RUNNING"
            return {"state": "RUNNING"}
        if path == "/api/mission/complete":
            self.mission_state = "COMPLETED"
            return {"state": "COMPLETED"}
        if path == "/api/mission/reset":
            self.mission_state = "IDLE"
            return {"state": "IDLE"}
        return {}


def test_start_uses_real_v2_mission_endpoint() -> None:
    request = FakeRequest()
    client = OperatorClient(request=request)

    result = client.start("터미널 V2 주행 테스트")

    assert result["state"] == "RUNNING"
    assert request.calls == [
        ("/api/mission/start", "POST", {"area": "터미널 V2 주행 테스트"})
    ]


def test_stop_completes_mission_to_stop_and_disarm() -> None:
    request = FakeRequest("RUNNING")
    client = OperatorClient(request=request)

    result = client.stop()

    assert result["state"] == "COMPLETED"
    assert request.calls == [("/api/mission/complete", "POST", {})]


def test_safe_shutdown_stops_a_running_mission() -> None:
    request = FakeRequest("RUNNING")
    client = OperatorClient(request=request)

    client.safe_shutdown()

    assert request.calls == [
        ("/api/mission", "GET", None),
        ("/api/mission/complete", "POST", {}),
    ]


def test_safe_shutdown_does_not_touch_idle_motor_state() -> None:
    request = FakeRequest("IDLE")
    client = OperatorClient(request=request)

    client.safe_shutdown()

    assert request.calls == [("/api/mission", "GET", None)]
