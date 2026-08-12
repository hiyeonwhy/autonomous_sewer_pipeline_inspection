# districts 컬렉션의 PK 필드명을 district_id 에서 id 로 옮깁니다.
#
# 왜 필요한가:
#   구버전 로더는 districts 문서의 PK 를 district_id 로 썼고, 현재 코드는 id 로
#   씁니다. ensure_districts 는 sgg_code 로 매칭해 upsert 하므로 이미 있는 문서에는
#   $setOnInsert 가 걸리지 않아 id 가 영구히 채워지지 않습니다. 그 상태로 새 코드를
#   올리면 ensure_database_indexes 가 districts.id 유니크 인덱스를 만들려다
#   `E11000 dup key: { id: null }` 로 죽고 API 가 기동 자체를 못 합니다.
#   (2026-08-03 배포에서 실제로 발생)
#
#   구버전 데이터가 든 DB 에서 딱 한 번 실행하면 됩니다. 새로 만든 DB 에는
#   해당 사항이 없고, 이미 옮겨진 DB 에서 다시 돌려도 아무 일도 하지 않습니다.
#
# python3 scripts/migrate_districts_id.py --dry-run
# python3 scripts/migrate_districts_id.py

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import AsyncMongoClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

BACKUP_COLLECTION = "districts_backup_pre_id_rename"
STALE_INDEX = "district_id_1"


async def migrate(database, dry_run: bool) -> int:
    total = await database.districts.count_documents({})
    stale = await database.districts.count_documents({"district_id": {"$exists": True}})
    missing_id = await database.districts.count_documents(
        {"$or": [{"id": None}, {"id": {"$exists": False}}]}
    )

    print(f"districts 문서      {total}개")
    print(f"district_id 보유    {stale}개")
    print(f"id 없음             {missing_id}개")

    if stale == 0:
        print("\n옮길 문서가 없습니다. 이미 마이그레이션된 DB 입니다.")
        return 0

    # 값이 겹치면 유니크 인덱스를 만들 수 없으므로 미리 막습니다.
    pipeline = [
        {"$match": {"district_id": {"$exists": True}}},
        {"$group": {"_id": "$district_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ]
    # pymongo async 의 aggregate 는 커서를 코루틴으로 돌려주므로 먼저 await 한다.
    cursor = await database.districts.aggregate(pipeline)
    duplicates = [row async for row in cursor]
    if duplicates:
        raise SystemExit(
            f"district_id 값이 중복됩니다: {duplicates[:5]} — 수동 정리가 필요합니다."
        )

    existing_ids = await database.districts.distinct("id", {"id": {"$ne": None}})
    collisions = await database.districts.count_documents(
        {"district_id": {"$in": existing_ids}}
    )
    if collisions:
        raise SystemExit(
            f"이미 id 로 쓰이는 값을 district_id 가 {collisions}건 갖고 있습니다 — 수동 정리가 필요합니다."
        )

    if dry_run:
        print(f"\n[dry-run] {stale}개 문서의 district_id 를 id 로 옮깁니다.")
        return stale

    print(f"\n백업 → {BACKUP_COLLECTION}")
    await database[BACKUP_COLLECTION].drop()
    backup_cursor = await database.districts.aggregate([{"$out": BACKUP_COLLECTION}])
    await backup_cursor.to_list(None)
    backed_up = await database[BACKUP_COLLECTION].count_documents({})
    if backed_up != total:
        raise SystemExit(f"백업 문서 수가 맞지 않습니다: {backed_up} != {total}")

    # rename 하면 모든 문서가 district_id: null 이 되므로, 유니크 인덱스가 남아
    # 있으면 그 update 자체가 실패합니다. 먼저 떨어뜨립니다.
    try:
        await database.districts.drop_index(STALE_INDEX)
        print(f"낡은 인덱스 제거   {STALE_INDEX}")
    except Exception as error:  # 인덱스가 없으면 그냥 넘어갑니다
        print(f"{STALE_INDEX} 제거 생략 ({type(error).__name__})")

    result = await database.districts.update_many(
        {"district_id": {"$exists": True}}, {"$rename": {"district_id": "id"}}
    )
    print(f"필드명 변경        {result.modified_count}개")

    remaining = await database.districts.count_documents(
        {"$or": [{"id": None}, {"id": {"$exists": False}}]}
    )
    unique_ids = len(await database.districts.distinct("id"))
    if remaining or unique_ids != total:
        raise SystemExit(
            f"검증 실패 — id 없음 {remaining}개, id 종류 {unique_ids}개 / 문서 {total}개. "
            f"{BACKUP_COLLECTION} 으로 되돌리세요."
        )

    print(f"검증 완료          {total}개 문서 모두 유니크한 id 보유")
    return result.modified_count


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="바꾸지 않고 무엇을 바꿀지만 출력"
    )
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]
    try:
        await database.command("ping")
        await migrate(database, args.dry_run)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
