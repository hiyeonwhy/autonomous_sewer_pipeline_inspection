# 우선순위 엔진 산출물을 MongoDB 에 적재합니다.
#
# API 를 띄우지 않고 적재만 할 때 씁니다. `POST /api/priority/recalculate` 가
# 내부에서 쓰는 것과 같은 로더입니다.
#
# python3 scripts/load_priority.py
# python3 scripts/load_priority.py --data ../priority_engine/data

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import AsyncMongoClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.indexes import ensure_database_indexes  # noqa: E402
from app.domain.priority import loader  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="우선순위 엔진 산출물을 MongoDB 에 적재합니다."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help=f"산출물 디렉터리 (기본: {loader.ENGINE_DATA})",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]

    try:
        await database.command("ping")
        await ensure_database_indexes(database)
        result = await loader.load_scores(database, args.data)
        # 배치 기록까지 남겨야 라우터가 이 적재본을 최신으로 인식합니다.
        await loader.register_direct_load(database, result)
    finally:
        await client.close()

    print(f"배치        {result['batch_id']}")
    print(f"모델        {result['model_version']}")
    print(f"시군구      {result['district_count']}개")
    if result.get("previous_batch_id"):
        print(f"직전 배치   {result['previous_batch_id']}")
        print(f"순위 변동   {result['changed_ranks']}개 (최대 {result['max_rank_shift']}위)")
    else:
        print("직전 배치   없음 (최초 적재)")


if __name__ == "__main__":
    asyncio.run(main())
