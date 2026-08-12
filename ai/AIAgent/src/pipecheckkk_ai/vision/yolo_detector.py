"""YOLO segmentation detector for defect detection."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

LOGGER = logging.getLogger(__name__)


class Detection(BaseModel):
    """Single YOLO detection result."""

    model_config = ConfigDict(extra="forbid")

    label: str
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2]
    mask: list[list[float]] | None = None


class YoloStatus(BaseModel):
    """Current YOLO detector status."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    loaded: bool
    message: str


class YoloDetector:
    """YOLO segmentation detector for pipe defects."""

    def __init__(
        self,
        model_path: str,
        class_names: dict[int, str] | None = None,
    ) -> None:
        """
        Initialize detector.

        Args:
            model_path: Path to .pt weights file
            class_names: Mapping {class_id: label}, e.g. {0: "damage", 1: "defect"}
        """
        self.model_path = Path(model_path)
        self.class_names = class_names or {0: "damage", 1: "defect"}
        self._model: Any = None
        self.last_frame_width: int | None = None
        self.last_frame_height: int | None = None

    def get_status(self) -> YoloStatus:
        """Get truthful detector status."""
        available = self.model_path.exists()
        loaded = self._model is not None

        if not available:
            return YoloStatus(
                available=False,
                loaded=False,
                message=f"Model not found at {self.model_path}",
            )

        if loaded:
            return YoloStatus(
                available=True,
                loaded=True,
                message="Model loaded and ready",
            )

        return YoloStatus(
            available=True,
            loaded=False,
            message="Model available but not yet loaded",
        )

    def load(self) -> None:
        """Load model weights from disk."""
        if self._model is not None:
            return

        try:
            # Import YOLO lazily to avoid requiring ultralytics if not used
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path))
            LOGGER.info(f"Loaded YOLO model from {self.model_path}")
        except ImportError:
            raise RuntimeError("ultralytics not installed")
        except Exception as error:
            raise RuntimeError(f"Failed to load YOLO model: {error}") from error

    def infer(self, image_path: str) -> list[Detection]:
        """
        Run inference on image.

        Args:
            image_path: Local path to image file

        Returns:
            List of detections with label, confidence, bbox
        """
        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first")

        try:
            results = self._model.predict(image_path, verbose=False)
            detections: list[Detection] = []

            if not results:
                return detections

            result = results[0]
            original_shape = getattr(result, "orig_shape", None)
            if original_shape is not None and len(original_shape) >= 2:
                self.last_frame_height = int(original_shape[0])
                self.last_frame_width = int(original_shape[1])
            if not hasattr(result, "boxes"):
                return detections

            boxes = result.boxes
            confidences = boxes.conf if hasattr(boxes, "conf") else []
            classes = boxes.cls if hasattr(boxes, "cls") else []
            xys = boxes.xyxy if hasattr(boxes, "xyxy") else []
            result_masks = getattr(result, "masks", None)
            mask_polygons = list(getattr(result_masks, "xy", []) or []) if result_masks is not None else []

            for index, (conf, cls_id, xy) in enumerate(zip(confidences, classes, xys)):
                class_id = int(cls_id.item()) if hasattr(cls_id, "item") else int(cls_id)
                label = self.class_names.get(class_id, f"class_{class_id}")
                confidence = float(conf.item()) if hasattr(conf, "item") else float(conf)
                bbox = [float(x) for x in xy]
                mask = None
                if index < len(mask_polygons):
                    points = mask_polygons[index]
                    if hasattr(points, "tolist"):
                        points = points.tolist()
                    mask = [[float(point[0]), float(point[1])] for point in points]

                detections.append(
                    Detection(
                        label=label,
                        confidence=confidence,
                        bbox=bbox,
                        mask=mask,
                    )
                )

            return detections

        except Exception as error:
            LOGGER.error(f"YOLO inference failed: {error}")
            raise RuntimeError(f"Inference failed: {error}") from error

    def infer_to_temp(
        self, frame_bytes: bytes, frame_id: str
    ) -> list[Detection]:
        """
        Run inference on frame bytes written to temp file.

        Args:
            frame_bytes: Raw image bytes
            frame_id: Frame identifier for temp file naming

        Returns:
            List of detections
        """
        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first")

        # Write frame to temp file for YOLO
        with tempfile.NamedTemporaryFile(
            suffix=".jpg",
            prefix=f"yolo-{frame_id}-",
            delete=False,
        ) as tmp:
            tmp.write(frame_bytes)
            tmp_path = tmp.name

        try:
            # Infer on temp path
            return self.infer(tmp_path)
        finally:
            # Clean up temp file
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception as error:
                LOGGER.warning(f"Failed to clean up temp file: {error}")
