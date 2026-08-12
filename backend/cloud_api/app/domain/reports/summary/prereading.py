"""점검이 도는 동안 결함 사진을 미리 읽어 둔다.

판독 한 건이 11.7초다(실측). 점검이 끝난 뒤에 몰아서 읽으면 결함 20건에 4분,
30건이면 6분이 걸려 「끝나자마자 보고서」가 불가능하다.

결함은 점검 내내 하나씩 도착하므로, **도착할 때마다 바로 읽어** 결함 문서에 적어
두면 종료 시점에는 대부분 이미 읽혀 있다. 보고서 생성은 그것을 가져다 쓰기만 한다
(`facts.sanitize_defect` → `DefectFact.photo_description`).

주의한 것 둘.

  * **게이트웨이를 몰아치지 않는다.** 응답 잘림·본문 크기 제한이 있는 게이트웨이라
    동시 호출을 늘리면 실패율이 오른다. 세마포어로 동시 건수를 묶는다.
  * **실패해도 점검을 막지 않는다.** 판독은 곁가지다. 예외는 여기서 삼키고, 못 읽은
    결함은 보고서 생성 때 평소 경로로 다시 시도한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.domain.defects.schema import ReviewStatus

from .facts import sanitize_defect
from .prompts import (
    PHOTO_SYSTEM_PROMPT,
    build_photo_payload,
    parse_photo_confirmed,
    parse_photo_description_text,
)
from .provider import build_provider

logger = logging.getLogger(__name__)

# 동시에 열어 둘 판독 수. 게이트웨이가 흔들리면 이 값을 먼저 낮춘다.
PREREAD_CONCURRENCY = 2

# 점검 중 판독은 한 번만 시도한다. 실패하면 보고서 생성 때 다시 읽으므로
# 여기서 매달릴 이유가 없다 — 그동안 다른 결함이 밀린다.
PREREAD_ATTEMPTS = 1

# 사진 판독이 「확인 못 했다」고 해도 목록에서 빼지 않는 결함 종류.
#
# 막힘은 관이 실제로 막혀 있으면 카메라가 장애물에 가로막혀 **화면이 어둡거나 한 색으로
# 가득 찬다.** 그런데 모델은 그걸 「어두워서 관 내부를 판단할 수 없다」로 읽는다.
# 실측에서 신뢰도 0.87 짜리 막힘이 "화면 전체가 어두운 단색에 가깝게 보이며 …
# 식별 불가"라는 판독으로 반려됐다. 어두운 화면은 막힘이 없다는 증거가 아니라
# 오히려 막힘의 모습이다.
#
# 게다가 막힘은 점검을 멈춰 세우는 결함이라 놓치는 대가가 가장 크다. 오탐 하나를
# 남기는 편이 낫다.
NEVER_REJECTED_TYPES = {"BLOCKAGE"}

# 크롭에서 가려지지 않은 면적이 이 아래면 판독 결과로 결함을 빼지 않는다.
#
# 결함 사진은 **다른 결함이 안 보이게** 나머지를 검게 덮어 만든다. 그런데 한 프레임에
# 결함이 여럿이면 덮을 게 많아 크롭이 거의 새까매진다. 실측(세션 220)에서 균열 두 건이
# 각각 0.3% 와 5.0% 만 남았고, 판독 모델은 당연히 「관 내부를 판단할 수 없다」고 답해
# 결함이 목록에서 빠졌다. 사진에 결함이 없어서가 아니라 우리가 가려서다.
#
# 15% 로 두는 이유: 반려된 것들의 보이는 면적을 세 세션에서 재니 0.3·0.6·3.2·4.6·5.0
# 다음이 21.2 였다. 그 사이 어디든 같은 다섯 건을 살린다.
#
# 뿌리는 크롭 쪽에 있다 — 같은 균열에 씌워진 중복 박스까지 가릴 대상에 넣어서 정작
# 그 균열을 덮는다. 거기는 주행에 닿는 코드라 시연 뒤에 손대는 편이 낫다.
MIN_VISIBLE_FRACTION = 0.15

# 마스크는 순수 검정으로 덮지만 JPEG 압축이 값을 조금 흔든다.
_MASK_LEVEL = 12

_semaphore: asyncio.Semaphore | None = None


def _limiter() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(PREREAD_CONCURRENCY)
    return _semaphore


def read_photo_description(document: dict[str, Any]) -> tuple[str, bool] | None:
    """결함 문서 1건의 사진을 읽어 판독 문장을 돌려준다 (블로킹).

    보고서 생성 경로와 **같은 프롬프트·같은 검증기**를 쓴다. 두 경로가 다른 문장을
    만들면 미리 읽은 것과 나중에 읽은 것이 섞여 보고서가 들쭉날쭉해진다.
    """
    settings = get_settings()
    if not settings.llm_describe_images:
        return None

    provider = build_provider()
    if provider is None or not provider.supports_vision:
        return None

    from .service import read_snapshot  # 순환 import 를 피해 늦게 가져온다.

    defect = sanitize_defect(document)
    if defect.snapshot_path is None:
        return None

    image = read_snapshot(defect.snapshot_path, budget=settings.llm_image_budget_bytes)
    for _attempt in range(PREREAD_ATTEMPTS):
        try:
            response = provider.complete(
                system_prompt=PHOTO_SYSTEM_PROMPT,
                user_payload=build_photo_payload(defect),
                image=image,
                max_tokens=1500,
            )
            return (
                parse_photo_description_text(response, defect=defect),
                parse_photo_confirmed(response),
            )
        except Exception as error:  # noqa: BLE001 - 곁가지 작업이라 여기서 끝낸다
            logger.info(
                "결함 %s 사전 판독 실패 (보고서 생성 때 다시 읽음): %s: %s",
                document.get("id"),
                type(error).__name__,
                error,
            )
    return None


def visible_fraction(snapshot_path: str | None) -> float:
    """크롭에서 마스크에 덮이지 않은 면적의 비율. 못 읽으면 1.0 으로 본다.

    못 읽었다고 결함을 살려 두는 쪽이 안전하다 — 판단 근거가 없으면 원래 규칙대로
    판독 결과를 따르게 둔다.
    """
    from app.domain.reports.document import resolve_media_file

    if not snapshot_path:
        return 1.0
    resolved = resolve_media_file(snapshot_path)
    if resolved is None:
        return 1.0
    try:
        from PIL import Image

        with Image.open(resolved) as opened:
            grey = opened.convert("L")
            total = grey.width * grey.height
            masked = sum(
                count for level, count in enumerate(grey.histogram()) if level < _MASK_LEVEL
            )
        return (total - masked) / total if total else 1.0
    except Exception:  # noqa: BLE001 - 곁가지 판단이라 실패해도 흐름을 막지 않는다
        return 1.0


async def preread_defect_photo(database, document: dict[str, Any]) -> None:
    """결함 하나를 배경에서 읽어 문서에 적는다. 실패는 조용히 넘긴다."""
    defect_id = document.get("id")
    if defect_id is None or document.get("photo_description"):
        return
    async with _limiter():
        result = await run_in_threadpool(read_photo_description, document)
    if not result:
        return
    description, confirmed = result
    update: dict[str, Any] = {"photo_description": description, "photo_confirmed": confirmed}
    defect_type = str(document.get("defect_type") or "")
    mostly_masked = visible_fraction(document.get("snapshot_path")) < MIN_VISIBLE_FRACTION
    if not confirmed and defect_type not in NEVER_REJECTED_TYPES and not mostly_masked:
        # 사진에서 확인되지 않은 결함은 목록에서 뺀다. **지우지는 않는다** — 판정이
        # 틀렸을 때 되살릴 수 있어야 하고, 원본 사진과 탐지 기록도 남아야 한다.
        update["review_status"] = ReviewStatus.REJECTED.value
    await database.defects.update_one(
        {"id": defect_id, "photo_description": {"$exists": False}},
        {"$set": update},
    )
    if confirmed:
        outcome = "확인됨"
    elif update.get("review_status"):
        outcome = "확인 안 됨 — 목록에서 제외"
    elif mostly_masked:
        outcome = "확인 안 됨 — 사진이 대부분 가려져 판단 근거 없음, 남김"
    else:
        outcome = f"확인 안 됨 — {defect_type} 는 제외하지 않음"
    logger.info("결함 %s 사진 판독 완료 (%s).", defect_id, outcome)


def schedule_preread(database, documents: list[dict[str, Any]]) -> None:
    """저장된 결함들의 판독을 배경 작업으로 띄운다.

    작업을 붙잡아 두지 않는다 — 수집 응답이 판독을 기다리면 Agent 업로드가 막힌다.
    참조를 남겨 가비지 컬렉터가 중간에 걷어 가지 않게만 한다.
    """
    for document in documents:
        task = asyncio.create_task(preread_defect_photo(database, document))
        _pending.add(task)
        task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task] = set()
