from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.core import agent_auth
from app.domain.agent_telemetry import frame_stream
from app.domain.agent_telemetry.frame_stream import (
    LatestFrameHub,
    ViewerCapacityError,
    valid_jpeg_frame,
)


def test_latest_frame_hub_drops_queued_stale_frame() -> None:
    async def scenario() -> bytes:
        hub = LatestFrameHub()
        queue = await hub.subscribe()
        await hub.publish(b"\xff\xd8old\xff\xd9")
        await hub.publish(b"\xff\xd8latest\xff\xd9")
        return queue.get_nowait()

    assert asyncio.run(scenario()) == b"\xff\xd8latest\xff\xd9"


def test_latest_frame_hub_fans_out_without_unbounded_queue() -> None:
    async def scenario() -> tuple[bytes, bytes, int, int]:
        hub = LatestFrameHub()
        first = await hub.subscribe()
        second = await hub.subscribe()
        await hub.publish(b"\xff\xd8frame\xff\xd9")
        return first.get_nowait(), second.get_nowait(), first.maxsize, second.maxsize

    assert asyncio.run(scenario()) == (
        b"\xff\xd8frame\xff\xd9",
        b"\xff\xd8frame\xff\xd9",
        1,
        1,
    )


def test_latest_frame_hub_rejects_viewers_above_fixed_capacity() -> None:
    async def scenario() -> None:
        hub = LatestFrameHub(max_subscribers=2)
        await hub.subscribe()
        await hub.subscribe()
        with pytest.raises(ViewerCapacityError):
            await hub.subscribe()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("frame", "valid"),
    [
        (b"\xff\xd8jpeg\xff\xd9", True),
        (b"not-jpeg", False),
        (b"\xff\xd8" + b"x" * (8 * 1024 * 1024) + b"\xff\xd9", False),
    ],
)
def test_stream_accepts_only_bounded_jpeg(frame: bytes, valid: bool) -> None:
    assert valid_jpeg_frame(frame) is valid


def test_websocket_relay_accepts_keyless_publisher_and_forwards_binary() -> None:
    class PublisherSocket:
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self.accepted = False
            self.received = 0

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, *, code: int) -> None:
            raise AssertionError(f"unexpected close {code}")

        async def receive(self) -> dict[str, object]:
            if self.received:
                return {"type": "websocket.disconnect", "code": 1000}
            self.received += 1
            return {"type": "websocket.receive", "bytes": b"\xff\xd8live\xff\xd9"}

    async def scenario() -> tuple[bool, bytes]:
        viewer_queue = await frame_stream.frame_hub.subscribe()
        publisher = PublisherSocket()
        try:
            await frame_stream.publish_frame_stream(cast(Any, publisher))
            return publisher.accepted, viewer_queue.get_nowait()
        finally:
            await frame_stream.frame_hub.unsubscribe(viewer_queue)

    assert asyncio.run(scenario()) == (True, b"\xff\xd8live\xff\xd9")


def test_http_agent_auth_allows_configured_agent_when_server_key_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_auth,
        "get_settings",
        lambda: type("Settings", (), {"agent_publish_key": ""})(),
    )
    assert agent_auth.agent_publish_key_is_valid("any-non-empty-value") is True
    assert agent_auth.agent_publish_key_is_valid("") is False


def test_publisher_text_message_closes_with_unsupported_data() -> None:
    class TextPublisherSocket:
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self.closed_code: int | None = None

        async def accept(self) -> None:
            return None

        async def receive(self) -> dict[str, object]:
            return {"type": "websocket.receive", "text": "not-binary"}

        async def close(self, *, code: int) -> None:
            self.closed_code = code

    async def scenario() -> int | None:
        websocket = TextPublisherSocket()
        await frame_stream.publish_frame_stream(cast(Any, websocket))
        return websocket.closed_code

    assert asyncio.run(scenario()) == 1003


def test_viewer_disconnect_is_removed_without_waiting_for_another_frame() -> None:
    class ViewerSocket:
        headers = {"origin": "https://i15c102.p.ssafy.io"}

        def __init__(self) -> None:
            self.accepted = False

        async def accept(self) -> None:
            self.accepted = True

        async def receive(self) -> dict[str, object]:
            return {"type": "websocket.disconnect", "code": 1000}

        async def send_bytes(self, _frame: bytes) -> None:
            raise AssertionError("disconnected viewer must not receive a frame")

    async def scenario() -> tuple[bool, int, int]:
        before = frame_stream.frame_hub.subscriber_count
        viewer = ViewerSocket()
        await frame_stream.view_frame_stream(cast(Any, viewer))
        return viewer.accepted, before, frame_stream.frame_hub.subscriber_count

    accepted, before, after = asyncio.run(scenario())
    assert accepted is True
    assert after == before


def test_viewer_rejects_non_dashboard_origin() -> None:
    class CrossOriginViewer:
        headers = {"origin": "https://attacker.example"}

        def __init__(self) -> None:
            self.closed_code: int | None = None

        async def close(self, *, code: int) -> None:
            self.closed_code = code

    async def scenario() -> int | None:
        viewer = CrossOriginViewer()
        await frame_stream.view_frame_stream(cast(Any, viewer))
        return viewer.closed_code

    assert asyncio.run(scenario()) == 1008
