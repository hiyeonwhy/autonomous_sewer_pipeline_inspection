import json
from urllib.error import HTTPError, URLError

import pytest

from pipecheckkk_ai.command.edge_client import HttpEdgeClient


class FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def close(self) -> None:
        return None


class RecordingOpener:
    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            FakeResponse({"ok": True}),
            FakeResponse({"armed": True, "reason": "ARMED"}),
            FakeResponse({"ok": True, "armed": True}),
        ]

    def __call__(self, request, timeout: float):
        assert timeout == 1.0
        self.requests.append(request)
        return self.responses.pop(0)


def test_http_edge_client_sends_token_and_json_contract() -> None:
    opener = RecordingOpener()
    client = HttpEdgeClient("http://192.168.100.246:8765", "secret", opener=opener)

    assert client.health() == {"ok": True}
    assert client.arm()["armed"] is True
    assert client.heartbeat()["armed"] is True

    assert opener.requests[0].full_url.endswith("/health")
    assert opener.requests[1].get_header("X-edge-token") == "secret"
    assert opener.requests[1].method == "POST"
    assert json.loads(opener.requests[1].data) == {}


def test_http_edge_client_surfaces_edge_rejection() -> None:
    def reject(request, timeout: float):
        raise HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            FakeResponse({"accepted": False, "reason": "COMMAND_EXPIRED"}),
        )

    client = HttpEdgeClient("http://edge:8765", "secret", opener=reject)

    with pytest.raises(RuntimeError, match="COMMAND_EXPIRED"):
        client.command({"commandId": "cmd-1"})


def test_imu_calibration_uses_extended_timeout_without_changing_normal_requests() -> None:
    observed: list[tuple[str, float]] = []

    def record(request, timeout: float):
        observed.append((request.full_url, timeout))
        return FakeResponse({"ok": True, "phase": "READY"})

    client = HttpEdgeClient(
        "http://edge:8765",
        "secret",
        timeout_s=1.0,
        opener=record,
    )

    client.health()
    client.imu_calibrate(samples=100)

    assert observed == [
        ("http://edge:8765/health", 1.0),
        ("http://edge:8765/imu/calibrate", 3.0),
    ]


def test_yaw_scan_and_reference_restore_use_dedicated_authenticated_routes() -> None:
    observed = []

    def opener(request, timeout):
        observed.append((request.full_url, json.loads(request.data), request.headers["X-edge-token"]))
        return FakeResponse({"ok": True})

    client = HttpEdgeClient("http://edge:8765", "secret", opener=opener)
    client.imu_restore_reference({"version": 1, "nonce": "checkpoint-12"})
    client.yaw_scan_360(deadline_s=45.0)

    assert observed == [
        (
            "http://edge:8765/imu/reference/restore",
            {"version": 1, "nonce": "checkpoint-12"},
            "secret",
        ),
        (
            "http://edge:8765/yaw-scan-360",
            {"deadlineSeconds": 45.0},
            "secret",
        ),
    ]


def test_temporary_reference_client_uses_dedicated_authenticated_routes() -> None:
    observed = []

    def opener(request, timeout):
        observed.append((request.full_url, json.loads(request.data), request.headers["X-edge-token"]))
        return FakeResponse({"ok": True})

    client = HttpEdgeClient("http://edge:8765", "secret", opener=opener)
    client.imu_capture_temporary_reference("AVOIDANCE")
    client.imu_start_relative_yaw_step("LEFT")

    assert observed == [
        (
            "http://edge:8765/imu/temporary-reference/capture",
            {"name": "AVOIDANCE"},
            "secret",
        ),
        (
            "http://edge:8765/imu/yaw-step-relative",
            {"direction": "LEFT"},
            "secret",
        ),
    ]


def test_imu_yaw_step_posts_direction_to_pi_endpoint() -> None:
    observed: list[tuple[str, dict[str, object]]] = []

    def record(request, timeout: float):
        assert timeout == 1.0
        observed.append((request.full_url, json.loads(request.data)))
        return FakeResponse({"phase": "PIVOT_YAW", "yawTargetDeg": -10.0})

    client = HttpEdgeClient("http://edge:8765", "secret", opener=record)

    response = client.imu_start_yaw_step("RIGHT")

    assert response["phase"] == "PIVOT_YAW"
    assert observed == [("http://edge:8765/imu/yaw-step", {"direction": "RIGHT"})]


def test_command_timeout_recovers_ack_when_pi_already_applied_sequence() -> None:
    calls: list[tuple[str, float]] = []

    def lose_command_ack(request, timeout: float):
        calls.append((request.full_url, timeout))
        if request.full_url.endswith("/command"):
            raise URLError(TimeoutError("timed out"))
        return FakeResponse({"ok": True, "armed": True, "lastSequence": 7})

    client = HttpEdgeClient("http://edge:8765", "secret", opener=lose_command_ack)
    response = client.command(
        {
            "commandId": "cmd-7",
            "sequence": 7,
            "leftSpeed": 0.5,
            "rightSpeed": 0.5,
        }
    )

    assert response["accepted"] is True
    assert response["commandId"] == "cmd-7"
    assert response["sequence"] == 7
    assert response["reason"] == "COMMAND_APPLIED_ACK_RECOVERED"
    assert calls == [
        ("http://edge:8765/command", 0.25),
        ("http://edge:8765/health", 0.25),
    ]


def test_command_connect_timeout_retries_once_when_pi_did_not_apply_sequence() -> None:
    calls: list[tuple[str, float]] = []
    command_attempts = 0

    def recover_connection(request, timeout: float):
        nonlocal command_attempts
        calls.append((request.full_url, timeout))
        if request.full_url.endswith("/health"):
            return FakeResponse({"ok": True, "armed": True, "lastSequence": 6})
        command_attempts += 1
        if command_attempts == 1:
            raise URLError(TimeoutError("timed out"))
        return FakeResponse(
            {
                "commandId": "cmd-7",
                "sequence": 7,
                "accepted": True,
                "reason": "COMMAND_APPLIED",
            }
        )

    client = HttpEdgeClient("http://edge:8765", "secret", opener=recover_connection)
    response = client.command({"commandId": "cmd-7", "sequence": 7})

    assert response["accepted"] is True
    assert calls == [
        ("http://edge:8765/command", 0.25),
        ("http://edge:8765/health", 0.25),
        ("http://edge:8765/command", 0.25),
    ]
