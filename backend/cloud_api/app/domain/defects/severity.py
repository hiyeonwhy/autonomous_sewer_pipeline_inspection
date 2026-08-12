"""바운딩 박스 크기로 심각도를 정한다.

YOLO는 결함 종류와 신뢰도만 낸다. 심각도는 내지 않는데, 상태등급이 결함 종류와
심각도의 조합으로 정해지므로(지침 제6장 결함점수 배점표) 어딘가에서 채워야 한다.

기준은 **박스가 원본 프레임에서 차지하는 면적 비율**이다. 결함이 클수록 심하다는
단순한 가정이고, 사진 한 장에서 코드가 계산할 수 있는 유일한 크기 정보다.

**이 값은 근사치다.** 화면 면적 비율은 카메라와 결함의 거리에 따라 달라진다 —
같은 균열이 1m 앞에서는 화면을 넓게 차지하고 3m 앞에서는 좁게 차지한다. 지침이
요구하는 실측치(균열 폭 mm, 손상 면적)와는 다른 값이며, 그것을 재려면 깊이 정보나
관경 대비 픽셀 환산이 필요하다. 그 전까지의 대용값이다.
"""

from __future__ import annotations

from app.domain.defects.schema import BoundingBox, Severity

# 면적 비율 → 심각도. 큰 값부터 검사한다.
#
# 경계값은 실제 탐지 3건(표면손상 2.95%, 균열 2.37%, 균열 0.82%)이 서로 다른 등급으로
# 갈리도록 잡았다. 지침에 「면적 비율 몇 %가 어느 심각도」라는 규정은 없다 —
# 지침은 결함 종류별 실측 기준(균열 폭, 손상 깊이)을 쓴다. 그래서 이 숫자는
# 관측 자료가 쌓이면 조정할 대상이다.
SEVERITY_AREA_BANDS: tuple[tuple[float, Severity], ...] = (
    (0.025, Severity.CRITICAL),
    (0.010, Severity.WARNING),
    (0.000, Severity.NORMAL),
)


def bbox_area_share(
    bbox: BoundingBox,
    *,
    image_width: int,
    image_height: int,
) -> float:
    """박스가 프레임에서 차지하는 면적 비율(0~1).

    크롭이나 마스킹 이전의 **원본 프레임** 기준이다. 결함 사진을 기준으로 재면
    크롭 영역 크기에 따라 같은 결함의 비율이 달라져 심각도가 흔들린다.
    """
    frame_area = float(image_width) * float(image_height)
    if frame_area <= 0:
        return 0.0
    box_area = max(0.0, bbox.x2 - bbox.x1) * max(0.0, bbox.y2 - bbox.y1)
    return min(1.0, box_area / frame_area)


def severity_from_bbox(
    bbox: BoundingBox,
    *,
    image_width: int,
    image_height: int,
) -> Severity:
    """박스 면적 비율로 심각도를 정한다."""
    share = bbox_area_share(bbox, image_width=image_width, image_height=image_height)
    for threshold, severity in SEVERITY_AREA_BANDS:
        if share >= threshold:
            return severity
    # 마지막 구간이 0.0 이므로 여기까지 오지 않는다. 표를 잘못 고쳤을 때의 방어.
    return Severity.NORMAL
