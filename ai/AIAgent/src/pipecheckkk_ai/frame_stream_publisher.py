from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

LOGGER = logging.getLogger(__name__)


def connection_error_summary(error: Exception) -> str:
    """Return safe connection diagnostics without response bodies or credentials."""
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return f"{type(error).__name__}(status={status_code})"
    return type(error).__name__


def cloud_websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("cloud API URL must use HTTP or HTTPS")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/ws/agent/frame-stream/publish", "", ""))


class CloudFrameStreamPublisher:
    def __init__(
        self,
        *,
        base_url: str,
        connect: Callable[..., Any] = websockets.connect,
    ) -> None:
        self.url = cloud_websocket_url(base_url)
        self.connect = connect
        self.pending_frame: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

    def submit(self, frame: bytes) -> None:
        if self.pending_frame.full():
            try:
                self.pending_frame.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.pending_frame.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    async def run(self) -> None:
        retry_delay = 0.5
        while True:
            try:
                async with self.connect(
                    self.url,
                    compression=None,
                    max_size=8 * 1024 * 1024,
                    open_timeout=5,
                    ping_interval=15,
                    ping_timeout=10,
                ) as websocket:
                    retry_delay = 0.5
                    while True:
                        await websocket.send(await self.pending_frame.get())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.warning("EC2 frame WebSocket unavailable: %s", connection_error_summary(error))
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10.0)
