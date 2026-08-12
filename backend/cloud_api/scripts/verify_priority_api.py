# priority 도메인 API 검증. MongoDB 서버 없이 돕니다.
#
# mongomock(동기)에 얇은 async 래퍼를 씌워 실제 HTTP 요청을 ASGI로 흘립니다.
# 래퍼는 우리 코드가 실제로 쓰는 연산만 지원하므로, 통과하면 그 연산 집합이
# 맞다는 뜻입니다. priority_engine 의 data/ 산출물이 있어야 합니다.
#
# 필요 패키지: mongomock · httpx
# python3 scripts/verify_priority_api.py

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")

import mongomock  # noqa: E402
import httpx  # noqa: E402


class AsyncCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def sort(self, *args, **kwargs):
        self._cursor = self._cursor.sort(*args, **kwargs)
        return self

    def skip(self, n):
        self._cursor = self._cursor.skip(n)
        return self

    def limit(self, n):
        self._cursor = self._cursor.limit(n)
        return self

    async def to_list(self, length=None):
        rows = list(self._cursor)
        return rows if length is None else rows[:length]

    def __aiter__(self):
        self._iter = iter(list(self._cursor))
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class AsyncCollection:
    def __init__(self, collection):
        self._c = collection

    def find(self, *args, **kwargs):
        return AsyncCursor(self._c.find(*args, **kwargs))

    async def find_one(self, *args, **kwargs):
        return self._c.find_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self._c.count_documents(*args, **kwargs)

    async def insert_one(self, *args, **kwargs):
        return self._c.insert_one(*args, **kwargs)

    async def insert_many(self, *args, **kwargs):
        return self._c.insert_many(*args, **kwargs)

    async def update_one(self, *args, **kwargs):
        return self._c.update_one(*args, **kwargs)

    async def update_many(self, *args, **kwargs):
        return self._c.update_many(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self._c.delete_one(*args, **kwargs)

    async def delete_many(self, *args, **kwargs):
        return self._c.delete_many(*args, **kwargs)

    async def bulk_write(self, operations, ordered=False):
        # mongomock 4.3 + 최신 pymongo 조합에서 UpdateOne._add_to_bulk 가
        # 'sort' kwarg 로 깨집니다. 라이브러리 비호환이므로 여기서 풀어 씁니다.
        for operation in operations:
            self._c.update_one(
                operation._filter, operation._doc, upsert=operation._upsert
            )
        return None

    async def find_one_and_update(self, *args, **kwargs):
        return self._c.find_one_and_update(*args, **kwargs)

    async def create_index(self, *args, **kwargs):
        return None


class AsyncDatabase:
    def __init__(self):
        self._db = mongomock.MongoClient().db

    def __getattr__(self, name):
        return AsyncCollection(self._db[name])

    async def command(self, *args, **kwargs):
        return {"ok": 1}


PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


async def main():
    from app.domain.priority import loader
    from app.domain.priority.router import router
    from fastapi import FastAPI

    database = AsyncDatabase()
    app = FastAPI()
    app.include_router(router)
    app.state.database = database

    print("[로더]")
    result = await loader.load_scores(database)
    check("적재 성공", result["district_count"] == 229, str(result["district_count"]))
    check("batch_id 가 입력 지문 기반", result["batch_id"].startswith("risk-v2-"), result["batch_id"])
    check("최초 적재는 직전 배치 없음", result["previous_batch_id"] is None)
    check("districts 229개", await database.districts.count_documents({}) == 229)
    check("admin_dongs 3557개(전국)", await database.admin_dongs.count_documents({}) == 3557,
          str(await database.admin_dongs.count_documents({})))

    # 배치 문서가 없어도 priority_meta 로 최신 배치를 찾아야 합니다.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        print("\n[GET /districts]")
        r = await client.get("/api/priority/districts")
        check("200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("total 229", body["total"] == 229, str(body.get("total")))
        check("1위가 rank 1", body["districts"][0]["rank"] == 1)
        check("meta 봉투 포함", body["meta"]["model_version"] == "risk-v2")
        # 지표 개수는 가중치 개정마다 바뀝니다(2차 개정에서 집중강우 중점관리구역
        # 추가로 6 → 7). 개수를 박는 대신 "쓰는 지표 전부에 한글명이 있다"는
        # 성질을 봅니다 — 프론트가 한글명을 하드코딩하지 않는 근거가 그것입니다.
        names = body["meta"]["display_names"]
        weighted = set(body["meta"]["occurrence_weights"]) | set(
            body["meta"]["impact"]["features"]
        )
        check("가중치 지표 전부에 한글명 있음", weighted <= set(names),
              str(sorted(weighted - set(names))))
        # 노후율 커버리지는 원본 판차에 따라 바뀝니다(2024판 155 → 2025판 171).
        # 특정 숫자를 박으면 원본을 갱신할 때마다 이 테스트가 깨지므로,
        # "일부만 관측된다"는 성질만 확인합니다. 전수(229)가 되면 결측 처리
        # 경로가 통째로 안 타므로 그때는 알아야 합니다.
        aged_coverage = body["meta"]["coverage"]["aged_ratio_25y"]
        check("coverage 노후율은 일부만 관측 (0 < n < 229)",
              0 < aged_coverage < 229, str(body["meta"]["coverage"]))
        check("coverage 콘크리트 229", body["meta"]["coverage"]["concrete_ratio"] == 229,
              str(body["meta"]["coverage"]["concrete_ratio"]))

        # 강수 트리거: 목록에도 실려야 화면이 "비까지 보면 몇 위"를 계산할 수
        # 있습니다. 불변식은 두 개 — 항상 원점수 이상이고, 배율 상한(1.2)을
        # 넘지 않습니다. 값을 박지 않으므로 강수가 갱신돼도 안 깨집니다.
        rows = body["districts"]
        triggered = [row for row in rows if row.get("triggered_score") is not None]
        check("목록에 triggered_score 실림", len(triggered) == len(rows),
              f"{len(triggered)}/{len(rows)}")
        check(
            "강수 반영 점수는 원점수 이상 (배율 ≥ 1.0)",
            all(row["triggered_score"] >= row["score"] - 1e-9 for row in triggered),
        )
        span = body["meta"]["rain_trigger"]["span"]
        check(
            f"강수 배율 상한 준수 (≤ 1 + {span})",
            all(
                row["triggered_score"] <= row["score"] * (1 + span) + 1e-9
                for row in triggered
            ),
        )
        check(
            "강수는 순위·등급에 반영되지 않음 (meta 선언)",
            body["meta"]["rain_trigger"]["applies_to_score"] is False,
            str(body["meta"]["rain_trigger"]),
        )
        # 순위는 강수 없는 score 기준이어야 합니다 — triggered_score 로
        # 정렬한 순서와 rank 가 같으면 강수가 순위에 새어든 것입니다.
        by_triggered = sorted(triggered, key=lambda row: -row["triggered_score"])
        check(
            "rank 는 강수 반영 순서와 다름 (강수가 순위에 새지 않음)",
            [row["sgg_code"] for row in by_triggered]
            != [row["sgg_code"] for row in sorted(rows, key=lambda r: r["rank"])],
        )

        r = await client.get("/api/priority/districts", params={"sido": "29"})
        check("시도 필터 광주 5개", r.json()["total"] == 5, str(r.json()["total"]))

        r = await client.get("/api/priority/districts", params={"grade": 5})
        check("5등급 23개", r.json()["total"] == 23, str(r.json()["total"]))

        r = await client.get("/api/priority/districts", params={"limit": 3, "offset": 2})
        check("페이징 동작", [d["rank"] for d in r.json()["districts"]] == [3, 4, 5],
              str([d["rank"] for d in r.json()["districts"]]))

        print("\n[GET /districts/{district}]")
        r = await client.get("/api/priority/districts/29140")
        check("5자리 코드로 조회", r.status_code == 200, r.text[:200])
        detail = r.json()
        check("광주 서구", detail["full_name"] == "광주광역시 서구", detail.get("full_name",""))
        check("contributions 있음", len(detail["contributions"]) == 5, str(len(detail["contributions"])))
        s = sum(detail["contributions"].values())
        check("기여도 합 == base_risk", abs(s - detail["features"]["base_risk"]) < 1e-12,
              f"{s} vs {detail['features']['base_risk']}")
        # 침하는 2026-08-03 실키 수집으로 결측이 해소됐습니다. 지표별 결측 여부를
        # 박아두면 데이터가 채워질 때마다 깨지므로, missing_features 가 실제
        # 지표 이름만 담는지(형식)와 결측 지표가 기여도에서 빠지는지만 봅니다.
        check("missing_features 는 지표 이름만",
              set(detail["missing_features"]) <= set(detail["display_names"]),
              str(detail["missing_features"]))
        check("display_names 동봉", "aged_ratio_25y" in detail["display_names"])

        did = detail["district_id"]
        r2 = await client.get(f"/api/priority/districts/{did}")
        check("int id 로도 조회", r2.status_code == 200 and r2.json()["sgg_code"] == "29140",
              r2.text[:160])

        r = await client.get("/api/priority/districts/99999")
        check("없는 코드 404", r.status_code == 404, str(r.status_code))
        r = await client.get("/api/priority/districts/abc")
        check("잘못된 형식 400", r.status_code == 400, str(r.status_code))

        print("\n[GET /districts/geojson]")
        r = await client.get("/api/priority/districts/geojson")
        check("200", r.status_code == 200, str(r.status_code))
        etag = r.headers.get("etag")
        check("ETag 헤더", bool(etag), str(dict(r.headers)))
        check("Cache-Control", "max-age" in r.headers.get("cache-control", ""))
        r = await client.get("/api/priority/districts/geojson",
                             headers={"If-None-Match": etag})
        check("재요청 304", r.status_code == 304, str(r.status_code))

        print("\n[GET /sido/geojson]")
        r = await client.get("/api/priority/sido/geojson")
        check("200", r.status_code == 200, str(r.status_code))
        sido_geo = r.json()
        # 개수를 박지 않습니다 — 폴리곤이 있고 전부 시도 코드(2자리)인지만.
        check("피처 존재", len(sido_geo.get("features", [])) > 0)
        check(
            "시도 코드 2자리",
            all(len(f["properties"]["code"]) == 2 for f in sido_geo["features"]),
        )
        check(
            "폴리곤 지오메트리",
            all(
                f["geometry"]["type"] in ("Polygon", "MultiPolygon")
                for f in sido_geo["features"]
            ),
        )
        sido_etag = r.headers.get("etag", "")
        r = await client.get("/api/priority/sido/geojson",
                             headers={"If-None-Match": sido_etag})
        check("재요청 304", r.status_code == 304, str(r.status_code))

        print("\n[GET /dongs/geojson]")
        r = await client.get("/api/priority/dongs/geojson", params={"sgg": "29155"})
        check("200", r.status_code == 200, str(r.status_code))
        dongs_geo = r.json()
        sgg_set = {f["properties"]["sgg_code"] for f in dongs_geo["features"]}
        check("sgg 필터 동작", sgg_set == {"29155"}, str(sgg_set))
        check("서구 읍면동 17개", len(dongs_geo["features"]) == 17,
              str(len(dongs_geo["features"])))
        check("adm_code 속성", "adm_code" in dongs_geo["features"][0]["properties"])
        dong_etag = r.headers.get("etag", "")
        check("sgg별 ETag 구분", dong_etag.endswith('-29155"'), dong_etag)
        r = await client.get("/api/priority/dongs/geojson",
                             params={"sgg": "29155"},
                             headers={"If-None-Match": dong_etag})
        check("재요청 304", r.status_code == 304, str(r.status_code))
        r = await client.get("/api/priority/dongs/geojson", params={"sgg": "00000"})
        check("없는 시군구는 빈 목록", r.json()["features"] == [], r.text[:80])

        print("\n[GET /sido]")
        r = await client.get("/api/priority/sido")
        check("17개 시도", len(r.json()) == 17, str(len(r.json())))
        check("1위부터 정렬", r.json()[0]["rank"] == 1)

        print("\n[GET /districts/{id}/dongs]")
        r = await client.get("/api/priority/districts/29140/dongs")
        check("200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("광주 서구 읍면동 목록", body["total"] > 0, str(body["total"]))
        # 점수 필드가 애초에 없어야 합니다 — Level 2 는 점수화하지 않습니다.
        check(
            "동 응답에 점수 필드 없음",
            all("score" not in dong and "grade" not in dong for dong in body["dongs"]),
        )

        print("\n[GET /history]")
        r = await client.get("/api/priority/history", params={"district": "29140"})
        check("200", r.status_code == 200, r.text[:200])
        check("이력 1건(최초)", len(r.json()["points"]) == 1, str(len(r.json()["points"])))
        check("점검 이력 없으면 빈 목록", r.json()["inspections"] == [],
              str(r.json()["inspections"]))

        # 실측 비교: 이 구역에서 세션이 돌고 결함이 나왔다면 나란히 실려야 합니다.
        seogu = await database.districts.find_one({"sgg_code": "29140"})
        await database.sessions.insert_one(
            {"id": 9001, "district_id": seogu["id"], "status": "COMPLETED",
             "started_at": "2026-08-01T10:00:00"}
        )
        await database.defects.insert_many(
            [{"id": 1, "session_id": 9001}, {"id": 2, "session_id": 9001}]
        )
        r = await client.get("/api/priority/history", params={"district": "29140"})
        inspections = r.json()["inspections"]
        check("세션 1건 조인", len(inspections) == 1, str(inspections))
        check("결함 2건 집계", inspections[0]["defect_count"] == 2, str(inspections))
        await database.sessions.delete_many({"id": 9001})
        await database.defects.delete_many({"session_id": 9001})

        print("\n[POST /recalculate]")
        r = await client.post("/api/priority/recalculate", json={"reason": "테스트"})
        check("202 Accepted", r.status_code == 202, str(r.status_code))
        accepted = r.json()
        check("batch_id 반환", "batch_id" in accepted, str(accepted))
        check("status_url 반환", accepted["status_url"].startswith("/api/priority/batches/"))
        check("status QUEUED", accepted["status"] == "QUEUED")

        # BackgroundTasks 가 응답 후 실행되므로 여기서는 이미 완료 상태여야 합니다.
        r = await client.get("/api/priority/batches")
        batches = r.json()
        check("배치 기록 생성", len(batches) == 1, str(len(batches)))
        check("완료 상태", batches[0]["status"] == "COMPLETED",
              f"{batches[0]['status']} err={batches[0].get('error')}")
        # 문서의 batch_id(임시 id)는 유지되어야 합니다. 교체하면 202 의
        # status_url 이 완료 순간 404 가 되고, 같은 입력 재적재가 unique
        # 충돌로 RUNNING 에 갇힙니다. 엔진 id 는 engine_batch_id 로만.
        check("임시 batch_id 유지", batches[0]["batch_id"].startswith("pending-"),
              batches[0]["batch_id"])
        check("engine_batch_id 기록",
              (batches[0].get("engine_batch_id") or "").startswith("risk-v2-"),
              str(batches[0].get("engine_batch_id")))
        check("입력 지문 기록", bool(batches[0].get("input_digest")))

        r = await client.get(f"/api/priority/batches/{batches[0]['batch_id']}")
        check("배치 단건 조회 (임시 id)", r.status_code == 200, str(r.status_code))
        r = await client.get(
            f"/api/priority/batches/{batches[0]['engine_batch_id']}"
        )
        check("배치 단건 조회 (엔진 id)", r.status_code == 200, str(r.status_code))
        r = await client.get("/api/priority/batches/pending-없는것")
        check("없는 배치 404", r.status_code == 404, str(r.status_code))

        print("\n[같은 입력 재산정]")
        # 입력이 안 바뀌면 엔진 batch_id 가 같습니다. 두 번째 배치도 완료까지
        # 가야 합니다 (버그였던 케이스: unique 충돌로 RUNNING 에 갇힘).
        r = await client.post(
            "/api/priority/recalculate", json={"reason": "동일 입력 재실행"}
        )
        check("두 번째 202", r.status_code == 202, str(r.status_code))
        second_id = r.json()["batch_id"]
        r = await client.get(f"/api/priority/batches/{second_id}")
        second = r.json()
        check("두 번째도 완료", second["status"] == "COMPLETED",
              f"{second['status']} err={second.get('error')}")
        check("같은 엔진 id", second.get("engine_batch_id") == batches[0]["engine_batch_id"],
              str(second.get("engine_batch_id")))
        # 입력이 같으면 순위도 같습니다. 예전에는 입력이 같은 배치를 건너뛰고 그
        # 이전과 비교해서, 아무것도 안 바뀐 적재에도 변동 수가 나왔습니다.
        check("입력 변화 없음으로 표시", second.get("input_changed") is False,
              str(second.get("input_changed")))
        check("변동 0개로 보고", second.get("changed_ranks") == 0,
              str(second.get("changed_ranks")))

        print("\n[동시 실행 방지]")
        # 진행 중 판정은 잠금 문서로 합니다. 잠금이 살아 있고(배치가 아직
        # QUEUED/RUNNING) 상한을 안 넘겼으면 409.
        from datetime import datetime, timezone

        await database.priority_batches.insert_one(
            {"batch_id": "pending-x", "status": "RUNNING",
             "queued_at": batches[0]["queued_at"], "run_engine": False}
        )
        await database.priority_locks.insert_one(
            {"_id": "recalculate", "batch_id": "pending-x",
             "locked_at": datetime.now(timezone.utc)}
        )
        r = await client.post("/api/priority/recalculate", json={})
        check("진행 중이면 409", r.status_code == 409, str(r.status_code))
        check(
            "409 본문에 진행 중 배치 id",
            "pending-x" in r.json().get("detail", ""),
            r.text[:120],
        )

        print("\n[서버 재시작 복구]")
        # 서버가 죽으면 잠금·RUNNING 배치가 남습니다. 잠금이 가리키는 배치를
        # FAILED 로 바꿔 "이미 끝났는데 잠금만 남은" 재시작 직후를 재현합니다.
        # 예전에는 복구 로직이 없어 이 상태에서 모든 재산정이 영구 409 였습니다.
        await database.priority_batches.update_one(
            {"batch_id": "pending-x"}, {"$set": {"status": "FAILED"}}
        )
        r = await client.post("/api/priority/recalculate", json={"reason": "복구 확인"})
        check("죽은 잠금 회수 후 202", r.status_code == 202, r.text[:120])
        recovered_id = r.json()["batch_id"]
        r = await client.get(f"/api/priority/batches/{recovered_id}")
        check("회수 후 배치 완료", r.json()["status"] == "COMPLETED",
              f"{r.json()['status']} err={r.json().get('error')}")
        check(
            "잠금이 풀려 있음 (완료 후)",
            await database.priority_locks.find_one({"_id": "recalculate"}) is None,
        )

        # RUNNING 고아 문서(잠금 없음)는 다음 재산정이 FAILED 로 닫습니다.
        await database.priority_batches.insert_one(
            {"batch_id": "pending-orphan", "status": "RUNNING",
             "queued_at": batches[0]["queued_at"], "run_engine": False}
        )
        r = await client.post("/api/priority/recalculate", json={})
        check("고아 RUNNING 이 있어도 202", r.status_code == 202, str(r.status_code))
        orphan = await database.priority_batches.find_one({"batch_id": "pending-orphan"})
        check("고아 배치가 FAILED 로 닫힘", orphan["status"] == "FAILED",
              str(orphan["status"]))
        check("닫힌 사유 기록", "중단" in (orphan.get("error") or ""),
              str(orphan.get("error")))
        await database.priority_batches.delete_many(
            {"batch_id": {"$in": ["pending-x", "pending-orphan"]}}
        )

        print("\n[재적재 멱등성]")
        before = await database.priority_scores.count_documents({})
        await loader.load_scores(database)
        after = await database.priority_scores.count_documents({})
        check("같은 배치 재적재 시 중복 없음", before == after == 229, f"{before} → {after}")

        print("\n[batch_id 명시 조회]")
        engine_id = batches[0]["engine_batch_id"]
        r = await client.get("/api/priority/districts", params={"batch_id": engine_id})
        check("과거 배치 명시 조회 200", r.status_code == 200 and r.json()["total"] == 229,
              r.text[:120])
        r = await client.get("/api/priority/districts", params={"batch_id": "없는배치"})
        check("없는 배치 404 (빈 200 아님)", r.status_code == 404, str(r.status_code))

        print("\n[읍면동 침하 카운트]")
        # 적재된 건별 레코드 중 행정동 매칭이 있는 곳을 하나 골라, 그 시군구의
        # 동 목록 카운트가 컬렉션과 일치하는지 봅니다. 예전에는 이 컬렉션에
        # 쓰는 코드가 없어 화면이 항상 0건이었습니다.
        loaded = await database.subsidence_records.count_documents({})
        check("건별 침하 레코드 적재됨", loaded > 0, str(loaded))
        sample = await database.subsidence_records.find_one({"adm_code": {"$ne": None}})
        check("행정동 매칭 건 존재", sample is not None)
        if sample:
            r = await client.get(f"/api/priority/districts/{sample['sgg_code']}/dongs")
            rows = r.json()["dongs"]
            shown = sum(row["subsidence_count"] for row in rows)
            stored = await database.subsidence_records.count_documents(
                {"sgg_code": sample["sgg_code"], "adm_code": {"$ne": None}}
            )
            check("동 카운트 = 컬렉션의 매칭 건수", shown == stored > 0,
                  f"{shown} vs {stored}")
            check(
                "하수 원인 ≤ 전체 (동마다)",
                all(row["sewer_cause_count"] <= row["subsidence_count"] for row in rows),
            )
            # 동을 특정하지 못한 건을 응답이 스스로 밝혀야 화면의 "이력 없음"이
            # 단정이 되지 않습니다.
            unlocated = await database.subsidence_records.count_documents(
                {"sgg_code": sample["sgg_code"], "adm_code": None}
            )
            check(
                "동 미확인 건수 보고 (매칭 + 미확인 = 전체)",
                r.json()["unlocated_count"] == unlocated
                and shown + unlocated
                == await database.subsidence_records.count_documents(
                    {"sgg_code": sample["sgg_code"]}
                ),
                f"응답 {r.json().get('unlocated_count')} vs 컬렉션 {unlocated}",
            )

        print("\n[geojson 나머지 분기]")
        r = await client.get("/api/priority/dongs/geojson")
        check("sgg 없이 전체 파일 200", r.status_code == 200, str(r.status_code))
        check("전체 파일도 geo+json", "geo+json" in r.headers.get("content-type", ""),
              r.headers.get("content-type", ""))
        multi = f'W/{etag}, "other-etag"'  # 헤더는 ASCII 만 허용됩니다
        r = await client.get("/api/priority/districts/geojson",
                             headers={"If-None-Match": multi})
        check("약한 검증자·다중 ETag 도 304", r.status_code == 304, str(r.status_code))

        original_name = loader.DONGS_GEOJSON_FILE
        loader.DONGS_GEOJSON_FILE = "없는파일.geojson"
        r = await client.get("/api/priority/dongs/geojson")
        check("경계 파일이 없으면 404 + 안내", r.status_code == 404
              and "build_boundaries" in r.json()["detail"], r.text[:120])
        loader.DONGS_GEOJSON_FILE = original_name

        print("\n[실패 경로 — FAILED 배치]")
        # 산출물 디렉터리를 잠깐 치워 적재 실패를 유도합니다. 실패해도 배치
        # 문서에 사유가 남고 잠금이 풀려야 다음 재산정이 가능합니다.
        original_data = loader.ENGINE_DATA
        loader.ENGINE_DATA = original_data / "없는디렉터리"
        r = await client.post("/api/priority/recalculate", json={"reason": "실패 유도"})
        check("실패해도 202 로 접수", r.status_code == 202, str(r.status_code))
        failed_id = r.json()["batch_id"]
        loader.ENGINE_DATA = original_data
        r = await client.get(f"/api/priority/batches/{failed_id}")
        check("배치가 FAILED 로 기록", r.json()["status"] == "FAILED", r.text[:120])
        check("실패 사유 기록", bool(r.json().get("error")), str(r.json().get("error")))
        check(
            "실패 후 잠금 해제",
            await database.priority_locks.find_one({"_id": "recalculate"}) is None,
        )
        r = await client.post("/api/priority/recalculate", json={"reason": "실패 후 재시도"})
        check("실패 후 재산정 가능 (409 아님)", r.status_code == 202, str(r.status_code))

        print("\n[스크립트 직접 적재 (load_priority.py 경로)]")
        # 2026-08-03 실제 장애: 이 기록이 없으면 과거 /recalculate 배치가 계속
        # 최신으로 뽑혀 새 적재본이 영구히 무시됩니다. 회귀하면 여기서 잡힙니다.
        direct = await loader.load_scores(database)
        await loader.register_direct_load(database, direct)
        registered = await database.priority_batches.find_one(
            {"batch_id": direct["batch_id"]}
        )
        check("배치 기록 생성 (엔진 id 그대로)", registered is not None)
        check("COMPLETED 로 기록", registered["status"] == "COMPLETED")
        check("스크립트 경로 표시", registered.get("source") == "script",
              str(registered.get("source")))
        check("input_changed 기록 (경로 간 필드 일치)",
              registered.get("input_changed") is False,
              str(registered.get("input_changed")))
        r = await client.get("/api/priority/districts")
        check("직접 적재 후에도 최신 배치 조회 정상", r.json()["total"] == 229,
              str(r.json().get("total")))

    print("\n" + "=" * 56)
    print(f"통과 {PASS}개 / 실패 {FAIL}개")
    return FAIL


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
