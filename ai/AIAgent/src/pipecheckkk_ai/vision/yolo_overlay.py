"""Render read-only YOLO detections on a camera JPEG for the existing dashboard."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from pipecheckkk_ai.vision.yolo_detector import Detection


CRACK_COLOR = (239, 68, 68)
SURFACE_DAMAGE_COLOR = (34, 197, 94)
UNKNOWN_DEFECT_COLOR = (59, 130, 246)

_CRACK_LABELS = {"defect", "crack", "균열"}
_SURFACE_DAMAGE_LABELS = {"damage", "surface_damage", "표면손상"}


def color_for_yolo_label(label: str) -> tuple[int, int, int]:
    """Return one stable dashboard color for each supported defect type."""
    normalized = label.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in _CRACK_LABELS:
        return CRACK_COLOR
    if normalized in _SURFACE_DAMAGE_LABELS:
        return SURFACE_DAMAGE_COLOR
    return UNKNOWN_DEFECT_COLOR


def render_yolo_overlay(frame_bytes: bytes, detections: list[Detection]) -> bytes:
    """Return a JPEG with defect polygons colored by stable defect type."""
    image = Image.open(BytesIO(frame_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for detection in detections:
        red, green, blue = color_for_yolo_label(detection.label)
        if detection.mask and len(detection.mask) >= 3:
            polygon = [(float(point[0]), float(point[1])) for point in detection.mask]
            draw.polygon(polygon, fill=(red, green, blue, 82))
        else:
            x1, y1, x2, y2 = detection.bbox
            draw.rectangle(
                (float(x1), float(y1), float(x2), float(y2)),
                fill=(red, green, blue, 82),
            )

    rendered = Image.alpha_composite(image, overlay).convert("RGB")
    output = BytesIO()
    rendered.save(output, format="JPEG", quality=88)
    return output.getvalue()
