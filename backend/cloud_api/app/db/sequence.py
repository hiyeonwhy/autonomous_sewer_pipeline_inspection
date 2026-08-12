from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase


async def get_next_sequence(
    database: AsyncDatabase,
    sequence_name: str,
) -> int:
    result = await database.counters.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    if result is None:
        raise RuntimeError(
            f"ID 시퀀스 생성에 실패했습니다: {sequence_name}"
        )

    return int(result["sequence_value"])