from __future__ import annotations

import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from pipecheckkk_ai.cloud_publisher import CloudTelemetryPublisher


def test_blockage_upload_uses_original_frame_and_existing_detection_path() -> None:
    calls: list[tuple[str, str, bytes, str, dict[str, str]]] = []

    def transport(method, url, body, content_type, headers):
        calls.append((method, url, body, content_type, headers))
        if url.endswith("/api/media/captures"):
            return b'{"snapshot_path":"/media/captures/blockage.jpg"}'
        return None

    source = BytesIO()
    Image.new("RGB", (64, 48), "white").save(source, format="JPEG")
    original = source.getvalue()
    publisher = CloudTelemetryPublisher(
        base_url="https://i15c102.p.ssafy.io",
        publish_key="test-publish-key",
        transport=transport,
    )

    asyncio.run(
        publisher.upload_blockage(
            original,
            session_id=185,
            operation_id="operation-185",
            event_id="operator-blockage-step20-operation-185",
        )
    )

    assert original in calls[0][2]
    payload = json.loads(calls[1][2])
    assert calls[1][1].endswith("/api/sessions/185/detections")
    assert payload["detections"] == [
        {
            "defect_type": "BLOCKAGE",
            "confidence": 0.87,
            "bbox": {"x1": 0.0, "y1": 0.0, "x2": 64.0, "y2": 48.0},
        }
    ]


def test_detection_retry_reuses_the_uploaded_source_frame() -> None:
    calls: list[str] = []
    detection_attempts = 0

    def transport(
        _method: str,
        url: str,
        _body: bytes,
        _content_type: str,
        _headers: dict[str, str],
    ) -> bytes | None:
        nonlocal detection_attempts
        calls.append(url)
        if url.endswith('/api/media/captures'):
            return b'{"snapshot_path":"/media/defects/pending.jpg"}'
        if url.endswith('/api/sessions/17/detections'):
            detection_attempts += 1
            if detection_attempts == 1:
                raise RuntimeError('temporary detection write failure')
        return None

    publisher = CloudTelemetryPublisher(
        base_url="https://i15c102.p.ssafy.io",
        publish_key="test-publish-key",
        transport=transport,
    )
    kwargs = {
        "session_id": 17,
        "operation_id": "operation-17",
        "event_id": "yolo-pending",
        "detections": [{"label": "defect", "confidence": 0.8, "bbox": [1, 2, 10, 20]}],
    }
    with pytest.raises(RuntimeError, match='temporary detection write failure'):
        asyncio.run(publisher.upload_detections(b"jpeg", **kwargs))
    asyncio.run(publisher.upload_detections(b"jpeg", **kwargs))

    assert sum(url.endswith('/api/media/captures') for url in calls) == 1
    assert sum(url.endswith('/api/sessions/17/detections') for url in calls) == 2


def test_cloud_publisher_uploads_yolo_detections_as_cloud_defects() -> None:
    calls: list[tuple[str, str, bytes, str, dict[str, str]]] = []

    def transport(
        method: str,
        url: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
    ) -> bytes | None:
        calls.append((method, url, body, content_type, headers))
        if url.endswith('/api/media/captures'):
            return b'{"snapshot_path":"/media/defects/source.jpg"}'
        return None

    publisher = CloudTelemetryPublisher(
        base_url="https://i15c102.p.ssafy.io",
        publish_key="test-publish-key",
        transport=transport,
    )
    asyncio.run(publisher.upload_detections(
        b"\xff\xd8source\xff\xd9",
        session_id=17,
        operation_id="operation-17",
        event_id="yolo-abc123",
        detections=[
            {"label": "crack", "confidence": 0.91, "bbox": [10, 20, 50, 70], "mask": [[1, 2]]},
            {"label": "class_1", "confidence": 0.72, "bbox": [100, 120, 180, 210]},
        ],
        approx_position_m=1.25,
    ))

    assert [call[1] for call in calls] == [
        "https://i15c102.p.ssafy.io/api/media/captures",
        "https://i15c102.p.ssafy.io/api/sessions/17/detections",
    ]
    assert calls[0][3].startswith("multipart/form-data; boundary=")
    payload = json.loads(calls[1][2])
    assert payload["snapshot_path"] == "/media/defects/source.jpg"
    assert payload["operation_id"] == "operation-17"
    assert payload["event_id"] == "yolo-abc123"
    assert payload["approx_position_m"] == 1.25
    assert payload["detections"] == [
        {"defect_type": "CRACK", "confidence": 0.91, "bbox": {"x1": 10.0, "y1": 20.0, "x2": 50.0, "y2": 70.0}},
        {"defect_type": "OTHER", "confidence": 0.72, "bbox": {"x1": 100.0, "y1": 120.0, "x2": 180.0, "y2": 210.0}},
    ]


def test_cloud_publisher_sends_state_frame_and_session_log_without_control_commands() -> None:
    calls: list[tuple[str, str, bytes, str, dict[str, str]]] = []

    def transport(
        method: str,
        url: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
    ) -> None:
        calls.append((method, url, body, content_type, headers))

    publisher = CloudTelemetryPublisher(
        base_url="https://i15c102.p.ssafy.io",
        publish_key="test-publish-key",
        transport=transport,
    )

    asyncio.run(publisher.upload_telemetry({"agent_id": "laptop-4070", "hermes": {"state": "RUNNING"}}))
    asyncio.run(
        publisher.upload_frame(
            b"\xff\xd8frame\xff\xd9",
            session_id=17,
            operation_id="operation-17",
        )
    )
    asyncio.run(
        publisher.upload_log(
            17,
            {
                "command_source": "Hermes VL",
                "agent_state": "RUNNING",
                "note": "이미지 분석 전달",
            },
        )
    )

    assert [call[1] for call in calls] == [
        "https://i15c102.p.ssafy.io/api/agent/telemetry",
        "https://i15c102.p.ssafy.io/api/agent/yolo-frame",
        "https://i15c102.p.ssafy.io/api/sessions/17/drive-logs",
    ]
    assert all("command" not in call[1] for call in calls)
    assert json.loads(calls[0][2])["hermes"]["state"] == "RUNNING"
    assert calls[1][3] == "image/jpeg"
    assert calls[1][4]["X-Inspection-Session-ID"] == "17"
    assert calls[1][4]["X-Inspection-Operation-ID"] == "operation-17"
    assert all(call[4]["X-Agent-Publish-Key"] == "test-publish-key" for call in calls)
