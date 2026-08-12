from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

MAX_STREAM_FRAME_BYTES = 8 * 1024 * 1024
MAX_STREAM_VIEWERS = 16
ALLOWED_VIEWER_ORIGINS = {"https://i15c102.p.ssafy.io"}


class ViewerCapacityError(RuntimeError):
    pass


class LatestFrameHub:
    def __init__(self, max_subscribers: int = MAX_STREAM_VIEWERS) -> None:
        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._lock = asyncio.Lock()
        self._max_subscribers = max_subscribers

    async def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        async with self._lock:
            if len(self._subscribers) >= self._max_subscribers:
                raise ViewerCapacityError("frame stream viewer capacity reached")
            self._subscribers.add(queue)
        return queue

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, frame: bytes) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass


def valid_jpeg_frame(frame: bytes) -> bool:
    return (
        4 <= len(frame) <= MAX_STREAM_FRAME_BYTES
        and frame.startswith(b"\xff\xd8")
        and frame.endswith(b"\xff\xd9")
    )


router = APIRouter(prefix="/ws/agent", tags=["agent-frame-stream"])
frame_hub = LatestFrameHub()


@router.websocket("/frame-stream/publish")
async def publish_frame_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            frame = message.get("bytes")
            if not isinstance(frame, bytes):
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                return
            if not valid_jpeg_frame(frame):
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                return
            await frame_hub.publish(frame)
    except WebSocketDisconnect:
        return


@router.websocket("/frame-stream")
async def view_frame_stream(websocket: WebSocket) -> None:
    if websocket.headers.get("origin") not in ALLOWED_VIEWER_ORIGINS:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        queue = await frame_hub.subscribe()
    except ViewerCapacityError:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return
    receive_task = asyncio.create_task(websocket.receive())
    frame_task = asyncio.create_task(queue.get())
    try:
        while True:
            completed, _ = await asyncio.wait(
                {receive_task, frame_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in completed:
                message = receive_task.result()
                if message.get("type") == "websocket.disconnect":
                    return
                receive_task = asyncio.create_task(websocket.receive())
            if frame_task in completed:
                await websocket.send_bytes(frame_task.result())
                frame_task = asyncio.create_task(queue.get())
    except WebSocketDisconnect:
        return
    finally:
        receive_task.cancel()
        frame_task.cancel()
        await asyncio.gather(receive_task, frame_task, return_exceptions=True)
        await frame_hub.unsubscribe(queue)
