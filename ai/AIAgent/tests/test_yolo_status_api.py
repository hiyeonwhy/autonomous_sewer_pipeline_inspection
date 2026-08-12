from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi.testclient import TestClient
from PIL import Image

from pipecheckkk_ai.api import create_app
from pipecheckkk_ai.vision.yolo_detector import Detection, YoloDetector
from pipecheckkk_ai.yolo_history import YoloCaptureStore


class FakeYoloDetector:
    def __init__(self) -> None:
        self.loaded = False
        self.frames: list[bytes] = []
        self.last_frame_width = 640
        self.last_frame_height = 480

    def get_status(self):
        return SimpleNamespace(available=True, loaded=self.loaded, message="ready")

    def load(self) -> None:
        self.loaded = True

    def infer_to_temp(self, frame_bytes: bytes, frame_id: str):
        assert frame_id
        self.frames.append(frame_bytes)
        return [Detection(label="defect", confidence=0.91, bbox=[1, 2, 3, 4])]


def test_default_yolo_labels_match_the_loaded_pipe_model(tmp_path: Path) -> None:
    app = create_app(
        snapshot_fetcher=lambda _url: b"frame",
        yolo_capture_store=YoloCaptureStore(tmp_path),
    )
    assert app.state.yolo_detector.class_names == {0: "damage", 1: "defect"}


def test_yolo_status_analyzes_latest_pi_frame(tmp_path: Path) -> None:
    detector = FakeYoloDetector()
    fetched: list[str] = []

    def snapshot_fetcher(url: str) -> bytes:
        fetched.append(url)
        return b"fresh-jpeg"

    with TestClient(
        create_app(
            snapshot_fetcher=snapshot_fetcher,
            yolo_detector=cast(YoloDetector, detector),
            yolo_capture_store=YoloCaptureStore(tmp_path, min_interval_s=0),
        )
    ) as client:
        response = client.get("/api/yolo/status")
        history_response = client.get("/api/yolo/history")
        history = history_response.json()["items"]
        capture_response = client.get(history[0]["snapshotUrl"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["loaded"] is True
    assert payload["detections"] == [
        {"label": "defect", "confidence": 0.91, "bbox": [1.0, 2.0, 3.0, 4.0], "mask": None}
    ]
    assert fetched == ["http://192.168.100.246:8081/snapshot.jpg"]
    assert detector.frames == [b"fresh-jpeg"]
    assert history_response.status_code == 200
    assert history[0]["frameWidth"] == 640
    assert history[0]["frameHeight"] == 480
    assert history[0]["detections"][0]["label"] == "defect"
    assert history[0]["detections"][0]["confidence"] == 0.91
    assert capture_response.status_code == 200
    assert capture_response.content == b"fresh-jpeg"


def test_yolo_frame_draws_only_colored_defect_mask(tmp_path: Path) -> None:
    detector = FakeYoloDetector()

    def infer_with_mask(frame_bytes: bytes, frame_id: str):
        detector.frames.append(frame_bytes)
        return [
            Detection(
                label="crack",
                confidence=0.93,
                bbox=[10, 10, 50, 50],
                mask=[[15, 15], [45, 15], [45, 45], [15, 45]],
            )
        ]

    detector.infer_to_temp = infer_with_mask  # type: ignore[method-assign]
    source = BytesIO()
    Image.new("RGB", (64, 64), "white").save(source, format="JPEG")

    with TestClient(
        create_app(
            snapshot_fetcher=lambda _url: source.getvalue(),
            yolo_detector=cast(YoloDetector, detector),
            yolo_capture_store=YoloCaptureStore(tmp_path, min_interval_s=0),
        )
    ) as client:
        response = client.get("/api/vision/yolo-frame.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    rendered = Image.open(BytesIO(response.content)).convert("RGB")
    assert rendered.getpixel((30, 30)) != (255, 255, 255)
    corner = rendered.getpixel((10, 10))
    assert isinstance(corner, tuple)
    assert sum(corner) > 700
