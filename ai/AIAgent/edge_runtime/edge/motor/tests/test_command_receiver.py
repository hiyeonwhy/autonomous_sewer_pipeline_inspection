import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from edge.motor.command_receiver import create_server
from edge.motor.command_service import EdgeCommandService
from edge.motor.local_safety import LocalSafetySupervisor
from edge.motor.server import (
    motor_network_config,
    trusted_lan_from_ip_json,
    ultrasonic_stop_m_config,
)


class RecordingMotor:
    def __init__(self) -> None:
        self.applied: list[tuple[float, float]] = []

    def set_speeds(self, left: float, right: float) -> None:
        self.applied.append((left, right))

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class RecoverPathImu:
    def __init__(self):
        self.phase = "READY"

    def status(self):
        return {"available": True, "calibrated": self.phase != "UNCALIBRATED", "phase": self.phase}

    def start_local_recovery(self, *, now, deadline_s):
        return {
            "available": True,
            "calibrated": True,
            "phase": "PRIMARY_YAW",
            "localOperation": "RECOVER_PATH",
            "operationDeadlineS": deadline_s,
        }

    def start_yaw_scan_360(self, *, now, deadline_s):
        self.phase = "YAW_SCAN_360"
        return {
            "available": True,
            "calibrated": True,
            "phase": self.phase,
            "localOperation": "YAW_SCAN_360",
            "operationDeadlineS": deadline_s,
        }

    def restore_reference_state(self, state, *, now, max_age_s):
        self.phase = "COMPLETED"
        return {**self.status(), "referenceRestored": True, "nonce": state["nonce"]}

    def capture_temporary_reference(self, name):
        return {**self.status(), "temporaryReference": {"name": name, "yawDeg": 8.8}}

    def start_relative_yaw_step(self, direction):
        self.phase = "PIVOT_YAW"
        return {**self.status(), "yawTargetDeg": 18.8 if direction == "LEFT" else -1.2}


def signed_reference_state() -> dict[str, object]:
    state: dict[str, object] = {
        "version": 1,
        "nonce": "checkpoint-12",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "phase": "COMPLETED",
        "relativeYawDeg": 2.9,
        "relativeRollDeg": -4.9,
        "relativePitchDeg": 15.0,
    }
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    state["authTag"] = hmac.new(b"secret", canonical, hashlib.sha256).hexdigest()
    return state


def post(base: str, path: str, body: dict[str, object] | None = None, token: str = "secret"):
    request = Request(
        f"{base}{path}",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", "X-Edge-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_receiver_exposes_authenticated_bounded_command_flow() -> None:
    now = datetime.now(timezone.utc)
    motor = RecordingMotor()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret")
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert post(base, "/arm")[0] == 200
        assert post(base, "/heartbeat")[0] == 200
        status, ack = post(
            base,
            "/command",
            {
                "commandId": "cmd-1",
                "missionId": "mission-1",
                "sequence": 1,
                "action": "FOLLOW_PIPE",
                "leftSpeed": 0.12,
                "rightSpeed": 0.10,
                "issuedAt": now.isoformat(),
                "expiresAt": (now + timedelta(seconds=2)).isoformat(),
            },
        )
        assert status == 200
        assert ack["accepted"] is True
        assert motor.applied == [(0.12, 0.10)]

        assert post(base, "/command", {"action": "FOLLOW_PIPE"})[0] == 400
        assert post(base, "/arm", token="wrong")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_receiver_defaults_to_loopback() -> None:
    service = EdgeCommandService(LocalSafetySupervisor(RecordingMotor()), token="secret")
    server = create_server(service, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_http_receiver_allows_laptop_on_explicit_controller_cidr() -> None:
    service = EdgeCommandService(LocalSafetySupervisor(RecordingMotor()), token="secret")
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        allowed_clients=("127.0.0.1/32",),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert post(base, "/arm")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_receiver_rejects_clients_outside_explicit_allowlist() -> None:
    service = EdgeCommandService(LocalSafetySupervisor(RecordingMotor()), token="secret")
    server = create_server(
        service,
        host="127.0.0.1",
        port=0,
        allowed_clients=("192.168.100.50/32",),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert post(base, "/arm")[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "host",
    ("", "0", "0.0.0.0", "::", "0:0:0:0:0:0:0:0"),
)
def test_http_receiver_rejects_wildcard_bind(host: str) -> None:
    service = EdgeCommandService(LocalSafetySupervisor(RecordingMotor()), token="secret")

    with pytest.raises(ValueError, match="specific trusted interface"):
        create_server(service, host=host, port=0)


def test_http_receiver_requires_nonempty_controller_allowlist() -> None:
    service = EdgeCommandService(LocalSafetySupervisor(RecordingMotor()), token="secret")

    with pytest.raises(ValueError, match="at least one allowed client"):
        create_server(service, host="127.0.0.1", port=0, allowed_clients=())


def test_server_lan_bind_requires_explicit_controller_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_MOTOR_HOST", "192.168.100.246")
    monkeypatch.delenv("PIPECHECKKK_MOTOR_ALLOWED_CLIENTS", raising=False)

    with pytest.raises(SystemExit, match="ALLOWED_CLIENTS"):
        motor_network_config()


def test_server_preserves_explicit_pi_lan_bind_and_laptop_cidr(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_MOTOR_HOST", "192.168.100.246")
    monkeypatch.setenv("PIPECHECKKK_MOTOR_ALLOWED_CLIENTS", "192.168.100.50/32")

    assert motor_network_config() == (
        "192.168.100.246",
        ("192.168.100.50/32",),
    )


def test_trusted_lan_uses_default_route_interface_ipv4_subnet() -> None:
    routes = [{"dst": "default", "dev": "wlan0", "gateway": "192.168.43.1"}]
    addresses = [
        {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.0.0.8", "prefixlen": 24}]},
        {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "192.168.43.169", "prefixlen": 24}]},
    ]

    assert trusted_lan_from_ip_json(routes, addresses) == ("192.168.43.169", "192.168.43.0/24")


def test_server_auto_bind_uses_discovered_interface_and_subnet(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_MOTOR_HOST", "auto")
    monkeypatch.setenv("PIPECHECKKK_MOTOR_ALLOWED_CLIENTS", "auto")

    assert motor_network_config(auto_resolver=lambda: ("192.168.43.169", "192.168.43.0/24")) == (
        "192.168.43.169",
        ("192.168.43.0/24",),
    )


def test_server_reads_five_centimeter_ultrasonic_stop_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_ULTRASONIC_STOP_M", "0.05")

    assert ultrasonic_stop_m_config() == 0.05


def test_server_accepts_zero_to_disable_ultrasonic_distance_hard_stop(monkeypatch) -> None:
    monkeypatch.setenv("PIPECHECKKK_ULTRASONIC_STOP_M", "0.0")

    assert ultrasonic_stop_m_config() == 0.0


def test_http_receiver_exposes_dedicated_recover_path_atomic_start() -> None:
    service = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=RecoverPathImu(),
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, response = post(
            base, "/recover-path", {"deadlineSeconds": 55.0}
        )

        assert status == 202
        assert response["phase"] == "PRIMARY_YAW"
        assert response["localOperation"] == "RECOVER_PATH"
        assert post(base, "/recover-path", token="wrong")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_receiver_exposes_reference_restore_and_yaw_scan_routes(tmp_path) -> None:
    imu = RecoverPathImu()
    imu.phase = "UNCALIBRATED"
    service = EdgeCommandService(
        LocalSafetySupervisor(RecordingMotor()),
        token="secret",
        imu=imu,
        reference_nonce_store=tmp_path / "reference-nonces.json",
    )
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        restored_status, restored = post(
            base,
            "/imu/reference/restore",
            signed_reference_state(),
        )
        scan_status, scan = post(
            base, "/yaw-scan-360", {"deadlineSeconds": 45.0}
        )

        assert restored_status == 200
        assert restored["referenceRestored"] is True
        assert scan_status == 202
        assert scan["phase"] == "YAW_SCAN_360"
        assert post(base, "/yaw-scan-360", token="wrong")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_temporary_reference_and_relative_yaw_routes_are_exposed() -> None:
    motor = RecordingMotor()
    imu = RecoverPathImu()
    service = EdgeCommandService(LocalSafetySupervisor(motor), token="secret", imu=imu)
    server = create_server(service, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        captured_status, captured = post(
            base, "/imu/temporary-reference/capture", {"name": "AVOIDANCE"}
        )
        assert captured_status == 200
        assert captured["temporaryReference"]["yawDeg"] == pytest.approx(8.8)

        assert post(base, "/arm")[0] == 200
        started_status, started = post(
            base, "/imu/yaw-step-relative", {"direction": "LEFT"}
        )
        assert started_status == 200
        assert started["yawTargetDeg"] == pytest.approx(18.8)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
