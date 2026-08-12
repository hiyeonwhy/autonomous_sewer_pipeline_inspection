"""Non-blocking outbound publishing from the laptop Agent to the EC2 dashboard API."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from io import BytesIO
from urllib.request import Request, urlopen

from PIL import Image

Transport = Callable[[str, str, bytes, str, dict[str, str]], bytes | None]


def _http_transport(
    method: str,
    url: str,
    body: bytes,
    content_type: str,
    headers: dict[str, str],
) -> bytes:
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": content_type, **headers},
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - configured project EC2 URL
        if not 200 <= response.status < 300:
            raise RuntimeError(f"cloud publish failed: HTTP {response.status}")
        return response.read()


class CloudTelemetryPublisher:
    def __init__(
        self,
        *,
        base_url: str,
        publish_key: str,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("cloud API URL must use HTTP or HTTPS")
        if not publish_key:
            raise ValueError("cloud Agent publish key is required")
        self._headers = {"X-Agent-Publish-Key": publish_key}
        self._transport = transport or _http_transport
        self._pending_detection_captures: dict[tuple[int, str, str], str] = {}

    async def _send_json(self, method: str, path: str, payload: dict[str, object]) -> bytes | None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return await asyncio.to_thread(
            self._transport,
            method,
            f"{self.base_url}{path}",
            body,
            "application/json",
            self._headers,
        )

    async def upload_telemetry(self, payload: dict[str, object]) -> None:
        await self._send_json("PUT", "/api/agent/telemetry", payload)

    async def upload_live_frame(self, frame: bytes) -> None:
        await asyncio.to_thread(
            self._transport,
            "PUT",
            f"{self.base_url}/api/agent/live-frame",
            frame,
            "image/jpeg",
            dict(self._headers),
        )

    async def upload_frame(
        self,
        frame: bytes,
        *,
        session_id: int,
        operation_id: str,
    ) -> None:
        headers = dict(self._headers)
        if session_id <= 0 or not operation_id:
            raise ValueError("frame upload requires an active inspection identity")
        headers["X-Inspection-Session-ID"] = str(session_id)
        headers["X-Inspection-Operation-ID"] = operation_id
        await asyncio.to_thread(
            self._transport,
            "PUT",
            f"{self.base_url}/api/agent/yolo-frame",
            frame,
            "image/jpeg",
            headers,
        )

    async def upload_detections(
        self,
        frame: bytes,
        *,
        session_id: int,
        operation_id: str,
        event_id: str,
        detections: list[dict[str, object]],
        approx_position_m: float | None = None,
    ) -> None:
        normalized: list[dict[str, object]] = []
        known_types = {"BLOCKAGE", "CRACK", "SURFACE_DAMAGE", "DEBRIS", "ROOT", "OTHER"}
        yolo_type_aliases = {"DEFECT": "CRACK", "DAMAGE": "SURFACE_DAMAGE"}
        for detection in detections:
            bbox = detection.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = (float(str(value)) for value in bbox)
            if x2 <= x1 or y2 <= y1:
                continue
            label = str(detection.get("label") or "OTHER").upper().replace(" ", "_").replace("-", "_")
            defect_type = yolo_type_aliases.get(label, label)
            if defect_type not in known_types:
                defect_type = "OTHER"
            confidence = min(1.0, max(0.0, float(str(detection.get("confidence") or 0.0))))
            normalized.append(
                {
                    "defect_type": defect_type,
                    "confidence": confidence,
                    "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                }
            )
        if not normalized:
            return

        pending_key = (session_id, operation_id, event_id)
        snapshot_path = self._pending_detection_captures.get(pending_key)
        if snapshot_path is None:
            boundary = "----PipeCheckkkAgentFrame"
            multipart = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="yolo-frame.jpg"\r\n'
                "Content-Type: image/jpeg\r\n\r\n"
            ).encode("ascii") + frame + f"\r\n--{boundary}--\r\n".encode("ascii")
            capture_response = await asyncio.to_thread(
                self._transport,
                "POST",
                f"{self.base_url}/api/media/captures",
                multipart,
                f"multipart/form-data; boundary={boundary}",
                dict(self._headers),
            )
            if not capture_response:
                raise RuntimeError("cloud capture upload returned an empty response")
            snapshot_path = str(json.loads(capture_response)["snapshot_path"])
            self._pending_detection_captures[pending_key] = snapshot_path
        payload: dict[str, object] = {
            "operation_id": operation_id,
            "event_id": event_id,
            "snapshot_path": snapshot_path,
            "detections": normalized,
        }
        if approx_position_m is not None:
            payload["approx_position_m"] = approx_position_m
        await self._send_json(
            "POST",
            f"/api/sessions/{session_id}/detections",
            payload,
        )
        self._pending_detection_captures.pop(pending_key, None)

    async def upload_blockage(
        self,
        frame: bytes,
        *,
        session_id: int,
        operation_id: str,
        event_id: str,
    ) -> None:
        """Best-effort caller API for an operator-confirmed full-frame blockage."""
        with Image.open(BytesIO(frame)) as image:
            width, height = image.size
        await self.upload_detections(
            frame,
            session_id=session_id,
            operation_id=operation_id,
            event_id=event_id,
            detections=[
                {
                    "label": "BLOCKAGE",
                    "confidence": 0.87,
                    "bbox": [0, 0, width, height],
                }
            ],
        )

    async def upload_log(self, session_id: int, payload: dict[str, object]) -> None:
        await self._send_json(
            "POST",
            f"/api/sessions/{session_id}/drive-logs",
            payload,
        )
