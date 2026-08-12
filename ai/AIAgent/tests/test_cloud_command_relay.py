from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "cloud_command_relay.py"
SPEC = importlib.util.spec_from_file_location("cloud_command_relay", SCRIPT)
assert SPEC and SPEC.loader
relay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay_module)


def test_claimed_start_is_forwarded_to_loopback_and_acknowledged(tmp_path) -> None:
    calls: list[tuple[str, str, dict | None, dict[str, str]]] = []

    def transport(method, url, payload, headers):
        calls.append((method, url, payload, headers))
        if url.endswith("claim?agent_id=laptop-4070"):
            return 200, {
                "command_id": "command-1",
                "claim_token": "a" * 32,
                "command_type": "START",
                "area": "하남동",
                "session_id": 152,
                "operation_id": "inspection-152",
            }
        if url == "http://127.0.0.1:8000/api/hermes/start":
            return 202, {"state": "RUNNING"}
        if url.endswith("/command-1/ack"):
            return 200, {"status": "SUCCEEDED"}
        raise AssertionError(url)

    relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=tmp_path / "ledger.json",
        transport=transport,
    )

    assert relay.run_once() is True
    assert calls[1][1] == "http://127.0.0.1:8000/api/hermes/start"
    assert calls[1][2] == {
        "area": "하남동",
        "session_id": 152,
        "operation_id": "inspection-152",
    }
    assert calls[2][2]["claim_token"] == "a" * 32
    assert calls[2][2]["status"] == "SUCCEEDED"


def test_relay_accepts_only_loopback_agent_target(tmp_path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        relay_module.CloudCommandRelay(
            cloud_url="https://i15c102.p.ssafy.io",
            agent_url="http://192.168.1.5:8000",
            agent_id="laptop-4070",
            ledger_path=tmp_path / "ledger.json",
        )


def test_claimed_stop_is_forwarded_to_loopback_and_acknowledged(tmp_path) -> None:
    calls = []

    def transport(method, url, payload, headers):
        calls.append((method, url, payload, headers))
        if url.endswith("claim?agent_id=laptop-4070"):
            return 200, {
                "command_id": "command-stop",
                "claim_token": "b" * 32,
                "command_type": "STOP",
            }
        if url == "http://127.0.0.1:8000/api/hermes/stop":
            return 200, {"state": "STOPPED"}
        if url.endswith("/command-stop/ack"):
            return 200, {"status": "SUCCEEDED"}
        raise AssertionError(url)

    relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=tmp_path / "ledger.json",
        transport=transport,
    )

    assert relay.run_once() is True
    assert calls[1][1] == "http://127.0.0.1:8000/api/hermes/stop"
    assert calls[1][2] is None
    assert calls[2][2]["status"] == "SUCCEEDED"


def test_failed_ack_is_retried_without_repeating_local_start(tmp_path) -> None:
    calls = []
    ack_attempts = 0

    def transport(method, url, payload, headers):
        nonlocal ack_attempts
        calls.append((method, url, payload, headers))
        if url.endswith("claim?agent_id=laptop-4070"):
            return 200, {
                "command_id": "command-retry",
                "claim_token": "c" * 32,
                "command_type": "START",
                "area": "하남동",
                "session_id": 152,
                "operation_id": "inspection-152",
            }
        if url == "http://127.0.0.1:8000/api/hermes/start":
            return 202, {"state": "RUNNING"}
        if url.endswith("/command-retry/ack"):
            ack_attempts += 1
            return (500, {"detail": "temporary"}) if ack_attempts == 1 else (200, {})
        raise AssertionError(url)

    relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=tmp_path / "ledger.json",
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="ACK failed"):
        relay.run_once()
    assert relay.run_once() is True
    assert sum(url.endswith("/api/hermes/start") for _, url, _, _ in calls) == 1
    assert sum(url.endswith("claim?agent_id=laptop-4070") for _, url, _, _ in calls) == 1
    assert ack_attempts == 2


def test_stale_ack_conflict_is_dropped_so_later_commands_can_be_claimed(tmp_path) -> None:
    calls = []

    def transport(method, url, payload, headers):
        calls.append(url)
        if url.endswith("/stale-command/ack"):
            return 409, {"detail": "claim lease superseded"}
        if url.endswith("claim?agent_id=laptop-4070"):
            return 204, None
        raise AssertionError(url)

    relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=tmp_path / "ledger.json",
        transport=transport,
    )
    relay.pending_ack = (
        "stale-command",
        "9" * 32,
        "SUCCEEDED",
        "local Agent accepted START",
    )

    assert relay.run_once() is True
    assert relay.pending_ack is None
    assert relay.run_once() is False
    assert calls[-1].endswith("claim?agent_id=laptop-4070")


def test_relay_restart_never_repeats_an_already_forwarded_start(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    claim_count = 0
    local_start_count = 0
    ack_statuses = []

    def transport(method, url, payload, headers):
        nonlocal claim_count, local_start_count
        if url.endswith("claim?agent_id=laptop-4070"):
            claim_count += 1
            return 200, {
                "command_id": "command-crash-window",
                "claim_token": ("d" if claim_count == 1 else "e") * 32,
                "command_type": "START",
                "area": "하남동",
                "session_id": 152,
                "operation_id": "inspection-152",
            }
        if url == "http://127.0.0.1:8000/api/hermes/start":
            local_start_count += 1
            return 202, {"state": "RUNNING"}
        if url.endswith("/command-crash-window/ack"):
            ack_statuses.append(payload["status"])
            return (500, {}) if len(ack_statuses) == 1 else (200, {})
        raise AssertionError(url)

    first_relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=ledger,
        transport=transport,
    )
    with pytest.raises(RuntimeError, match="ACK failed"):
        first_relay.run_once()

    restarted_relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=ledger,
        transport=transport,
    )
    assert restarted_relay.run_once() is True

    assert local_start_count == 1
    assert ack_statuses == ["SUCCEEDED", "FAILED"]


def test_relay_restart_replays_stop_until_it_is_acknowledged(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    claim_count = 0
    local_stop_count = 0

    def transport(method, url, payload, headers):
        nonlocal claim_count, local_stop_count
        if url.endswith("claim?agent_id=laptop-4070"):
            claim_count += 1
            return 200, {
                "command_id": "command-stop-crash-window",
                "claim_token": ("f" if claim_count == 1 else "0") * 32,
                "command_type": "STOP",
            }
        if url == "http://127.0.0.1:8000/api/hermes/stop":
            local_stop_count += 1
            return 200, {"state": "STOPPED"}
        if url.endswith("/command-stop-crash-window/ack"):
            return (500, {}) if local_stop_count == 1 else (200, {})
        raise AssertionError(url)

    first_relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=ledger,
        transport=transport,
    )
    with pytest.raises(RuntimeError, match="ACK failed"):
        first_relay.run_once()

    restarted_relay = relay_module.CloudCommandRelay(
        cloud_url="https://i15c102.p.ssafy.io",
        agent_url="http://127.0.0.1:8000",
        agent_id="laptop-4070",
        ledger_path=ledger,
        transport=transport,
    )
    assert restarted_relay.run_once() is True
    assert local_stop_count == 2
