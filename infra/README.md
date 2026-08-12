# 배포 구조 (Docker · Jenkins)

## 이번 개편 요약 (2026-08-03)

시작점은 「배포된 사이트가 develop 을 반영하지 않는다」였고, 원인이 한 군데가 아니라
배포 경로 전체에 흩어져 있어서 다음을 정리했다. 각 항목의 근거는 아래 해당 절에 있다.

* **배포 정의를 한 곳으로.** Jenkinsfile 이 3개(루트, `backend/`, `scripts/deploy.sh`),
  compose 가 2개(`docker/`, `infra/docker/`) 있어서 어느 것이 실제로 도는지 알 수 없었다.
  지금은 `infra/jenkins/Jenkinsfile` + `infra/docker/docker-compose.prod.yml` 뿐이고
  나머지는 삭제했다.
* **바인드 마운트를 전부 없앴다.** Jenkins 에서 배포하면 빈 디렉터리가 붙어 경계
  GeoJSON 이 404 가 됐다 → `priority_engine/data` 를 이미지에 굽는다.
  이 때문에 cloud-api 빌드 컨텍스트가 `backend/` 로 올라갔다. → [설계 원칙](#설계-원칙-compose-에-호스트-경로가-없다)
* **비밀값을 `infra/docker/.env` 한 파일로.** MongoDB 자격증명이 compose 에
  하드코딩돼 있던 것을 `MONGO_USER`/`MONGO_PASSWORD` 로 빼고, 비어 있으면 배포가
  시작 전에 멈추게 했다. → [설정과 비밀값](#설정과-비밀값)
* **배포된 커밋이 밖에서 보인다.** `/build-info.json`, `/api/health` 의 `commit`.
  개편 시점에 배포본이 develop 보다 17 커밋 밀려 있었는데 아무도 몰랐다.
  → [배포 버전 확인](#배포-버전-확인)
* **Verify Deploy 를 공개 URL 기준으로.** 기존 헬스체크(`curl 127.0.0.1:3000`)는
  Jenkins 컨테이너 안에서 도니 **어떤 경우에도 성공할 수 없었다.** → [Verify Deploy](#verify-deploy)
* **커밋별 이미지 태깅.** 재빌드 없이 되돌린다. → [롤백](#롤백)
* **프론트 포트 3000 → 8002**, `frontend/Dockerfile` 복구(있어야 할 파일이 없었다),
  호스트 nginx 예시도 함께 맞췄다.
* **`districts` PK 필드 마이그레이션**(`district_id` → `id`). 구버전 데이터에서는
  API 가 기동조차 못 했다. → [데이터 마이그레이션](#데이터-마이그레이션)
* **우선순위 지도 폴리곤이 배포에서만 안 그려지던 버그** 수정. 원인이 프론트
  빌드·서빙 쪽이라 자세한 내용은 [../frontend/README.md](../frontend/README.md) 에 있다.

호스트 쪽 변경 하나는 git 에 남지 않으므로 여기 적어 둔다: 실서버 nginx 의
`location /mytest/`(→ 8100) 블록을 제거하고 해당 컨테이너를 내렸다. 백업은
`/etc/nginx/sites-available/default.bak-20260803-mytest`.

## 전체 아키텍처

```text
외부 Nginx (EC2, SSL 종료 — infra/nginx/i15c102.conf.example)
├─ /        → 127.0.0.1:8002  (frontend 컨테이너: React 19 + Vite, nginx:alpine)
├─ /api/    → 127.0.0.1:8001  (cloud-api 컨테이너: FastAPI, uvicorn :8000)
└─ /media/  → 127.0.0.1:8001  (cloud-api 컨테이너, 결함 이미지)

cloud-api ── depends_on(healthy) ──> mongodb (내부 네트워크, 포트 미노출)
```

`/media/` 라우트는 반드시 있어야 한다. 결함 이미지 URL은 `/api/media/...`가 아니라
`/media/defects/...`다 (FastAPI가 `/api` 밖에 `StaticFiles`로 마운트, 프론트의
`resolveMediaUrl`도 `${origin}/media/...`를 생성). 이 라우트가 빠지면 API는
정상인데 결함 사진만 전부 404가 나서 배포 직후엔 잘 드러나지 않는다.

**프론트 포트는 8002 로 고정이다.** 호스트 nginx 가 그 번호로 프록시하므로,
compose 에서 이 번호를 바꾸면 컨테이너는 멀쩡한데 사이트 전체가 502 가 된다.

## 설계 원칙: compose 에 호스트 경로가 없다

이 compose 파일에는 바인드 마운트가 **하나도 없다.** 상대 경로 마운트
(`../../backend/...`)는 compose 파일이 있는 디렉터리 기준으로 풀리는데, Jenkins
워크스페이스는 도커 볼륨 안이라 호스트 파일시스템에 존재하지 않는다. 그런데
마운트를 실제로 붙이는 것은 호스트의 도커 데몬이라, Jenkins 에서 배포하면 데몬이
그 경로를 못 찾아 **빈 디렉터리를 만들어 붙인다.**

2026-08-03 에 이 문제로 우선순위 경계 GeoJSON 이 404 가 나서 지도가
「경계·순위 데이터를 불러오는 중…」에서 멈췄다. 그래서 지금은

* git 이 추적하는 데이터(`priority_engine/data`, 5.8MB)는 **이미지에 굽고**,
* 나머지 상태는 **named volume**(`mongodb_data`, `media_data`)으로만 다룬다.

덕분에 호스트에서 실행하든 Jenkins 에서 실행하든 결과가 같다. 마운트를 새로
추가하려면 이 원칙을 깨는 것이니, 절대 경로(`/etc/letsencrypt` 같은)만 쓰거나
이미지에 굽는 쪽을 먼저 검토한다.

## 폴더 구조

```text
frontend/
├─ Dockerfile        # multi-stage: node:20-alpine 빌드 → nginx:alpine 서빙
├─ .dockerignore
├─ nginx.conf         # 정적 서빙 + SPA 폴백 + 캐시 정책 + client_max_body_size 12m
│                     #   + /maplibre/ 의 .mjs MIME (없으면 지도 폴리곤이 안 그려진다)
└─ (기존 프론트 코드)

backend/
├─ .dockerignore      # 컨텍스트 루트가 backend/ 이므로 여기에 둔다
├─ cloud_api/
│  ├─ Dockerfile      # 컨텍스트는 backend/ (아래 설명)
│  └─ (기존 FastAPI 코드)
└─ priority_engine/
   └─ data/           # 엔진 산출물 — 이미지에 구워진다

infra/
├─ docker/docker-compose.prod.yml
├─ docker/.env.example
├─ jenkins/Jenkinsfile
└─ nginx/i15c102.conf.example
```

Dockerfile은 각 소스 폴더 옆에 둔다. `infra/`로 옮기면 빌드 컨텍스트를 저장소
루트로 잡아야 하고, 그러면 `ai/3d_gsplat` 등 무관한 대용량 디렉터리까지 빌드
컨텍스트로 전송

단, **cloud-api 의 빌드 컨텍스트는 `backend/` 다** (`backend/cloud_api/` 가 아니다).
Dockerfile 이 `priority_engine/data` 를 이미지에 굽는데 docker 는 컨텍스트 밖을
`COPY` 할 수 없어서 한 단계 올렸다. 그래서 Dockerfile 안의 경로는 모두
`cloud_api/` 로 시작하고, `.dockerignore` 는 `backend/.dockerignore` 가 유효하다
(컨텍스트 루트에서만 읽힌다). `backend/` 전체가 8.4MB 라 컨텍스트 부담은 없다.

## Docker Compose 서비스 (`infra/docker/docker-compose.prod.yml`)

| 서비스 | 빌드 컨텍스트 | 포트 | 비고 |
|---|---|---|---|
| `frontend` | `../../frontend` | `127.0.0.1:8002:80` | 정적 서빙. `GIT_COMMIT` 빌드 인자 |
| `cloud-api` | `../../backend` (`dockerfile: cloud_api/Dockerfile`) | `127.0.0.1:8001:8000` | `env_file: .env`, `GIT_COMMIT` 빌드 인자 |
| `mongodb` | (이미지 `mongo:7.0`) | 미노출 | `depends_on` healthy 대기 |

볼륨은 두 개뿐이다: `mongodb_data:/data/db`, `media_data:/app/media`(결함 이미지).
보고서 PDF는 요청 시 메모리에서 렌더 후 바로 반환되고, LLM 요약은 MongoDB의
`reports` 문서에 저장되므로 별도 볼륨이 필요 없다.

## 설정과 비밀값

**`infra/docker/.env` 한 파일에 전부 모여 있다** (git 추적 안 함, 예시는
`infra/docker/.env.example`). compose 의 `${...}` 치환과 cloud-api 의 `env_file`
이 같은 파일을 읽으므로 비밀값이 한 군데에만 존재하고, Jenkins 는 credential
하나만 이 경로에 심으면 된다.

MongoDB 자격증명은 `MONGO_USER` / `MONGO_PASSWORD` 로만 존재하고, `MONGODB_URL` 은
compose 가 그 둘로 조립한다. 값이 비어 있으면 compose 가 배포를 시작하기 전에
멈춘다 — 빈 값으로 조용히 떠서 인증이 깨진 채 도는 것보다 낫다.

```bash
cp infra/docker/.env.example infra/docker/.env
# MONGO_USER / MONGO_PASSWORD / LLM_API_KEY 등 실제 값 입력
```

`MONGO_USER` / `MONGO_PASSWORD` 는 `mongodb_data` 볼륨이 **처음 만들어질 때만**
계정 생성에 쓰인다. 이미 데이터가 있는 볼륨에서 값만 바꾸면 계정은 그대로인데
헬스체크와 접속만 깨진다.

로컬에서 cloud_api 를 직접 띄울 때는 `backend/cloud_api/.env` 를 쓴다
(Settings 가 cwd 의 `.env` 를 읽는다). 키 목록은 `backend/cloud_api/.env.example`.

### 서버 실행 명령

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) \
  docker-compose -f infra/docker/docker-compose.prod.yml up -d --build --remove-orphans
```

`GIT_COMMIT` 을 빼먹으면 `build-info.json` 과 `/api/health` 에 `unknown` 이 찍히고
배포 검증이 실패한다.

## 배포 버전 확인

배포된 커밋을 컨테이너 밖에서 볼 수 있다. 이것이 「배포했는데 반영이 안 됐다」를
판정하는 근거다.

```bash
curl -s https://i15c102.p.ssafy.io/build-info.json   # {"commit":"...","built_at":"..."}
curl -s https://i15c102.p.ssafy.io/api/health        # {"status":"ok",...,"commit":"..."}
```

두 값이 `git rev-parse --short HEAD` 와 다르면 배포가 뒤처진 것이다. 2026-08-03 에
배포본이 develop 보다 17 커밋 밀려 있었는데 아무도 몰랐던 일이 있어서 넣었다.

## Jenkins

### Script Path 변경 (수동 작업 필요)

`backend/Jenkinsfile` → `infra/jenkins/Jenkinsfile`

Jenkins Job 설정(Pipeline → Definition → Script Path)에서 **직접 경로를 바꿔야
한다.** 파일만 옮기고 Job 설정을 그대로 두면 파이프라인이 옛 경로를 찾다가 실패한다.

### 파이프라인 단계

1. Checkout — 배포 대상 커밋(`GIT_COMMIT_SHORT`) 확정
2. Frontend Verify — `npm ci` → `npm run lint` → `npm run test`
3. Setup Python / Backend Sanity Check — venv + 의존성 + `compileall` + import 검사
4. (develop) Deploy — credential 로 `infra/docker/.env` 생성 후 `up -d --build --remove-orphans`
5. (develop) Verify Deploy — 아래 참고

`--remove-orphans` 는 이 compose 파일이 `sewer-cloud` 프로젝트를 단독으로 소유하기
때문에 안전하다. compose 파일이 둘 이상 같은 프로젝트 이름을 쓰면 서로의 컨테이너를
지우게 되니 그때는 반드시 빼야 한다.

### 필요한 Jenkins Credentials

| ID | 종류 | 용도 |
|---|---|---|
| `cloud-api-env-file` | Secret file | `infra/docker/.env` 전체 내용 (`infra/docker/.env.example` 의 키를 채운 파일). **`MONGO_USER` / `MONGO_PASSWORD` 가 반드시 포함되어야 한다** |

이 credential 의 내용이 바뀌었다. 예전에는 `backend/cloud_api/.env` 였고 Mongo
자격증명이 없었다. 갱신하지 않으면 compose 가 `MONGO_USER` 없음으로 멈춘다.

### Verify Deploy

배포 검증을 `127.0.0.1` 로 하면 안 된다 — **Jenkins 컨테이너의 localhost 는
호스트가 아니다.** 예전 헬스체크(`curl http://127.0.0.1:3000/`)는 어떤 경우에도
성공할 수 없었다. 공개 URL 로 확인한다.

```text
https://i15c102.p.ssafy.io/api/health                        커밋 일치
https://i15c102.p.ssafy.io/build-info.json                   커밋 일치
https://i15c102.p.ssafy.io/api/priority/districts/geojson    200  (구운 산출물 확인)
https://i15c102.p.ssafy.io/{/, /api/health, /docs}           200  (호스트 nginx 경유)
```

`districts/geojson` 을 넣은 이유는 이미지에 구운 엔진 산출물이 빠지면 여기서만
404 가 나기 때문이다. 나머지 API 는 전부 정상으로 보인다.

## 롤백

이미지가 **커밋별로 태깅된다** (`sewer-cloud-cloud-api:<커밋>`). compose 의
`image:` 필드가 `${GIT_COMMIT}` 을 쓰기 때문에 `up -d --build` 가 커밋 태그로
빌드하고, 이전 커밋의 이미지가 로컬에 그대로 남는다. 그래서 되돌릴 때 다시
빌드하지 않는다.

```bash
docker images | grep sewer-cloud            # 되돌릴 커밋 확인
GIT_COMMIT=<이전커밋> docker-compose -f infra/docker/docker-compose.prod.yml up -d --no-build
```

`--no-build` 를 꼭 넣는다. 빼면 현재 워크스페이스 소스로 그 태그를 다시 만들어서
롤백이 아니라 재배포가 된다.

되돌린 뒤에는 저장소도 맞춰야 한다. 그대로 두면 다음 빌드가 다시 문제된 커밋을
배포하고, `Verify Deploy` 는 배포본과 HEAD 가 어긋나 실패한다. `git revert` 로
정리한다.

태깅을 compose 쪽에서 하는 이유는 Jenkins 컨테이너에 `docker` CLI 가 없어
(`docker-compose` 만 있다) `docker tag` 를 쓸 수 없기 때문이다.

수동으로 태깅할 일이 있으면 **이미지 ID(`docker image inspect -f '{{.Id}}'`)로
태깅하면 안 된다** — containerd 이미지 저장소에서 그 값은 매니페스트 리스트
다이제스트라 `docker tag` 가 `No such image` 로 거부한다 (2026-08-03 에 실제로
롤백이 이 이유로 실패했다). 이름 태그를 쓴다.

오래된 커밋 태그가 쌓이면 정리한다.

```bash
docker image prune -a --filter "until=336h"   # 2주 넘은 미사용 이미지
```

## 데이터 마이그레이션

`districts` 컬렉션의 PK 필드가 `district_id` 에서 `id` 로 바뀌었다. 구버전 데이터가
든 DB 는 새 코드가 유니크 인덱스를 만들다 `dup key: { id: null }` 로 죽어 **API 가
기동 자체를 못 한다.** 딱 한 번 실행하면 되고, 이미 이관된 DB 에서는 아무 일도
하지 않는다.

```bash
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py --dry-run
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py
```

## 알려진 미해결 항목

* **`admin_dongs.geojson` 이 없다** — 프론트는 구역을 파고들 때
  `/api/priority/dongs/geojson` 을 호출하지만(`PriorityMap.tsx`) 이 파일이 없어
  404 다. 원래부터 그랬고, 읍면동 경계만 안 나온다.
  6.2MB 라 `.gitignore` 에 걸려 있고, 생성원인 `priority_engine/raw/` 도 저장소에
  없어서 `boundary/build_boundaries.py` 를 돌릴 수도 없다. **먼저 원본 데이터를
  구해야** 하고, 그 다음 공급 경로(git 커밋 후 이미지에 굽기 / MongoDB 적재 /
  절대 경로 마운트)를 정해야 한다. 굽는 쪽이 이 문서의 원칙과 맞는다.
* **쓰이지 않는 폰트 24MB** — `frontend/public/fonts/AritaDotumKR-*.ttf` 4종이
  저장소와 이미지에 들어 있지만 **어디서도 참조하지 않는다** (`@font-face` 선언
  없음, 빌드된 CSS 에도 `fonts/` 참조 없음). 실제 사용 폰트는 `index.html` 이
  Google Fonts 에서 받는 Noto Sans KR 이다. 브라우저가 받아가지는 않으므로 사용자
  체감은 없고, 저장소·이미지 용량만 죽어 있다.
  `.dockerignore` 로 빼는 것은 권하지 않는다 — 나중에 `@font-face` 를 추가하면
  개발 서버에서는 되고 운영에서만 404 가 나는 함정이 된다. 넣은 사람의 의도를
  확인해 쓰거나 지우는 쪽이 맞다.
* **호스트 nginx 설정 자동 반영** — `infra/nginx/i15c102.conf.example` 은 예시일
  뿐이고 실제 적용은 수동이다. 실서버 설정과 어긋나면 502 로 드러난다.
  현재 실서버는 `/`→8002, `/api/`·`/media/`·`/ws/`·`/docs`→8001 이다.
