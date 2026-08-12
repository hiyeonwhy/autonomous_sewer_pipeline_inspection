from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.domain.priority import loader
from app.domain.priority.schema import (
    AdminDongResponse,
    BatchResponse,
    BatchStatus,
    DistrictListResponse,
    DongListResponse,
    HistoryPoint,
    HistoryResponse,
    InspectionSummary,
    MetaResponse,
    RecalculateAccepted,
    RecalculateRequest,
    ScoreDetail,
    ScoreSummary,
    SidoSummaryResponse,
)

router = APIRouter(prefix="/api/priority", tags=["priority"])

# 엔진 재실행 상한. 침하 상세 조회가 N+1 이라 길게 잡습니다.
ENGINE_TIMEOUT_SECONDS = 900

# 재산정 잠금 문서의 고정 _id. _id 삽입이 원자적이라 이 삽입 성공 = 잠금 획득.
RECALC_LOCK_ID = "recalculate"
# 잠금이 이보다 오래되면 죽은 프로세스가 남긴 것으로 보고 회수합니다.
LOCK_STALE_SECONDS = ENGINE_TIMEOUT_SECONDS + 60

# 경계 GeoJSON 1.28 MB 는 사실상 불변입니다. 매 요청 전송하면 지도 UX 가 무거워지므로
# 파일 지문으로 ETag 를 만들고 304 를 돌려줍니다.
GEOJSON_MAX_AGE = 86400

_etag_cache: dict[str, tuple[float, int, str]] = {}


def file_etag(path: Path) -> str:
    stat = path.stat()
    cached = _etag_cache.get(str(path))
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    etag = f'"{digest.hexdigest()[:32]}"'
    _etag_cache[str(path)] = (stat.st_mtime, stat.st_size, etag)
    return etag


def etag_matches(header: str | None, etag: str) -> bool:
    # If-None-Match 는 여러 값과 약한 검증자(W/)가 올 수 있습니다. 단순 문자열
    # 동치로만 보면 그런 클라이언트가 항상 200 을 받아 캐시가 무력해집니다.
    if not header:
        return False
    if header.strip() == "*":
        return True
    candidates = [value.strip() for value in header.split(",")]
    return any(value == etag or value == f"W/{etag}" for value in candidates)


async def latest_batch_id(database) -> str | None:
    document = await database.priority_batches.find_one(
        {"status": BatchStatus.COMPLETED.value}, sort=[("finished_at", DESCENDING)]
    )
    if document:
        # 점수 문서는 엔진 batch_id 로 저장됩니다. 배치 문서의 batch_id 는
        # 요청 시점의 임시 id 라 engine_batch_id 가 우선입니다.
        return document.get("engine_batch_id") or document["batch_id"]

    # 배치 기록 없이 적재만 된 경우(스크립트 직접 실행)를 위한 대비.
    document = await database.priority_meta.find_one(sort=[("calculated_at", DESCENDING)])
    return document["batch_id"] if document else None


async def require_batch(database, batch_id: str | None) -> str:
    if batch_id is not None:
        # 명시한 배치가 없으면 404 가 맞습니다. 예전에는 200 + 빈 결과라
        # "데이터 없음"과 "잘못된 배치 id"를 구분할 수 없었습니다.
        exists = await database.priority_meta.find_one({"batch_id": batch_id})
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"배치를 찾을 수 없습니다: {batch_id}",
            )
        return batch_id

    resolved = await latest_batch_id(database)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "적재된 우선순위 산정 결과가 없습니다. "
                "POST /api/priority/recalculate 로 적재하세요."
            ),
        )
    return resolved


async def load_meta(database, batch_id: str) -> MetaResponse | None:
    document = await database.priority_meta.find_one({"batch_id": batch_id})
    if document is None:
        return None
    document.pop("_id", None)
    document.pop("calculated_at", None)
    return MetaResponse.model_validate(document)


async def resolve_district(database, district: str) -> dict:
    # int id 와 5자리 sgg_code 를 모두 받습니다.
    #
    # 프론트는 GeoJSON 에서 sgg_code 를 얻으므로 그대로 쓸 수 있어야 하고,
    # 기존 컨벤션(int id)도 유지해야 합니다. 5자리 숫자는 sgg_code 로 봅니다.
    if district.isdigit() and len(district) == 5:
        query = {"sgg_code": district}
    elif district.isdigit():
        query = {"id": int(district)}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"district 는 int id 또는 5자리 시군구 코드여야 합니다: {district!r}",
        )

    document = await database.districts.find_one(query)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"시군구를 찾을 수 없습니다: {district}",
        )
    return document


@router.get("/districts", response_model=DistrictListResponse)
async def list_districts(
    request: Request,
    sido: str | None = Query(default=None, description="시도 코드 2자리 (예: 29 광주)"),
    grade: int | None = Query(default=None, ge=1, le=5, description="우선순위 등급"),
    # 기본값을 229 로 박지 않습니다 — 행정구역 수는 데이터의 사실이지 코드의
    # 상수가 아닙니다. 상한과 같게 두어 기본 호출이 잘리지 않게 합니다.
    limit: int = Query(default=1000, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    batch_id: str | None = Query(default=None, description="생략하면 최신 배치"),
) -> DistrictListResponse:
    database = request.app.state.database
    resolved = await require_batch(database, batch_id)

    query: dict = {"batch_id": resolved}
    if sido:
        query["sido_code"] = sido
    if grade is not None:
        query["grade"] = grade

    total = await database.priority_scores.count_documents(query)
    cursor = (
        database.priority_scores.find(query)
        .sort("rank", ASCENDING)
        .skip(offset)
        .limit(limit)
    )
    documents = await cursor.to_list(length=limit)

    return DistrictListResponse(
        meta=await load_meta(database, resolved),
        total=total,
        districts=[ScoreSummary.model_validate(document) for document in documents],
    )


@router.get("/sido", response_model=list[SidoSummaryResponse])
async def list_sido(
    request: Request,
    batch_id: str | None = Query(default=None),
) -> list[SidoSummaryResponse]:
    database = request.app.state.database
    resolved = await require_batch(database, batch_id)
    cursor = database.priority_by_sido.find({"batch_id": resolved}).sort(
        "rank", ASCENDING
    )
    documents = await cursor.to_list(length=100)
    return [SidoSummaryResponse.model_validate(document) for document in documents]


@router.get("/districts/geojson")
async def districts_geojson(request: Request) -> Response:
    path = loader.ENGINE_DATA / loader.GEOJSON_FILE
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{path.name} 이 없습니다. boundary/build_boundaries.py 를 실행하세요.",
        )

    # 6.2 MB 해시는 첫 요청에서 수백 ms 짜리 동기 작업입니다. 이벤트 루프를
    # 세우지 않게 스레드로 뺍니다 (이후에는 mtime 캐시에 걸립니다).
    etag = await asyncio.to_thread(file_etag, path)
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return FileResponse(
        path,
        media_type="application/geo+json",
        headers={"ETag": etag, "Cache-Control": f"public, max-age={GEOJSON_MAX_AGE}"},
    )


@router.get("/sido/geojson")
async def sido_geojson(request: Request) -> Response:
    # 시도 경계선(17개 폴리곤). 지도의 검정 시도 경계가 이걸 그립니다.
    path = loader.ENGINE_DATA / loader.SIDO_GEOJSON_FILE
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{path.name} 이 없습니다. boundary/build_boundaries.py 를 실행하세요.",
        )

    etag = await asyncio.to_thread(file_etag, path)
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return FileResponse(
        path,
        media_type="application/geo+json",
        headers={"ETag": etag, "Cache-Control": f"public, max-age={GEOJSON_MAX_AGE}"},
    )


# 전국 읍면동 6.2 MB 를 매번 통째로 보낼 수 없어 시군구 단위로 잘라 줍니다.
# 파일 mtime 이 바뀌면 다시 읽습니다.
_dongs_cache: dict = {"mtime": None, "by_sgg": {}}


def dongs_by_sgg(path: Path) -> dict[str, list]:
    mtime = path.stat().st_mtime
    if _dongs_cache["mtime"] != mtime:
        payload = json.loads(path.read_text(encoding="utf-8"))
        grouped: dict[str, list] = {}
        for feature in payload.get("features", []):
            grouped.setdefault(feature["properties"]["sgg_code"], []).append(feature)
        _dongs_cache["mtime"] = mtime
        _dongs_cache["by_sgg"] = grouped
    return _dongs_cache["by_sgg"]


@router.get("/dongs/geojson")
async def dongs_geojson(
    request: Request,
    sgg: str | None = Query(default=None, description="시군구 코드 5자리 — 그 구역만"),
) -> Response:
    # Level 2 읍면동 경계 (전국). 점수는 싣지 않습니다 — 읍면동 단위 지표가
    # 침하 이력 하나뿐이라 운영자 판단 대상입니다.
    path = loader.ENGINE_DATA / loader.DONGS_GEOJSON_FILE
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{path.name} 이 없습니다. boundary/build_boundaries.py 를 실행하세요.",
        )

    base_etag = await asyncio.to_thread(file_etag, path)
    etag = base_etag if sgg is None else base_etag[:-1] + f"-{sgg}\""
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    headers = {"ETag": etag, "Cache-Control": f"public, max-age={GEOJSON_MAX_AGE}"}

    if sgg is None:
        return FileResponse(path, media_type="application/geo+json", headers=headers)

    # 6.2 MB JSON 파싱도 첫 요청에서 이벤트 루프를 세우므로 스레드로 뺍니다.
    features = (await asyncio.to_thread(dongs_by_sgg, path)).get(sgg, [])
    return JSONResponse(
        {"type": "FeatureCollection", "features": features},
        media_type="application/geo+json",
        headers=headers,
    )


@router.get("/districts/{district}", response_model=ScoreDetail)
async def get_district(
    district: str,
    request: Request,
    batch_id: str | None = Query(default=None),
) -> ScoreDetail:
    database = request.app.state.database
    resolved = await require_batch(database, batch_id)
    row = await resolve_district(database, district)

    document = await database.priority_scores.find_one(
        {"batch_id": resolved, "sgg_code": row["sgg_code"]}
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{row['full_name']} 의 산정 결과가 배치 {resolved} 에 없습니다.",
        )

    meta = await load_meta(database, resolved)
    document.pop("_id", None)
    document["display_names"] = meta.display_names if meta else {}
    return ScoreDetail.model_validate(document)


@router.get("/districts/{district}/dongs", response_model=DongListResponse)
async def get_district_dongs(district: str, request: Request) -> DongListResponse:
    database = request.app.state.database
    row = await resolve_district(database, district)

    cursor = database.admin_dongs.find({"sgg_code": row["sgg_code"]}).sort(
        "adm_code", ASCENDING
    )
    documents = await cursor.to_list(length=500)

    # 침하 카운트는 시군구 한 번의 조회로 모아서 셉니다. 동마다 2회씩 세면
    # 동 17개짜리 구에서 34쿼리(N+1)가 됩니다.
    total_counts: dict[str, int] = {}
    sewer_counts: dict[str, int] = {}
    unlocated = 0
    async for record in database.subsidence_records.find(
        {"sgg_code": row["sgg_code"]}, {"adm_code": 1, "cause_is_sewer": 1}
    ):
        adm_code = record.get("adm_code")
        if not adm_code:
            # 행정동을 특정하지 못한 건 — 어느 동 카운트에도 안 들어가므로
            # 별도로 세어 응답에 싣습니다. 숨기면 "이력 없음"이 거짓말이 됩니다.
            unlocated += 1
            continue
        total_counts[adm_code] = total_counts.get(adm_code, 0) + 1
        if record.get("cause_is_sewer"):
            sewer_counts[adm_code] = sewer_counts.get(adm_code, 0) + 1

    dongs = []
    for document in documents:
        dongs.append(
            AdminDongResponse(
                adm_code=document["adm_code"],
                adm_name=document.get("adm_name", ""),
                sgg_code=document.get("sgg_code", row["sgg_code"]),
                subsidence_count=total_counts.get(document["adm_code"], 0),
                sewer_cause_count=sewer_counts.get(document["adm_code"], 0),
            )
        )

    return DongListResponse(
        sgg_code=row["sgg_code"],
        full_name=row.get("full_name", ""),
        total=len(dongs),
        unlocated_count=unlocated,
        dongs=dongs,
    )


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    request: Request,
    district: str = Query(description="int id 또는 5자리 시군구 코드"),
    limit: int = Query(default=20, ge=1, le=200),
) -> HistoryResponse:
    database = request.app.state.database
    row = await resolve_district(database, district)

    cursor = (
        database.priority_scores.find({"sgg_code": row["sgg_code"]})
        .sort("calculated_at", DESCENDING)
        .limit(limit)
    )
    documents = await cursor.to_list(length=limit)

    # 산정(예측) 옆에 실측을 나란히 둡니다 — 기획서 Ⅶ '과거 산정 결과와 실제
    # 결함 발견 이력 비교'. 세션은 로봇 점검 도메인이 만들고, 여기서는 이
    # 구역(district_id)에서 실행된 세션과 결함 수만 읽습니다.
    session_cursor = (
        database.sessions.find({"district_id": row["id"]})
        .sort("started_at", DESCENDING)
        .limit(limit)
    )
    sessions = await session_cursor.to_list(length=limit)

    # 결함 수는 세션 id 묶음 한 번의 조회로 셉니다. 세션마다 세면 limit=200
    # 에서 200쿼리(N+1)가 됩니다.
    defect_counts: dict = {}
    if sessions:
        async for defect in database.defects.find(
            {"session_id": {"$in": [session["id"] for session in sessions]}},
            {"session_id": 1},
        ):
            key = defect["session_id"]
            defect_counts[key] = defect_counts.get(key, 0) + 1

    inspections = [
        InspectionSummary(
            session_id=session["id"],
            started_at=session.get("started_at"),
            status=session.get("status"),
            defect_count=defect_counts.get(session["id"], 0),
        )
        for session in sessions
    ]

    return HistoryResponse(
        district_id=row["id"],
        sgg_code=row["sgg_code"],
        full_name=row.get("full_name", ""),
        points=[HistoryPoint.model_validate(document) for document in documents],
        inspections=inspections,
    )


@router.get("/batches", response_model=list[BatchResponse])
async def list_batches(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BatchResponse]:
    database = request.app.state.database
    cursor = database.priority_batches.find().sort("queued_at", DESCENDING).limit(limit)
    documents = await cursor.to_list(length=limit)
    return [BatchResponse.model_validate(document) for document in documents]


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: str, request: Request) -> BatchResponse:
    database = request.app.state.database
    # 임시 id(202 응답의 status_url)와 엔진 batch_id 어느 쪽으로도 찾습니다.
    document = await database.priority_batches.find_one(
        {"$or": [{"batch_id": batch_id}, {"engine_batch_id": batch_id}]}
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"배치를 찾을 수 없습니다: {batch_id}",
        )
    return BatchResponse.model_validate(document)


async def run_recalculation(database, batch_id: str, run_engine: bool) -> None:
    # 백그라운드 재산정. 실패해도 배치 문서에 사유를 남깁니다.
    await database.priority_batches.update_one(
        {"batch_id": batch_id},
        {
            "$set": {
                "status": BatchStatus.RUNNING.value,
                "started_at": datetime.now(timezone.utc),
            }
        },
    )

    try:
        if run_engine:
            # 엔진은 raw/ 원본 28 MB 를 읽습니다. API 컨테이너에 원본이 없으면
            # 여기서 실패하는 것이 정상이고, 그 경우 run_engine=false 로 적재만 합니다.
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "run.py",
                cwd=str(loader.ENGINE_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(), timeout=ENGINE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                process.kill()
                # kill 만 하고 떠나면 좀비 프로세스가 남습니다. 회수까지.
                await process.wait()
                raise RuntimeError(
                    f"엔진 실행이 {ENGINE_TIMEOUT_SECONDS}초를 넘겼습니다."
                ) from None

            if process.returncode != 0:
                tail = output.decode(errors="replace")[-1500:]
                raise RuntimeError(f"엔진 종료 코드 {process.returncode}\n{tail}")

        result = await loader.load_scores(database)

        # 엔진 산출물의 batch_id 는 입력 데이터 지문에서 나옵니다. 문서의
        # batch_id(임시 id)는 그대로 두고 engine_batch_id 로만 기록합니다.
        # 갈아끼우면 두 가지가 깨집니다:
        #   (1) 202 로 돌려준 status_url 이 완료 순간 404 가 됩니다.
        #   (2) 같은 입력을 다시 적재하면 unique 인덱스 충돌로 배치가
        #       RUNNING 으로 남아 이후 모든 재산정이 409 로 막힙니다.
        await database.priority_batches.update_one(
            {"batch_id": batch_id},
            {
                "$set": {
                    "status": BatchStatus.COMPLETED.value,
                    "finished_at": datetime.now(timezone.utc),
                    "engine_batch_id": result["batch_id"],
                    "model_version": result["model_version"],
                    "input_digest": result.get("input_digest"),
                    "district_count": result["district_count"],
                    "previous_batch_id": result.get("previous_batch_id"),
                    "changed_ranks": result.get("changed_ranks"),
                    "max_rank_shift": result.get("max_rank_shift"),
                    # 입력 지문이 직전과 같으면 False. 화면이 "변경 없음"을
                    # 말할 수 있어야 재산정 버튼이 거짓말하지 않습니다.
                    "input_changed": result.get("input_changed"),
                }
            },
        )

    except Exception as error:  # noqa: BLE001: 어떤 실패든 배치 문서에 남겨야 합니다
        await database.priority_batches.update_one(
            {"batch_id": batch_id},
            {
                "$set": {
                    "status": BatchStatus.FAILED.value,
                    "finished_at": datetime.now(timezone.utc),
                    "error": str(error)[:2000],
                }
            },
        )
    finally:
        # 성공이든 실패든 잠금은 반드시 풉니다. 프로세스가 여기 오기 전에
        # 죽으면 잠금이 남는데, 그 경우는 recalculate() 의 회수 로직이 처리합니다.
        await database.priority_locks.delete_one({"_id": RECALC_LOCK_ID})


@router.post(
    "/recalculate",
    response_model=RecalculateAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def recalculate(
    payload: RecalculateRequest,
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    database = request.app.state.database
    queued_at = datetime.now(timezone.utc)

    # 요청 시점에는 입력 지문을 모르므로 임시 id 를 씁니다. 이 id 가 문서에
    # 그대로 남고, 엔진 batch_id 는 완료 시 engine_batch_id 에 기록됩니다.
    # 마이크로초까지 넣는 이유: 같은 초에 두 요청이 오면 unique 인덱스 충돌.
    provisional = f"pending-{queued_at.strftime('%Y%m%dT%H%M%S%f')}"

    # 죽은 잠금 회수: 서버가 재시작하면 백그라운드 태스크는 사라지는데 잠금
    # 문서는 남습니다. 잠금이 가리키는 배치가 이미 종결됐거나 상한을 넘겼으면
    # 회수합니다. 예전에는 이 복구가 없어서 재시작 후 모든 재산정이 409 였습니다.
    lock = await database.priority_locks.find_one({"_id": RECALC_LOCK_ID})
    if lock is not None:
        batch = await database.priority_batches.find_one(
            {"batch_id": lock.get("batch_id")}
        )
        settled = batch is None or batch["status"] in (
            BatchStatus.COMPLETED.value,
            BatchStatus.FAILED.value,
        )
        locked_at = lock.get("locked_at") or queued_at
        if locked_at.tzinfo is None:  # mongomock 은 naive 로 돌려줍니다
            locked_at = locked_at.replace(tzinfo=timezone.utc)
        stale = (queued_at - locked_at).total_seconds() > LOCK_STALE_SECONDS
        if settled or stale:
            await database.priority_locks.delete_one({"_id": RECALC_LOCK_ID})

    # 잠금 획득 = 고정 _id 삽입. _id 는 항상 unique 라 확인→삽입 사이에 다른
    # 요청이 끼어들 수 없습니다 (예전 방식은 조회→삽입이 원자적이지 않아 동시
    # 요청 둘 다 409 를 통과했습니다).
    try:
        await database.priority_locks.insert_one(
            {"_id": RECALC_LOCK_ID, "batch_id": provisional, "locked_at": queued_at}
        )
    except DuplicateKeyError:
        holder = lock.get("batch_id") if lock else "알 수 없음"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 진행 중인 배치가 있습니다: {holder}",
        ) from None

    # 잠금을 잡았으므로 지금 QUEUED/RUNNING 인 배치는 전부 서버 중단으로
    # 완료되지 못한 고아입니다. FAILED 로 닫아 이력이 진실을 말하게 합니다.
    await database.priority_batches.update_many(
        {"status": {"$in": [BatchStatus.QUEUED.value, BatchStatus.RUNNING.value]}},
        {
            "$set": {
                "status": BatchStatus.FAILED.value,
                "finished_at": queued_at,
                "error": "서버 중단으로 완료되지 못한 배치입니다 (자동 정리)",
            }
        },
    )

    document = {
        "batch_id": provisional,
        "status": BatchStatus.QUEUED.value,
        "queued_at": queued_at,
        "run_engine": payload.run_engine,
        "reason": payload.reason,
    }
    await database.priority_batches.insert_one(document)

    background.add_task(run_recalculation, database, provisional, payload.run_engine)

    accepted = RecalculateAccepted(
        batch_id=provisional,
        status=BatchStatus.QUEUED,
        status_url=f"/api/priority/batches/{provisional}",
        queued_at=queued_at,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=accepted.model_dump(mode="json"),
    )
