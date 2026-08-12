"""Dashboard-to-Discord Hermes VL inspection relay."""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable


@dataclass(frozen=True)
class HermesRelayStatus:
    state: str = "IDLE"
    command: str | None = None
    message: str = "AI 점검 명령을 기다리고 있습니다."
    started_at: datetime | None = None
    ended_at: datetime | None = None


def _validate_payload(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"image_path", "edge_status"}:
        raise ValueError("VL payload must contain only image_path and edge_status")
    image_path = payload.get("image_path")
    edge_status = payload.get("edge_status")
    if not isinstance(image_path, str) or not Path(image_path).is_absolute():
        raise ValueError("image_path must be an absolute local path")
    if not isinstance(edge_status, dict):
        raise ValueError("edge_status must be an object")
    return {"image_path": image_path, "edge_status": edge_status}


async def run_hermes_discord_webhook(
    payload: dict[str, object],
    *,
    webhook_name: str = "pipecheckkk-vl",
    timeout_s: float = 120.0,
) -> str:
    """Trigger the configured Hermes webhook; it analyzes and replies in Discord."""
    validated = _validate_payload(payload)
    hermes_path = shutil.which("hermes")
    if hermes_path is None:
        raise RuntimeError("Hermes CLI is unavailable")
    payload_json = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
    process = await asyncio.create_subprocess_exec(
        hermes_path,
        "webhook",
        "test",
        webhook_name,
        "--payload",
        payload_json,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Hermes Discord webhook failed: {detail[:300]}")
    output = stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RuntimeError("Hermes Discord webhook returned no delivery result")
    return output


class HermesInspectionRelay:
    def __init__(
        self,
        runner: Callable[[dict[str, object]], Awaitable[str]] | None = None,
    ) -> None:
        self._runner = runner or run_hermes_discord_webhook
        self._task: asyncio.Task[None] | None = None
        self._status = HermesRelayStatus()

    def status(self) -> HermesRelayStatus:
        return replace(self._status)

    async def start(self, payload: dict[str, object]) -> HermesRelayStatus:
        if self._status.state == "RUNNING":
            raise RuntimeError("Hermes inspection already running")
        validated = _validate_payload(payload)
        self._status = HermesRelayStatus(
            state="RUNNING",
            command="점검시작",
            message="실제 이미지와 센서값을 Discord Hermes VL에 전달했습니다.",
            started_at=datetime.now(timezone.utc),
        )
        self._task = asyncio.create_task(self._run(validated))
        return self.status()

    async def _run(self, payload: dict[str, object]) -> None:
        try:
            await self._runner(payload)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._status = replace(
                self._status,
                state="ERROR",
                message=f"Hermes Discord VL 전달 오류: {type(error).__name__}",
                ended_at=datetime.now(timezone.utc),
            )

    async def _retire_task(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def complete(self, message: str = "STOP_DISARMED") -> HermesRelayStatus:
        if self._status.state != "RUNNING":
            raise RuntimeError("Hermes inspection is not running")
        await self._retire_task()
        verified = message == "STOP_DISARMED"
        self._status = replace(
            self._status,
            state="IDLE" if verified else "ERROR",
            command=None,
            message=("AI 점검 명령을 기다리고 있습니다." if verified else message),
            ended_at=datetime.now(timezone.utc),
        )
        return self.status()

    async def stop(self) -> HermesRelayStatus:
        await self._retire_task()
        self._status = replace(
            self._status,
            state="IDLE",
            command=None,
            message="AI 점검 명령을 기다리고 있습니다.",
            ended_at=datetime.now(timezone.utc),
        )
        return self.status()

    async def wait(self) -> HermesRelayStatus:
        if self._task is not None:
            await self._task
        return self.status()
