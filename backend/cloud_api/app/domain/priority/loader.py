from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings
from app.db.sequence import get_next_sequence

# 엔진 자체의 위치. 저장소에서는 cloud_api 와 priority_engine 이 같은 backend/
# 아래 형제입니다. /recalculate 의 run_engine=true 경로만 이 값을 씁니다.
ENGINE_ROOT = Path(__file__).resolve().parents[4] / "priority_engine"

# 산출물 위치. 컨테이너에는 cloud_api 만 들어오므로 위 형제 관계가 성립하지
# 않습니다. 그래서 PRIORITY_DATA_DIR 로 마운트 지점을 받고, 없으면 저장소
# 레이아웃으로 떨어집니다 — 로컬에서는 지금까지와 똑같이 동작합니다.
ENGINE_DATA = Path(get_settings().priority_data_dir or ENGINE_ROOT / "data")

SCORES_FILE = "priority_scores.json"
SIDO_FILE = "priority_by_sido.json"
MASTER_FILE = "districts_master.json"
DONGS_FILE = "admin_dongs_master.json"
# 건별 침하 레코드 — 엔진이 원인 분류·행정동 매칭까지 끝내서 내보냅니다.
# 읍면동 뷰의 "침하 N건" 이 이 컬렉션에서 나옵니다.
SUBSIDENCE_FILE = "subsidence_records.json"
GEOJSON_FILE = "municipality_boundaries.geojson"
# 시도 경계선용 폴리곤 17개. 시군구 경계를 이어 붙여 만들 수 없어(단순화가
# 시군구별 독립이라 인접 폴리곤이 꼭짓점을 공유하지 않음) 엔진이 따로 냅니다.
SIDO_GEOJSON_FILE = "sido_boundaries.geojson"
# 읍면동 경계 (전국). 점수 단위는 시군구이므로 동은 경계·이름·침하 이력만.
DONGS_GEOJSON_FILE = "admin_dongs.geojson"


class EngineOutputMissing(RuntimeError):
    pass


def data_dir(override: Path | None = None) -> Path:
    return override or ENGINE_DATA


def read_json(path: Path) -> dict | list:
    if not path.exists():
        raise EngineOutputMissing(
            f"{path} 가 없습니다. priority_engine 에서 `python3 run.py` 를 먼저 실행하세요."
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


async def ensure_districts(database: AsyncDatabase, directory: Path) -> dict[str, int]:
    # districts 를 채우고 {sgg_code: district_id} 를 돌려줍니다.
    #
    # 기존 컨벤션대로 counters 기반 int id 를 씁니다. sgg_code 가 unique 이므로
    # 재적재해도 같은 id 가 유지됩니다. sessions.district_id 가 이 id 를 참조합니다.
    master = read_json(directory / MASTER_FILE)

    existing = {
        document["sgg_code"]: document["id"]
        async for document in database.districts.find({}, {"sgg_code": 1, "id": 1})
    }

    operations: list[UpdateOne] = []
    for row in master:
        code = row["sgg_code"]
        district_id = existing.get(code)
        if district_id is None:
            district_id = await get_next_sequence(database, "districts")
            existing[code] = district_id

        operations.append(
            UpdateOne(
                {"sgg_code": code},
                {
                    "$set": {
                        "sido_code": row.get("sido_code"),
                        "sido_name": row.get("sido_name"),
                        "sgg_name": row.get("sgg_name"),
                        "full_name": row.get("full_name"),
                        "boundary_version": row.get("boundary_version", "ver20251231"),
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$setOnInsert": {"id": district_id, "sgg_code": code},
                },
                upsert=True,
            )
        )

    if operations:
        await database.districts.bulk_write(operations, ordered=False)

    return existing


async def load_admin_dongs(database: AsyncDatabase, directory: Path) -> int:
    path = directory / DONGS_FILE
    if not path.exists():
        return 0

    dongs = read_json(path)
    operations = []
    for row in dongs:
        operations.append(
            UpdateOne(
                {"adm_code": row["adm_code"]},
                {
                    "$set": {
                        "sgg_code": row.get("sgg_code"),
                        "sgg_name": row.get("sgg_name"),
                        "adm_name": row.get("adm_name"),
                    },
                    "$setOnInsert": {"adm_code": row["adm_code"]},
                },
                upsert=True,
            )
        )
    if operations:
        await database.admin_dongs.bulk_write(operations, ordered=False)
    return len(operations)


async def load_subsidence_records(database: AsyncDatabase, directory: Path) -> int:
    """건별 침하 레코드 적재. 파일이 없으면 0 (동 침하 카운트만 0 으로 나옴).

    adm_code 는 행정동이 확정된 건에만 있습니다 — 없는 건은 동 카운트 대신
    unlocated_count 로 집계됩니다.
    """
    path = directory / SUBSIDENCE_FILE
    if not path.exists():
        return 0

    records = read_json(path)
    operations = [
        UpdateOne({"sago_no": row["sago_no"]}, {"$set": row}, upsert=True)
        for row in records
        if row.get("sago_no")
    ]
    if operations:
        await database.subsidence_records.bulk_write(operations, ordered=False)
        await database.subsidence_records.delete_many(
            {"sago_no": {"$nin": [row["sago_no"] for row in records if row.get("sago_no")]}}
        )
    return len(operations)


async def compare_with_previous(
    database: AsyncDatabase, batch_id: str, scores: list[dict]
) -> dict:
    # 직전 배치와 순위를 비교합니다.
    #
    # 값이 같은데 순위가 달라졌다면 모델이 바뀐 것이고, 값이 다르면 데이터가 갱신된
    # 것입니다. input_digest 와 함께 보면 두 원인을 구분할 수 있습니다.
    # **직전** 배치 하나만 봅니다. 예전에는 입력이 같은 배치를 건너뛰고 그 이전과
    # 비교했는데, 그러면 아무것도 안 바뀐 적재에도 옛 배치 대비 변동 수가 나와서
    # "재산정을 누를 때마다 순위가 바뀐다"로 읽혔습니다. 실제로는 안 바뀝니다.
    #
    # 점수 문서는 **엔진 batch_id** 로 저장됩니다. 배치 문서의 batch_id 는
    # /recalculate 가 만든 임시 id(pending-...)일 수 있어, 그걸로 점수를 찾으면
    # 늘 빈 결과가 나와 "순위 변동 None" 이 됩니다.
    #
    # 지금 돌고 있는 배치는 RUNNING 이라 이 조회에 걸리지 않습니다.
    document = await database.priority_batches.find_one(
        {"status": "COMPLETED", "engine_batch_id": {"$ne": None}},
        sort=[("finished_at", -1)],
    )
    if document is None:
        return {
            "previous_batch_id": None,
            "changed_ranks": None,
            "max_rank_shift": None,
            "input_changed": None,
        }

    previous_id = document["engine_batch_id"]

    # 입력 지문이 같으면 순위는 정의상 같습니다. 비교할 것도 없고, 무엇보다
    # "변동 없음"을 화면이 그대로 말할 수 있어야 합니다.
    if previous_id == batch_id:
        return {
            "previous_batch_id": previous_id,
            "changed_ranks": 0,
            "max_rank_shift": 0,
            "input_changed": False,
        }

    old = {
        document["sgg_code"]: document["rank"]
        async for document in database.priority_scores.find(
            {"batch_id": previous_id}, {"sgg_code": 1, "rank": 1}
        )
    }
    if not old:
        return {
            "previous_batch_id": previous_id,
            "changed_ranks": None,
            "max_rank_shift": None,
            "input_changed": True,
        }

    changed = 0
    biggest = 0
    for row in scores:
        before = old.get(row["sgg_code"])
        if before is None:
            continue
        shift = abs(row["rank"] - before)
        if shift:
            changed += 1
            biggest = max(biggest, shift)

    return {
        "previous_batch_id": previous_id,
        "changed_ranks": changed,
        "max_rank_shift": biggest,
        "input_changed": True,
    }


async def load_scores(
    database: AsyncDatabase, directory: Path | None = None
) -> dict:
    # 엔진 산출물을 Mongo 에 적재합니다. batch 단위로 이력이 쌓입니다.
    directory = data_dir(directory)
    payload = read_json(directory / SCORES_FILE)

    if not isinstance(payload, dict) or "meta" not in payload:
        raise EngineOutputMissing(
            f"{SCORES_FILE} 에 meta 봉투가 없습니다. 엔진을 최신 버전으로 다시 실행하세요."
        )

    meta = payload["meta"]
    districts = payload["districts"]
    batch_id = meta["batch_id"]
    calculated_at = datetime.now(timezone.utc)

    code_to_id = await ensure_districts(database, directory)
    await load_admin_dongs(database, directory)
    await load_subsidence_records(database, directory)

    documents = []
    for row in districts:
        code = row["sgg_code"]
        documents.append(
            {
                "batch_id": batch_id,
                "district_id": code_to_id.get(code),
                "sgg_code": code,
                "full_name": row.get("full_name"),
                "sido_code": row.get("sido_code"),
                "sido_name": row.get("sido_name"),
                "score": row.get("score"),
                "rank": row.get("rank"),
                "grade": row.get("grade"),
                "grade_label": row.get("grade_label"),
                "percentile": row.get("percentile"),
                "sido_rank": row.get("sido_rank"),
                "sido_count": row.get("sido_count"),
                "pipe_total_length_km": row.get("pipe_total_length_km"),
                # 강수 트리거 반영 점수. 순위·등급에는 안 들어가지만 목록에서
                # "비까지 보면 몇 위"를 계산하려면 요약 응답에도 있어야 합니다.
                "triggered_score": (row.get("features") or {}).get("triggered_score"),
                "features": row.get("features") or {},
                "contributions": row.get("contributions") or {},
                "contribution_shares": row.get("contribution_shares") or {},
                "missing_features": row.get("missing_features") or [],
                "model_version": meta["model_version"],
                "calculated_at": calculated_at,
            }
        )

    comparison = await compare_with_previous(database, batch_id, documents)

    # 같은 batch 를 다시 적재하면 갈아끼웁니다. 배치는 입력 지문 기반이라
    # 같은 batch_id 는 같은 입력을 뜻합니다. delete → insert 로 갈아끼우면
    # 그 사이 창에서 최신 배치 조회가 빈 결과를 200 으로 돌려주므로, 문서
    # 단위 upsert 로 바꿔 빈 창을 없앱니다 — 각 문서는 항상 완전합니다.
    if documents:
        await database.priority_scores.bulk_write(
            [
                UpdateOne(
                    {"batch_id": batch_id, "sgg_code": row["sgg_code"]},
                    {"$set": row},
                    upsert=True,
                )
                for row in documents
            ],
            ordered=False,
        )
    await database.priority_scores.delete_many(
        {
            "batch_id": batch_id,
            "sgg_code": {"$nin": [row["sgg_code"] for row in documents]},
        }
    )

    sido_path = directory / SIDO_FILE
    if sido_path.exists():
        sido_payload = read_json(sido_path)
        rows = (
            sido_payload.get("sido", [])
            if isinstance(sido_payload, dict)
            else sido_payload
        )
        if rows:
            await database.priority_by_sido.bulk_write(
                [
                    UpdateOne(
                        {"batch_id": batch_id, "sido_code": row["sido_code"]},
                        {"$set": dict(row, batch_id=batch_id)},
                        upsert=True,
                    )
                    for row in rows
                ],
                ordered=False,
            )
        await database.priority_by_sido.delete_many(
            {
                "batch_id": batch_id,
                "sido_code": {"$nin": [row["sido_code"] for row in rows]},
            }
        )

    await database.priority_meta.update_one(
        {"batch_id": batch_id},
        {"$set": dict(meta, batch_id=batch_id, calculated_at=calculated_at)},
        upsert=True,
    )

    return {
        "batch_id": batch_id,
        "district_count": len(documents),
        "model_version": meta["model_version"],
        "input_digest": meta.get("input_digest"),
        **comparison,
    }


async def register_direct_load(database: AsyncDatabase, result: dict) -> dict:
    """스크립트 직접 적재를 배치 기록으로 남깁니다.

    안 남기면 latest_batch_id() 가 과거 /recalculate 배치를 계속 최신으로 뽑아
    새 적재가 무시됩니다 (docs/priority/06-함정.md).
    """
    now = datetime.now(timezone.utc)
    batch_id = result["batch_id"]
    await database.priority_batches.update_one(
        {"batch_id": batch_id},
        {
            "$set": {
                "status": "COMPLETED",
                "finished_at": now,
                "engine_batch_id": batch_id,
                "model_version": result["model_version"],
                "input_digest": result.get("input_digest"),
                "district_count": result["district_count"],
                "previous_batch_id": result.get("previous_batch_id"),
                "changed_ranks": result.get("changed_ranks"),
                "max_rank_shift": result.get("max_rank_shift"),
                # /recalculate 경로와 같은 필드 집합이어야 합니다. 이 필드가
                # 빠지면 스크립트 적재 배치는 "변경 없음" 표시가 절대 안 뜹니다.
                "input_changed": result.get("input_changed"),
            },
            "$setOnInsert": {
                "batch_id": batch_id,
                "queued_at": now,
                "started_at": now,
                # 어떤 경로로 들어온 배치인지 남깁니다. /recalculate 로 만든
                # 배치는 임시 id(pending-...)를 갖는 반면 이쪽은 엔진 id 입니다.
                "source": "script",
            },
        },
        upsert=True,
    )
    return {"batch_id": batch_id, "registered_at": now}
