"""결함별 사진 — 다른 결함이 안 보이는 선에서 가장 넓은 영역만 남긴다.

탐지 모델이 한 프레임에서 결함 여러 개를 찾으면, 결함마다 그 결함만 담긴 사진이
있어야 한다. 바운딩 박스만 남기면 주변 맥락(관 벽면, 물 흐름, 결함의 연장 방향)이
사라져 판독 품질이 떨어지므로, **다른 결함 박스에 닿기 직전까지** 사방으로 넓힌다.

영역 계산과 사진 생성이 나뉘어 있다. `expand_without_others` 가 남길 영역을 정하고,
`render_defect_photo` 가 그 **밖을 검게 덮는다** — 잘라내지 않는다. 이유는
`render_defect_photo` 주석에 있다.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# 크롭 저장 형식. 판독에 넘길 때 다시 축소되므로 여기서는 화질을 넉넉히 남긴다.
CROP_FORMAT = "JPEG"
CROP_QUALITY = 90

# 막는 결함에서 물러날 여유(픽셀). 이미지 짧은 변의 3%를 쓰되 최소 24px.
#
# 좌표상 겹치지 않게만 자르면 **렌더된 주석이 새어 들어온다.** 탐지 결과가 그려진
# 프레임에서는 라벨 글자가 박스 **위쪽**에, 테두리가 박스 경계 바깥으로 그려지기
# 때문이다. 실측에서 크롭 세 장 모두에 옆 결함의 라벨이 걸렸다.
MIN_OBSTACLE_MARGIN_PX = 24
OBSTACLE_MARGIN_RATIO = 0.03


def obstacle_margin(image_width: int, image_height: int) -> float:
    """이미지 크기에 맞춘 여유. 큰 프레임은 라벨도 크게 그려진다."""
    return max(
        float(MIN_OBSTACLE_MARGIN_PX),
        round(min(image_width, image_height) * OBSTACLE_MARGIN_RATIO),
    )


@dataclass(frozen=True)
class Box:
    """픽셀 좌표. x2 > x1, y2 > y1 을 전제한다."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    def overlaps_x(self, other: "Box") -> bool:
        return other.x1 < self.x2 and other.x2 > self.x1

    def overlaps_y(self, other: "Box") -> bool:
        return other.y1 < self.y2 and other.y2 > self.y1


def _expand_one(
    rect: Box,
    direction: str,
    others: list[Box],
    image_width: int,
    image_height: int,
    gap: float,
) -> Box:
    """한 방향으로 최대한 넓힌다. 다른 결함에 닿으면 `gap`만큼 앞에서 멈춘다.

    현재 사각형을 기준으로 막는 결함을 판정하므로, 앞선 확장으로 범위에 새로 들어온
    결함도 제대로 막는다. 확장만 하므로 `target` 포함은 자동으로 유지된다.
    """
    if direction == "left":
        limits = [
            other.x2 + gap
            for other in others
            if rect.overlaps_y(other) and other.x1 < rect.x2
        ]
        return Box(min(rect.x1, max([0.0, *limits])), rect.y1, rect.x2, rect.y2)

    if direction == "right":
        limits = [
            other.x1 - gap
            for other in others
            if rect.overlaps_y(other) and other.x2 > rect.x1
        ]
        return Box(rect.x1, rect.y1, max(rect.x2, min([float(image_width), *limits])), rect.y2)

    if direction == "top":
        limits = [
            other.y2 + gap
            for other in others
            if rect.overlaps_x(other) and other.y1 < rect.y2
        ]
        return Box(rect.x1, min(rect.y1, max([0.0, *limits])), rect.x2, rect.y2)

    limits = [
        other.y1 - gap for other in others if rect.overlaps_x(other) and other.y2 > rect.y1
    ]
    return Box(rect.x1, rect.y1, rect.x2, max(rect.y2, min([float(image_height), *limits])))


# 네 방향을 넓히는 순서. 순서가 결과를 크게 바꾼다 — 예를 들어 위아래로 먼저 넓히면
# 옆에 있는 결함을 세로 범위에서 피할 수 있어 훨씬 큰 사각형이 나온다. 전부 시도해서
# 면적이 가장 큰 것을 고른다(24가지뿐이라 비용은 무시할 수준).
_DIRECTIONS = ("left", "right", "top", "bottom")


def expand_without_others(
    target: Box,
    others: list[Box],
    *,
    image_width: int,
    image_height: int,
    margin: float | None = None,
) -> Box:
    """`target`을 품는 **면적이 가장 큰** 사각형을 찾는다. `others`와는 겹치지 않는다.

    관 전체 구조가 보이도록 최대한 넓게 잡는다. 판독 모델이 결함만 확대해서 보면
    관 벽면·물 흐름·이음부 같은 맥락을 잃는다.

    막는 결함에서는 `margin`만큼 물러난다. 좌표만 안 겹치게 자르면 렌더된 라벨 글자와
    테두리가 크롭에 남는다(`obstacle_margin` 주석 참조). 이미지 경계에는 여유를 두지
    않는다 — 거기엔 남의 주석이 없다.

    여유를 주면 대상 박스를 잘라먹는 배치도 있다. 그때는 여유를 포기하고 박스 경계까지만
    물러난다 — 대상 결함이 크롭에서 빠지는 것이 더 나쁘다.
    """
    from itertools import permutations

    gap = obstacle_margin(image_width, image_height) if margin is None else margin
    # 여유까지 부풀린 금지 영역. 이걸 건드리는 크롭에는 라벨 글자가 새어 들어온다.
    forbidden = [
        Box(other.x1 - gap, other.y1 - gap, other.x2 + gap, other.y2 + gap)
        for other in others
    ]

    best: Box | None = None
    best_key: tuple[int, float] | None = None
    for order in permutations(_DIRECTIONS):
        rect = target
        for direction in order:
            rect = _expand_one(rect, direction, others, image_width, image_height, gap)
        rect = Box(
            max(0.0, rect.x1),
            max(0.0, rect.y1),
            min(float(image_width), rect.x2),
            min(float(image_height), rect.y2),
        )
        # 방향별 확장은 그 시점의 사각형만 보므로, 확장하지 않은 변이 우연히 다른 결함
        # 가까이 남을 수 있다. 면적만 보고 고르면 그런 후보가 이긴다 — 실측에서 세로로
        # 긴 크롭(429k)이 깨끗한 가로 크롭(390k)을 이기고 옆 라벨을 물고 들어왔다.
        # 그래서 여유를 지킨 후보를 먼저 고르고, 그다음 면적을 본다.
        clean = not any(rect.overlaps_x(zone) and rect.overlaps_y(zone) for zone in forbidden)
        key = (1 if clean else 0, rect.width * rect.height)
        if best_key is None or key > best_key:
            best, best_key = rect, key

    if best is None or best.width <= 0 or best.height <= 0:
        logger.warning("크롭 확장 실패 — 바운딩 박스를 그대로 사용함")
        return target
    return best


def render_defect_photo(
    source_bytes: bytes,
    box: Box,
    output_directory: Path,
) -> tuple[str, int, int]:
    """`box` 밖을 검게 덮은 사진을 저장하고 (파일명, 폭, 높이)를 돌려준다.

    **잘라내지 않고 원본 크기를 유지한다.** 이유가 셋이다.

    * 보고서 사진 상자(67.3 × 50.5mm)는 양식이 정한 고정 크기다. 잘라낸 이미지는
      비율이 제각각이라(가로로 긴 1024×381, 세로로 긴 511×1024) 같은 상자에서
      25mm ~ 67mm 로 들쭉날쭉해진다. 원본 비율이면 항상 같은 크기로 들어간다.
    * 결함의 위치와 관 둘레 대비 크기가 보인다. 지침은 결함 위치를 시계 방향으로
      기재하도록 하는데, 잘라낸 사진에는 그 기준이 될 관 둘레가 없다.
    * 확대 효과가 거의 없다. 크롭 영역이 이미 화면의 37~77% 라, 잘라내도 결함이
      종이에서 커지는 폭이 0.1 ~ 4mm 에 그쳤다. 파일을 두 벌 유지할 이유가 못 된다.

    다른 결함은 크롭 영역 밖이므로 검은색에 함께 덮인다 — 따로 가릴 필요가 없고,
    라벨 글자가 새어 들어오는 경로도 구조적으로 사라진다.
    """
    from PIL import Image

    with Image.open(io.BytesIO(source_bytes)) as opened:
        source = opened.convert("RGB")
        area = (
            int(box.x1),
            int(box.y1),
            int(round(box.x2)),
            int(round(box.y2)),
        )
        photo = Image.new("RGB", source.size, (0, 0, 0))
        photo.paste(source.crop(area), area)

        buffer = io.BytesIO()
        photo.save(buffer, format=CROP_FORMAT, quality=CROP_QUALITY, optimize=True)
        data = buffer.getvalue()
        size = photo.size

    output_directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.jpg"
    (output_directory / filename).write_bytes(data)
    return filename, size[0], size[1]


def image_size(source_bytes: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(source_bytes)) as opened:
        return opened.size


def appearance_hash(source_bytes: bytes, box: Box) -> str:
    """결함이 찍힌 영역의 생김새를 64비트로 요약한다 (dHash).

    사진을 9×8 흑백으로 줄이고 **가로 방향 밝기 변화의 부호만** 담는다. 같은 결함을
    조금 다른 각도·밝기로 찍으면 값이 비슷하게 나오고, 다른 결함이면 크게 갈린다.
    두 값의 해밍거리로 「같은 것을 또 찍었는지」를 판단한다.

    학습 모델이 아니라 산술이다. 실측 3.4ms/건으로, 사진 판독 한 번(11.7초)의
    3천분의 1이다. 실시간 수집 경로에 넣어도 지장이 없다.
    """
    from PIL import Image

    left, top = max(0, int(box.x1)), max(0, int(box.y1))
    right, bottom = max(left + 1, int(box.x2)), max(top + 1, int(box.y2))
    with Image.open(io.BytesIO(source_bytes)) as opened:
        region = opened.convert("L").crop((left, top, right, bottom))
        small = region.resize((9, 8), Image.LANCZOS)
        pixels = list(small.getdata())

    bits = 0
    for row in range(8):
        for column in range(8):
            left_pixel = pixels[row * 9 + column]
            right_pixel = pixels[row * 9 + column + 1]
            bits = (bits << 1) | (1 if left_pixel > right_pixel else 0)
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    """두 dHash 가 몇 비트나 다른지. 작을수록 닮았다."""
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def box_iou(left: Box, right: Box) -> float:
    """두 박스가 겹치는 정도. 1이면 완전히 같은 자리다."""
    ix1, iy1 = max(left.x1, right.x1), max(left.y1, right.y1)
    ix2, iy2 = min(left.x2, right.x2), min(left.y2, right.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (
        (left.x2 - left.x1) * (left.y2 - left.y1)
        + (right.x2 - right.x1) * (right.y2 - right.y1)
        - inter
    )
    return inter / union if union > 0 else 0.0


def parse_detection_text(text: str) -> list[tuple[str, float, Box]]:
    """YOLO 결과 텍스트를 파싱한다.

        LABEL CONFIDENCE x1 y1 x2 y2

    예: `SURFACE_DAMAGE 0.9931 605 892 841 1023`

    좌표는 원본 이미지의 픽셀이다. 정규화 좌표(0~1)는 받지 않는다 — 둘을 섞으면
    조용히 잘못된 영역을 자르게 되므로, 애매하면 거부하는 편이 낫다.
    """
    parsed: list[tuple[str, float, Box]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 6:
            raise ValueError(
                f"{line_number}번째 줄 형식 오류 — "
                "'LABEL CONFIDENCE x1 y1 x2 y2' 여야 합니다: " + line
            )
        label = parts[0].upper()
        try:
            confidence = float(parts[1])
            x1, y1, x2, y2 = (float(value) for value in parts[2:])
        except ValueError as error:
            raise ValueError(f"{line_number}번째 줄 숫자 변환 실패: {line}") from error
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"{line_number}번째 줄 좌표가 뒤집혀 있음: {line}")
        parsed.append((label, confidence, Box(x1, y1, x2, y2)))
    if not parsed:
        raise ValueError("탐지 결과가 비어 있습니다.")
    return parsed
