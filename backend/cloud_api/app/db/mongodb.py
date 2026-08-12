from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymongo import AsyncMongoClient

from app.core.config import get_settings
from app.db.indexes import ensure_database_indexes


@asynccontextmanager
async def mongodb_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]



    # 서버 시작 전에 MongoDB 연결 여부 확인
    await database.command("ping")

    await ensure_database_indexes(database)

    app.state.mongodb_client = client
    app.state.database = database

    try:
        yield
    finally:
        await client.close()