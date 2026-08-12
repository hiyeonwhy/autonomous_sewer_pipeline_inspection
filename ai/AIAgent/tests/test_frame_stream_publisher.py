from __future__ import annotations

import asyncio

from pipecheckkk_ai.frame_stream_publisher import (
    CloudFrameStreamPublisher,
    cloud_websocket_url,
    connection_error_summary,
)


def test_cloud_websocket_url_uses_secure_scheme_for_https() -> None:
    assert cloud_websocket_url("https://i15c102.p.ssafy.io") == (
        "wss://i15c102.p.ssafy.io/ws/agent/frame-stream/publish"
    )
    assert cloud_websocket_url("http://127.0.0.1:8001") == (
        "ws://127.0.0.1:8001/ws/agent/frame-stream/publish"
    )


def test_connection_error_summary_reports_only_safe_http_status() -> None:
    class Response:
        status_code = 403
        body = "must-not-leak"

    class InvalidStatus(Exception):
        response = Response()

    summary = connection_error_summary(InvalidStatus("secret-bearing detail"))

    assert summary == "InvalidStatus(status=403)"
    assert "secret" not in summary
    assert "must-not-leak" not in summary


def test_submit_keeps_only_latest_frame() -> None:
    async def scenario() -> bytes:
        publisher = CloudFrameStreamPublisher(
            base_url="https://i15c102.p.ssafy.io",
        )
        publisher.submit(b"old")
        publisher.submit(b"latest")
        return publisher.pending_frame.get_nowait()

    assert asyncio.run(scenario()) == b"latest"


def test_run_sends_binary_frame_without_publish_header() -> None:
    async def scenario() -> tuple[list[bytes], dict[str, str]]:
        sent: list[bytes] = []
        observed_headers: dict[str, str] = {}
        sent_event = asyncio.Event()

        class Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def send(self, frame: bytes) -> None:
                sent.append(frame)
                sent_event.set()

        def connect(_url: str, **kwargs):
            observed_headers.update(kwargs.get("additional_headers", {}))
            return Connection()

        publisher = CloudFrameStreamPublisher(
            base_url="https://i15c102.p.ssafy.io",
            connect=connect,
        )
        publisher.submit(b"jpeg")
        task = asyncio.create_task(publisher.run())
        await asyncio.wait_for(sent_event.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return sent, observed_headers

    sent, headers = asyncio.run(scenario())
    assert sent == [b"jpeg"]
    assert headers == {}
