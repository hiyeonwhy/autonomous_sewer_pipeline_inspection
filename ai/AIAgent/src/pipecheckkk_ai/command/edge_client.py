from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EdgeConnectionError(RuntimeError):
    pass


class HttpEdgeClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 1.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("edge base URL must use http or https")
        if not token:
            raise ValueError("edge token must not be empty")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.opener = opener

    def health(self) -> dict[str, object]:
        return self._request("GET", "/health")

    def arm(self) -> dict[str, object]:
        return self._request("POST", "/arm", {})

    def heartbeat(self) -> dict[str, object]:
        return self._request("POST", "/heartbeat", {})

    def command(self, payload: dict[str, object]) -> dict[str, object]:
        command_timeout_s = min(self.timeout_s, 0.25)
        try:
            return self._request(
                "POST",
                "/command",
                payload,
                timeout_s=command_timeout_s,
            )
        except EdgeConnectionError as original_error:
            health = self._request("GET", "/health", timeout_s=command_timeout_s)
            sequence = payload.get("sequence")
            last_sequence = health.get("lastSequence")
            edge_ready = bool(health.get("ok")) and bool(health.get("armed"))
            if edge_ready and isinstance(sequence, int) and last_sequence == sequence:
                return {
                    "commandId": payload.get("commandId", "unknown"),
                    "sequence": sequence,
                    "accepted": True,
                    "reason": "COMMAND_APPLIED_ACK_RECOVERED",
                    "appliedLeftSpeed": payload.get("leftSpeed", 0.0),
                    "appliedRightSpeed": payload.get("rightSpeed", 0.0),
                }
            if (
                edge_ready
                and isinstance(sequence, int)
                and isinstance(last_sequence, int)
                and last_sequence < sequence
            ):
                return self._request(
                    "POST",
                    "/command",
                    payload,
                    timeout_s=command_timeout_s,
                )
            raise original_error

    def stop(self, sequence: int) -> dict[str, object]:
        return self._request("POST", "/stop", {"sequence": sequence})

    def disarm(self) -> dict[str, object]:
        return self._request("POST", "/disarm", {})

    def imu_calibrate(self, samples: int = 100) -> dict[str, object]:
        return self._request(
            "POST",
            "/imu/calibrate",
            {"samples": samples},
            timeout_s=max(self.timeout_s, 3.0),
        )

    def imu_start_pitch_guard(self) -> dict[str, object]:
        return self._request("POST", "/imu/pitch-guard", {})

    def imu_start_yaw_step(self, direction: str) -> dict[str, object]:
        return self._request("POST", "/imu/yaw-step", {"direction": direction})

    def imu_capture_temporary_reference(self, name: str) -> dict[str, object]:
        return self._request(
            "POST", "/imu/temporary-reference/capture", {"name": name}
        )

    def imu_start_relative_yaw_step(self, direction: str) -> dict[str, object]:
        return self._request(
            "POST", "/imu/yaw-step-relative", {"direction": direction}
        )

    def imu_start_recovery(self) -> dict[str, object]:
        return self._request("POST", "/imu/recover", {})

    def imu_restore_reference(self, state: dict[str, object]) -> dict[str, object]:
        return self._request("POST", "/imu/reference/restore", state)

    def yaw_scan_360(self, *, deadline_s: float = 45.0) -> dict[str, object]:
        return self._request(
            "POST", "/yaw-scan-360", {"deadlineSeconds": deadline_s}
        )

    def imu_reset(self) -> dict[str, object]:
        return self._request("POST", "/imu/reset", {})

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        encoded = None if payload is None else json.dumps(payload).encode()
        request = Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "X-Edge-Token": self.token,
            },
            method=method,
        )
        try:
            with self.opener(
                request,
                timeout=self.timeout_s if timeout_s is None else timeout_s,
            ) as response:
                parsed = json.loads(response.read())
        except HTTPError as error:
            try:
                body = json.loads(error.read())
                reason = body.get("reason", body)
            except Exception:
                reason = f"HTTP {error.code}"
            raise RuntimeError(f"Edge request rejected: {reason}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise EdgeConnectionError(f"Edge connection failed: {error}") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("Edge response must be a JSON object")
        return parsed
