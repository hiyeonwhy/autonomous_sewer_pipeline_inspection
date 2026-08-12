"""Tests for YOLO segmentation detector."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipecheckkk_ai.vision.yolo_detector import (
    Detection,
    YoloDetector,
)


class TestYoloDetectorInit:
    """Test YOLO detector initialization."""

    def test_detector_status_model_not_found(self) -> None:
        """Detector status is truthful when model not found."""
        with patch("pathlib.Path.exists", return_value=False):
            detector = YoloDetector(model_path="/nonexistent/path")
            status = detector.get_status()

            assert status.available is False
            assert status.loaded is False
            assert "not found" in status.message.lower()

    def test_detector_status_model_found_not_loaded(self) -> None:
        """Detector status shows found but not loaded."""
        with patch("pathlib.Path.exists", return_value=True):
            detector = YoloDetector(model_path="/some/path/model.pt")
            status = detector.get_status()

            assert status.available is True
            assert status.loaded is False

    @patch("ultralytics.YOLO")
    def test_detector_loads_model(self, mock_yolo_class) -> None:
        """Detector loads model on demand."""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        with patch("pathlib.Path.exists", return_value=True):
            detector = YoloDetector(model_path="/some/model.pt")

            # Model not loaded yet
            assert detector.get_status().loaded is False

            # Load model
            detector.load()

            # Now it's loaded
            assert detector.get_status().loaded is True
            mock_yolo_class.assert_called_once()


class TestYoloDetection:
    """Test YOLO detection data structure."""

    def test_detection_structure(self) -> None:
        """Detection has required fields."""
        det = Detection(
            label="damage",
            confidence=0.85,
            bbox=[10, 20, 50, 60],
            mask=None,
        )
        assert det.label == "damage"
        assert det.confidence == 0.85
        assert det.bbox == [10, 20, 50, 60]

    def test_detection_json_serialization(self) -> None:
        """Detection serializes to JSON."""
        det = Detection(
            label="defect",
            confidence=0.72,
            bbox=[100, 150, 200, 250],
        )
        data = det.model_dump(mode="json")
        assert data["label"] == "defect"
        assert data["confidence"] == 0.72


class TestYoloInference:
    """Test YOLO inference."""

    @patch("ultralytics.YOLO")
    def test_inference_on_real_frame(self, mock_yolo_class) -> None:
        """Detector runs inference and returns detections."""
        # Mock the model
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        # Mock the results
        mock_result = MagicMock()
        mock_boxes = MagicMock()
        mock_boxes.conf = [0.85, 0.72]
        mock_boxes.cls = [0, 1]  # damage, defect
        mock_boxes.xyxy = [[10, 20, 50, 60], [100, 150, 200, 250]]
        mock_result.boxes = mock_boxes
        mock_result.masks = None
        mock_model.predict.return_value = [mock_result]

        # Create temp image
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jpg", delete=False) as f:
            temp_path = f.name
            f.write("fake image")

        try:
            with patch("pathlib.Path.exists", return_value=True):
                detector = YoloDetector(
                    model_path="/some/model.pt",
                    class_names={0: "damage", 1: "defect"},
                )
                detector.load()

                detections = detector.infer(temp_path)

                assert len(detections) == 2
                assert detections[0].label == "damage"
                assert detections[0].confidence == pytest.approx(0.85)
                assert detections[1].label == "defect"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @patch("ultralytics.YOLO")
    def test_inference_preserves_segmentation_polygon(self, mock_yolo_class) -> None:
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model
        mock_result = MagicMock()
        mock_result.orig_shape = (480, 640)
        mock_result.boxes.conf = [0.9]
        mock_result.boxes.cls = [0]
        mock_result.boxes.xyxy = [[10, 20, 50, 60]]
        mock_result.masks.xy = [[[12, 22], [48, 22], [48, 58], [12, 58]]]
        mock_model.predict.return_value = [mock_result]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jpg", delete=False) as file:
            temp_path = file.name
            file.write("fake image")

        try:
            with patch("pathlib.Path.exists", return_value=True):
                detector = YoloDetector(
                    model_path="/some/model.pt",
                    class_names={0: "damage"},
                )
                detector.load()
                detections = detector.infer(temp_path)

            assert detections[0].mask == [
                [12.0, 22.0],
                [48.0, 22.0],
                [48.0, 58.0],
                [12.0, 58.0],
            ]
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @patch("ultralytics.YOLO")
    def test_inference_when_not_loaded_fails(self, mock_yolo_class) -> None:
        """Inference fails when model not loaded."""
        with patch("pathlib.Path.exists", return_value=True):
            detector = YoloDetector(model_path="/some/model.pt")

            # Try to infer without loading
            with pytest.raises(RuntimeError, match="not loaded"):
                detector.infer("/some/image.jpg")

    @patch("ultralytics.YOLO")
    def test_inference_no_detections(self, mock_yolo_class) -> None:
        """Inference returns empty list when no detections."""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        mock_result = MagicMock()
        mock_boxes = MagicMock()
        mock_boxes.conf = []
        mock_boxes.cls = []
        mock_boxes.xyxy = []
        mock_result.boxes = mock_boxes
        mock_result.masks = None
        mock_model.predict.return_value = [mock_result]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jpg", delete=False) as f:
            temp_path = f.name
            f.write("fake image")

        try:
            with patch("pathlib.Path.exists", return_value=True):
                detector = YoloDetector(model_path="/some/model.pt")
                detector.load()

                detections = detector.infer(temp_path)
                assert detections == []
        finally:
            Path(temp_path).unlink(missing_ok=True)
