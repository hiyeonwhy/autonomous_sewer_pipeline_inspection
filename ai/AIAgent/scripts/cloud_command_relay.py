#!/usr/bin/env python3
"""Relay unauthenticated Cloud START/STOP commands to the loopback Agent."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

Transport = Callable[[str, str, dict[str, object] | None, dict[str, str]], tuple[int, object | None]]


def http_transport(
    method: str,
    url: str,
    payload: dict[str, object] | None,
    headers: dict[str, str],
) -> tuple[int, object | None]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urlopen(request, timeout=8) as response:  # noqa: S310 - configured Cloud/loopback URLs
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        raw = error.read()
        detail: object | None = None
        if raw:
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw.decode("utf-8", errors="replace")
        return error.code, detail


class CloudCommandRelay:
    def __init__(
        self,
        *,
        cloud_url: str,
        agent_url: str,
        agent_id: str,
        ledger_path: str | os.PathLike[str],
        transport: Transport = http_transport,
    ) -> None:
        if not cloud_url.startswith("https://"):
            raise ValueError("remote command Cloud URL must use HTTPS")
        if agent_url not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
            raise ValueError("remote command target must remain the loopback Agent")
        self.cloud_url = cloud_url.rstrip("/")
        self.agent_url = agent_url.rstrip("/")
        self.agent_id = agent_id
        self.headers: dict[str, str] = {}
        self.transport = transport
        self.pending_ack: tuple[str, str, str, str] | None = None
        self.ledger_path = Path(ledger_path)
        self.forwarded_command_ids = self._load_ledger()

    def _load_ledger(self) -> list[str]:
        if not self.ledger_path.exists():
            return []
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"command ledger cannot be read: {error}") from error
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise RuntimeError("command ledger must contain a JSON string list")
        return payload[-256:]

    def _remember_forwarded(self, command_id: str) -> None:
        if command_id in self.forwarded_command_ids:
            return
        updated = [*self.forwarded_command_ids, command_id][-256:]
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_suffix(f"{self.ledger_path.suffix}.tmp")
        temporary.write_text(json.dumps(updated), encoding="utf-8")
        os.replace(temporary, self.ledger_path)
        self.forwarded_command_ids = updated

    def _ack(self, command_id: str, claim_token: str, status: str, message: str) -> None:
        code, payload = self.transport(
            "POST",
            f"{self.cloud_url}/api/agent/commands/{command_id}/ack",
            {
                "agent_id": self.agent_id,
                "claim_token": claim_token,
                "status": status,
                "message": message[:500],
            },
            self.headers,
        )
        if code == 409:
            # This claim lease was superseded or already completed. Retrying the
            # stale ACK forever would block later commands, especially STOP.
            return
        if code != 200:
            raise RuntimeError(f"command ACK failed: HTTP {code} {payload}")

    def run_once(self) -> bool:
        if self.pending_ack is not None:
            self._ack(*self.pending_ack)
            self.pending_ack = None
            return True

        query = urlencode({"agent_id": self.agent_id})
        code, command = self.transport(
            "POST",
            f"{self.cloud_url}/api/agent/commands/claim?{query}",
            None,
            self.headers,
        )
        if code == 204:
            return False
        if code != 200 or not isinstance(command, dict):
            raise RuntimeError(f"command claim failed: HTTP {code} {command}")

        command_id = str(command.get("command_id") or "")
        claim_token = str(command.get("claim_token") or "")
        command_type = command.get("command_type")
        if command_type not in {"START", "STOP"} or not command_id or len(claim_token) != 32:
            if command_id and len(claim_token) == 32:
                self._ack(command_id, claim_token, "FAILED", "unsupported remote command")
            return True

        if command_type == "START" and command_id in self.forwarded_command_ids:
            self.pending_ack = (
                command_id,
                claim_token,
                "FAILED",
                "command was already forwarded; local action was not repeated",
            )
            self._ack(*self.pending_ack)
            self.pending_ack = None
            return True

        local_payload: dict[str, Any] | None = None
        local_path = "/api/hermes/stop"
        expected_codes = {200}
        if command_type == "START":
            local_path = "/api/hermes/start"
            expected_codes = {202}
            local_payload = {
                "area": command.get("area"),
                "session_id": command.get("session_id"),
                "operation_id": command.get("operation_id"),
            }
        # START is at-most-once: persist before crossing the local physical-command
        # boundary so an unknown crash outcome cannot restart an active mission.
        # STOP is deliberately replayable because repeated STOP is idempotent and
        # safer than losing a stop during the same crash window.
        if command_type == "START":
            self._remember_forwarded(command_id)
        local_code, local_result = self.transport(
            "POST",
            f"{self.agent_url}{local_path}",
            local_payload,
            {},
        )
        if local_code in expected_codes:
            self.pending_ack = (
                command_id,
                claim_token,
                "SUCCEEDED",
                f"local Agent accepted {command_type}",
            )
        else:
            self.pending_ack = (
                command_id,
                claim_token,
                "FAILED",
                f"local Agent HTTP {local_code}: {local_result}",
            )
        self._ack(*self.pending_ack)
        self.pending_ack = None
        return True


def main() -> int:
    relay = CloudCommandRelay(
        cloud_url=os.environ["PIPECHECKKK_CLOUD_API_URL"],
        agent_url=os.environ.get("PIPECHECKKK_LOCAL_AGENT_URL", "http://127.0.0.1:8000"),
        agent_id=os.environ.get("PIPECHECKKK_AGENT_ID", "laptop-4070"),
        ledger_path=os.environ.get(
            "PIPECHECKKK_COMMAND_LEDGER_PATH",
            str(Path.home() / ".local/state/pipecheckkk/cloud-command-relay.json"),
        ),
    )
    poll_s = max(0.25, float(os.environ.get("PIPECHECKKK_COMMAND_POLL_S", "0.5")))
    error_backoff_s = max(3.0, float(os.environ.get("PIPECHECKKK_COMMAND_ERROR_BACKOFF_S", "5")))
    while True:
        sleep_s = poll_s
        try:
            relay.run_once()
        except Exception as error:
            print(f"cloud command relay unavailable: {type(error).__name__}: {error}", flush=True)
            sleep_s = error_backoff_s
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
