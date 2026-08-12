# backend

```text
backend/
├─ cloud_api/         # FastAPI 서버 (배포되는 것)
├─ priority_engine/   # 우선순위 점수 산출 파이프라인 (오프라인 실행)
└─ .dockerignore      # 빌드 컨텍스트 루트가 backend/ 이므로 여기에 있다
```

`cloud_api` 는 컨테이너로 배포되고, `priority_engine` 은 개발자가 필요할 때 돌려서
`priority_engine/data/` 에 산출물을 남기는 오프라인 도구다. 서버는 그 산출물만 읽는다.

## 로컬 실행

```bash
cd backend/cloud_api
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 값 채우기
uvicorn app.main:app --reload --port 8001
```

포트를 8001 로 두는 이유는 프론트 개발 서버의 프록시(`frontend/vite.config.ts` 의
`/backend-api/priority` → `http://127.0.0.1:8001`)가 그 번호를 본다. 배포에서도
호스트가 8001 을 보므로 번호가 같다(컨테이너 내부는 8000).

설정은 `app/core/config.py` 의 `Settings` 가 cwd 의 `.env` 를 읽는다. 키 목록은
`cloud_api/.env.example`. **`Settings` 는 `extra="ignore"` 라 오타난 키나 없어진 키를
조용히 무시한다** — 값이 안 먹으면 먼저 키 이름을 의심한다.

`PRIORITY_DATA_DIR` 을 비워 두면 저장소 레이아웃(`backend/priority_engine/data`)을
쓴다. 배포 이미지에서는 Dockerfile 이 산출물을 굽고 이 값을 직접 넣는다.

## 배포 이미지

`cloud_api/Dockerfile` 을 쓰지만 **빌드 컨텍스트는 `backend/` 다**
(`cloud_api/` 가 아니다). Dockerfile 이 `priority_engine/data` 를 이미지에 굽는데
docker 는 컨텍스트 밖을 `COPY` 할 수 없어서 한 단계 올렸다. 그래서

* Dockerfile 안의 경로는 모두 `cloud_api/` 로 시작한다,
* `.dockerignore` 는 `backend/.dockerignore` 만 유효하다 (컨텍스트 루트에서만 읽힌다).

산출물을 바인드 마운트하지 않고 굽는 이유, compose·Jenkins 구성, 롤백은
[../infra/README.md](../infra/README.md) 에 있다.

빌드 인자 `GIT_COMMIT` 은 `/api/health` 의 `commit` 으로 노출된다. 배포된 커밋을
밖에서 확인하는 수단이고, 빼먹으면 `unknown` 이 찍혀 배포 검증이 실패한다.

```bash
curl -s https://i15c102.p.ssafy.io/api/health
# {"status":"ok","server":"connected","mongodb":"connected","commit":"3ead6f6"}
```

## 데이터 마이그레이션

`districts` 컬렉션의 PK 필드가 `district_id` → `id` 로 바뀌었다. 구버전 데이터가 든
DB 는 새 코드가 유니크 인덱스를 만들다 `dup key: { id: null }` 로 죽어서 **API 가
기동 자체를 못 한다.** 한 번만 돌리면 되고, 이미 이관된 DB 에서는 아무 일도 하지 않는다.

```bash
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py --dry-run
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py
```

`districts_backup_pre_id_rename` 컬렉션에 백업을 남긴다. 확인 후 지워도 된다.

## 알려진 미해결 항목

* `admin_dongs.geojson` 이 없어 `/api/priority/dongs/geojson` 이 404 다. 원래부터
  그랬고 읍면동 경계만 안 나온다. 원본 데이터(`priority_engine/raw/`)가 저장소에
  없어서 재생성도 못 한다 — 자세한 내용은 [../infra/README.md](../infra/README.md).
* `cloud_api/.env.example` 말미에 적어 둔 대로, 서버 `.env` 의 `UPLOAD_TOKEN` /
  `MEDIA_URL_PREFIX` / `MAX_UPLOAD_BYTES` 는 코드에서 읽지 않는다.
  `MAX_UPLOAD_BYTES` 는 `app/domain/media/router.py` 에 10MB 로 하드코딩
