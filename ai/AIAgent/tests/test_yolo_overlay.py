from io import BytesIO

from PIL import Image

from pipecheckkk_ai.vision.yolo_detector import Detection
from pipecheckkk_ai.vision.yolo_overlay import (
    color_for_yolo_label,
    render_yolo_overlay,
)


def test_yolo_defect_colors_are_fixed_by_defect_type() -> None:
    crack = (239, 68, 68)
    surface_damage = (34, 197, 94)

    assert color_for_yolo_label("defect") == crack
    assert color_for_yolo_label("crack") == crack
    assert color_for_yolo_label("CRACK") == crack
    assert color_for_yolo_label("균열") == crack

    assert color_for_yolo_label("damage") == surface_damage
    assert color_for_yolo_label("surface_damage") == surface_damage
    assert color_for_yolo_label("SURFACE_DAMAGE") == surface_damage
    assert color_for_yolo_label("표면손상") == surface_damage

    assert crack != surface_damage


def test_detection_without_mask_has_fill_but_no_outline_or_text() -> None:
    source = BytesIO()
    Image.new("RGB", (64, 64), "white").save(source, format="JPEG")
    rendered_bytes = render_yolo_overlay(
        source.getvalue(),
        [
            Detection(
                label="damage",
                confidence=0.91,
                bbox=[16, 16, 48, 48],
                mask=None,
            )
        ],
    )
    rendered = Image.open(BytesIO(rendered_bytes)).convert("RGB")
    center = rendered.getpixel((32, 32))
    outside = rendered.getpixel((8, 8))

    assert isinstance(center, tuple)
    assert center[1] > center[0]
    assert isinstance(outside, tuple)
    assert sum(outside) > 740
