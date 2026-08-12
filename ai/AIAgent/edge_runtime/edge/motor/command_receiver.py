from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from socket import SOCK_STREAM, getaddrinfo
from typing import Any, Iterable

from edge.motor.command_service import EdgeCommandService


MAX_BODY_BYTES = 65_536


def create_server(
    service: EdgeCommandService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allowed_clients: Iterable[str] = ("127.0.0.1/32", "::1/128"),
) -> ThreadingHTTPServer:
    if not host.strip():
        raise ValueError("motor server must bind a specific trusted interface, not a wildcard")
    try:
        bind_addresses = {
            ip_address(str(address[4][0]).split("%", 1)[0])
            for address in getaddrinfo(host, None, type=SOCK_STREAM)
        }
    except OSError as error:
        raise ValueError("motor server bind host could not be resolved") from error
    if any(address.is_unspecified for address in bind_addresses):
        raise ValueError("motor server must bind a specific trusted interface, not a wildcard")
    allowed_networks = tuple(ip_network(value.strip(), strict=False) for value in allowed_clients)
    if not allowed_networks:
        raise ValueError("motor server requires at least one allowed client network")

    class CommandHandler(BaseHTTPRequestHandler):
        def _client_is_allowed(self) -> bool:
            client_ip = ip_address(self.client_address[0].split("%", 1)[0])
            return any(client_ip in network for network in allowed_networks)

        def do_GET(self) -> None:  # noqa: N802
            if not self._client_is_allowed():
                self._send(HTTPStatus.FORBIDDEN, {"ok": False, "reason": "CLIENT_NOT_ALLOWED"})
                return
            if self.path != "/health":
                self._send(HTTPStatus.NOT_FOUND, {"ok": False, "reason": "NOT_FOUND"})
                return
            self._send(HTTPStatus.OK, service.health())

        def do_POST(self) -> None:  # noqa: N802
            if not self._client_is_allowed():
                self._send(HTTPStatus.FORBIDDEN, {"ok": False, "reason": "CLIENT_NOT_ALLOWED"})
                return
            token = self.headers.get("X-Edge-Token", "")
            try:
                payload = self._read_json()
            except ValueError as error:
                service.safety.disarm("MALFORMED_HTTP_BODY")
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "reason": "MALFORMED_HTTP_BODY", "detail": str(error)},
                )
                return

            if self.path == "/arm":
                status, response = service.arm(token)
            elif self.path == "/disarm":
                status, response = service.disarm(token)
            elif self.path == "/heartbeat":
                status, response = service.heartbeat(token)
            elif self.path == "/command":
                status, response = service.command(token, payload)
            elif self.path == "/stop":
                raw_sequence = payload.get("sequence", 0)
                sequence = raw_sequence if isinstance(raw_sequence, int) else 0
                status, response = service.stop(token, sequence=sequence)
            elif self.path == "/imu/calibrate":
                status, response = service.imu_calibrate(token, payload)
            elif self.path == "/imu/yaw-step":
                status, response = service.imu_start_yaw_step(token, payload)
            elif self.path == "/imu/yaw-step-current":
                status, response = service.imu_start_current_yaw_step(token, payload)
            elif self.path == "/imu/temporary-reference/capture":
                status, response = service.imu_capture_temporary_reference(token, payload)
            elif self.path == "/imu/temporary-reference/clear":
                status, response = service.imu_clear_temporary_reference(token)
            elif self.path == "/imu/temporary-reference/restore":
                status, response = service.imu_restore_temporary_reference(token, payload)
            elif self.path == "/imu/yaw-step-relative":
                status, response = service.imu_start_relative_yaw_step(token, payload)
            elif self.path == "/imu/pitch-guard":
                status, response = service.imu_start_pitch_guard(token)
            elif self.path == "/imu/recover":
                status, response = service.imu_start_recovery(token)
            elif self.path == "/forward-timed-continuous":
                status, response = service.forward_timed_continuous(token, payload)
            elif self.path == "/forward-until-distance":
                status, response = service.forward_until_distance(token, payload)
            elif self.path == "/recover-path":
                status, response = service.recover_path(token, payload)
            elif self.path == "/yaw-scan-360":
                status, response = service.yaw_scan_360(token, payload)
            elif self.path == "/yaw-temp-180":
                status, response = service.yaw_scan_360(
                    token, {**payload, "temporaryReferenceName": "AVOIDANCE"}
                )
            elif self.path == "/imu/reference/restore":
                status, response = service.imu_restore_reference(token, payload)
            elif self.path == "/imu/reset":
                status, response = service.imu_reset(token)
            else:
                status, response = HTTPStatus.NOT_FOUND, {
                    "ok": False,
                    "reason": "NOT_FOUND",
                }
            self._send(status, response)

        def _read_json(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise ValueError("invalid content length") from error
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("request body exceeds limit")
            if length == 0:
                return {}
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send(self, status: int, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), CommandHandler)
