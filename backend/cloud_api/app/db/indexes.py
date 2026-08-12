from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase


async def ensure_database_indexes(database: AsyncDatabase) -> None:
    await database.sessions.create_index("id", unique=True)
    await database.sessions.create_index([("started_at", DESCENDING)])
    # priority /history 가 구역별 세션을 최근순으로 읽습니다.
    await database.sessions.create_index(
        [("district_id", ASCENDING), ("started_at", DESCENDING)]
    )

    await database.defects.create_index("id", unique=True)
    await database.defects.create_index([("session_id", ASCENDING)])
    await database.defects.create_index(
        [
            ("session_id", ASCENDING),
            ("operation_id", ASCENDING),
            ("event_id", ASCENDING),
            ("detection_index", ASCENDING),
        ],
        unique=True,
        partialFilterExpression={
            "operation_id": {"$type": "string"},
            "event_id": {"$type": "string"},
            "detection_index": {"$type": "int"},
        },
    )

    await database.agent_telemetry.create_index("agent_id", unique=True)
    await database.agent_commands.create_index("command_id", unique=True)
    await database.agent_commands.create_index(
        [
            ("agent_id", ASCENDING),
            ("request_id", ASCENDING),
        ],
        unique=True,
    )
    await database.agent_commands.create_index(
        [
            ("agent_id", ASCENDING),
            ("status", ASCENDING),
            ("claimed_until", ASCENDING),
            ("created_at", ASCENDING),
        ]
    )
    await database.agent_commands.create_index("expires_at", expireAfterSeconds=86_400)

    await database.drive_logs.create_index("id", unique=True)
    await database.drive_logs.create_index([("session_id", ASCENDING)])

    await database.reports.create_index("id", unique=True)
    await database.reports.create_index([("session_id", ASCENDING)])

    await database.reconstructions.create_index("id", unique=True)
    await database.reconstructions.create_index([("session_id", ASCENDING)])

    # 우선순위 산정 도메인
    await database.districts.create_index("sgg_code", unique=True)
    await database.districts.create_index("id", unique=True)
    await database.districts.create_index([("sido_code", ASCENDING)])

    await database.admin_dongs.create_index("adm_code", unique=True)
    await database.admin_dongs.create_index([("sgg_code", ASCENDING)])

    # 목록 조회는 batch 안에서 rank 순입니다.
    await database.priority_scores.create_index(
        [("batch_id", ASCENDING), ("rank", ASCENDING)]
    )
    await database.priority_scores.create_index(
        [("batch_id", ASCENDING), ("sgg_code", ASCENDING)], unique=True
    )
    # history 는 한 구역의 시점별 변화를 봅니다.
    await database.priority_scores.create_index(
        [("sgg_code", ASCENDING), ("calculated_at", DESCENDING)]
    )
    await database.priority_by_sido.create_index(
        [("batch_id", ASCENDING), ("rank", ASCENDING)]
    )
    await database.priority_meta.create_index("batch_id", unique=True)
    await database.priority_batches.create_index("batch_id", unique=True)
    await database.priority_batches.create_index([("queued_at", DESCENDING)])
    # 최신 배치 조회가 status 필터 + finished_at 정렬입니다.
    await database.priority_batches.create_index(
        [("status", ASCENDING), ("finished_at", DESCENDING)]
    )

    await database.subsidence_records.create_index("sago_no", unique=True)
    await database.subsidence_records.create_index([("adm_code", ASCENDING)])
    await database.subsidence_records.create_index([("sgg_code", ASCENDING)])

    await database.robots.create_index("id", unique=True)
    await database.operators.create_index("id", unique=True)
    await database.counters.create_index("_id")
