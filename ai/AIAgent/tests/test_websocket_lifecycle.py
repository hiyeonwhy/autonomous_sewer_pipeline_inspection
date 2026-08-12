import pytest

from pipecheckkk_ai.agent import IntegratedAgent
from pipecheckkk_ai.api import EventHub, stream_events


class DisconnectingWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)

    async def receive(self) -> dict:
        return {"type": "websocket.disconnect"}


@pytest.mark.asyncio
async def test_event_stream_unsubscribes_immediately_when_browser_disconnects() -> None:
    websocket = DisconnectingWebSocket()
    hub = EventHub()

    await stream_events(websocket, IntegratedAgent(), hub)

    assert websocket.accepted is True
    assert websocket.messages[0]["type"] == "mission.snapshot"
    assert hub.subscriber_count == 0
