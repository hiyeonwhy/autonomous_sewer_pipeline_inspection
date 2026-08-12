# PIPE CHECKKK 포팅 매뉴얼

> 대상 저장소: `S15P11C102`
>
> 이 문서는 SSAFY 공통 프로젝트 산출물의 포팅 매뉴얼 요구사항 중 **1. GitLab 소스 클론 이후 빌드 및 배포**와 **2. 프로젝트에서 사용하는 외부 서비스 정보**를 정리한 문서입니다.
>
> 실제 비밀번호, API Key, Token 등 민감정보는 GitLab에 커밋하지 않습니다. 아래의 `<...>` 표시는 환경에 맞는 실제 값으로 교체합니다.

---

# 1. GitLab 소스 클론 이후 빌드 및 배포

## 1.1 프로젝트 구성

```text
S15P11C102/
├─ frontend/                    # React + Vite 관제 웹
├─ backend/
│  ├─ cloud_api/               # FastAPI Cloud API
│  └─ priority_engine/         # 우선순위 산정 데이터 파이프라인
├─ ai/
│  ├─ AIAgent/                 # 로컬 AI Agent
│  ├─ yolo_detection/          # 하수관 결함 탐지
│  ├─ 3d_gsplat/               # 3D Gaussian Splatting
│  └─ llm_report/              # 보고서 관련 AI 모듈
├─ embedded/                   # Raspberry Pi 주행/오도메트리 코드
├─ hardware/                   # 로봇 3D 모델
├─ infra/
│  ├─ docker/                  # 운영 Docker Compose
│  ├─ jenkins/                 # Jenkins Pipeline
│  └─ nginx/                   # EC2 Host Nginx 설정 예시
└─ exec/                       # 포팅 매뉴얼 및 제출 산출물
```

운영 Cloud 서비스는 다음 구조로 배포합니다.

```text
Internet
   │
   ▼
EC2 Host Nginx :80 / :443
   ├─ /, 정적 웹  ──────────────> 127.0.0.1:8002 -> frontend:80
   ├─ /api/* ─────────────────> 127.0.0.1:8001 -> cloud-api:8000
   ├─ /ws/* ──────────────────> 127.0.0.1:8001 -> cloud-api:8000
   └─ /media/* ───────────────> 127.0.0.1:8001 -> cloud-api:8000

Docker network: sewer-cloud
   ├─ frontend
   ├─ cloud-api
   └─ mongodb:27017 (외부 포트 미노출)
```

---

## 1.2 개발 · 빌드 · 배포 환경

### 1.2.1 Cloud Backend

| 구분 | 사용 기술 | 버전 / 설정 |
|---|---|---|
| Language | Python | `3.12` |
| Framework | FastAPI | `requirements.txt` 기준 |
| WAS / ASGI Server | Uvicorn | `uvicorn[standard]` |
| Database | MongoDB | `7.0` |
| PDF Renderer | WeasyPrint | `requirements.txt` 기준 |
| Docker Base Image | Python | `python:3.12-slim` |
| JVM | 사용하지 않음 | - |

Backend의 정확한 Python 패키지 목록은 다음 파일을 기준으로 합니다.

```text
backend/cloud_api/requirements.txt
```

### 1.2.2 Frontend

| 구분 | 사용 기술 | 버전 / 설정 |
|---|---|---|
| Runtime / Build | Node.js | `20` |
| Framework | React | `19.0.1` |
| Build Tool | Vite | `6.4.3` 계열 |
| Language | TypeScript | `5.8.x` |
| Web Server | Nginx | `nginx:alpine` |
| CSS | Tailwind CSS | `4.1.x` |

Frontend 의존성의 최종 기준 파일은 다음과 같습니다.

```text
frontend/package.json
frontend/package-lock.json
```

### 1.2.3 Local AI Agent

| 구분 | 사용 기술 | 버전 / 설정 |
|---|---|---|
| Language | Python | `>=3.11,<3.12` |
| API Server | FastAPI / Uvicorn | `pyproject.toml` 기준 |
| AI Runtime | PyTorch | `>=2.7,<3` (models extra) |
| YOLO | Ultralytics | `>=8.3,<9` (models extra) |
| Transformers | Hugging Face Transformers | `4.51.3` |
| GPU | NVIDIA GPU 권장 | RTX 4070 Laptop 8GB에서 검증됨 |

AI Agent의 패키지 기준 파일:

```text
ai/AIAgent/pyproject.toml
```

> CUDA 버전은 저장소에서 하나의 버전으로 고정하지 않습니다. 로컬 GPU/드라이버에 맞는 PyTorch CUDA wheel을 사용합니다.

### 1.2.4 Infra / CI/CD

| 구분 | 사용 기술 | 비고 |
|---|---|---|
| Container | Docker | 운영 서버 설치 버전 사용 |
| Compose | Docker Compose V2 | Jenkins 환경에서 `v2.26.1` 사용 확인 |
| CI/CD | Jenkins Pipeline | `infra/jenkins/Jenkinsfile` |
| Reverse Proxy | Nginx | EC2 Host에서 SSL 종료 및 Reverse Proxy |
| TLS | Let's Encrypt / Certbot | 실제 서버 인증서 경로 사용 |
| IDE | Visual Studio Code | 팀원별 버전 상이, 빌드/배포에 비의존 |

> Docker Engine, Jenkins, EC2 Ubuntu의 정확한 설치 버전은 저장소에서 고정하지 않았습니다. 인수 시 실제 서버에서 아래 명령으로 확인합니다.
>
> ```bash
> docker --version
> docker-compose version
> python3 --version
> nginx -v
> lsb_release -a
> ```

---

## 1.3 주요 환경 변수 및 설정 파일

### 1.3.1 운영 Cloud 환경 변수

운영 배포는 다음 파일 하나를 중심으로 관리합니다.

```text
infra/docker/.env
```

이 파일은 민감정보를 포함하므로 Git에서 추적하지 않습니다. 예제 파일을 복사하여 생성합니다.

```bash
cp infra/docker/.env.example infra/docker/.env
```

주요 값:

| 변수 | 설명 | 필수 여부 |
|---|---|---|
| `MONGO_USER` | MongoDB 관리자 계정 | 필수 |
| `MONGO_PASSWORD` | MongoDB 관리자 비밀번호 | 필수 |
| `MONGODB_DB_NAME` | DB 이름, 기본 `sewer_inspection` | 필수 |
| `MEDIA_ROOT` | 결함 이미지 저장 경로, 기본 `/app/media` | 필수 |
| `AGENT_PUBLISH_KEY` | Agent telemetry/log 쓰기 인증 키 | 선택 |
| `LLM_ENABLED` | LLM 보고서 요약 활성화 | 선택 |
| `LLM_API_BASE_URL` | LLM API Base URL | LLM 사용 시 |
| `LLM_API_KEY` | LLM API 인증 키 | LLM 사용 시 |
| `LLM_MODEL` | 보고서 텍스트 모델 | LLM 사용 시 |
| `LLM_VISION_MODEL` | 결함 이미지 판독 모델 | 선택 |
| `LLM_TIMEOUT_S` | LLM 요청 timeout | 선택 |
| `LLM_DESCRIBE_IMAGES` | 결함 이미지 판독 사용 여부 | 선택 |
| `LLM_MAX_VISION_DEFECTS` | 보고서 1회당 최대 비전 분석 결함 수 | 선택 |
| `LLM_IMAGE_BUDGET_BYTES` | LLM 전송 이미지 최대 크기 | 선택 |

예시:

```dotenv
MONGO_USER=<MONGODB_USER>
MONGO_PASSWORD=<MONGODB_PASSWORD>
MONGODB_DB_NAME=sewer_inspection
MEDIA_ROOT=/app/media

AGENT_PUBLISH_KEY=

LLM_ENABLED=true
LLM_API_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1
LLM_API_KEY=<SSAFY_GMS_API_KEY>
LLM_MODEL=gpt-5.1
LLM_VISION_MODEL=gpt-5.1
LLM_TIMEOUT_S=60
LLM_DESCRIBE_IMAGES=true
LLM_MAX_VISION_DEFECTS=8
LLM_IMAGE_BUDGET_BYTES=61440
```

### 1.3.2 Backend 단독 실행 환경 변수

Backend를 Docker 없이 직접 실행할 때는 다음 파일을 사용합니다.

```text
backend/cloud_api/.env
```

생성:

```bash
cd backend/cloud_api
cp .env.example .env
```

주요 설정:

```dotenv
MONGODB_URL=mongodb://<USER>:<PASSWORD>@mongodb:27017/sewer_inspection?authSource=admin
MONGODB_DB_NAME=sewer_inspection
MEDIA_ROOT=/app/media
PRIORITY_DATA_DIR=

LLM_ENABLED=true
LLM_API_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1
LLM_API_KEY=<SSAFY_GMS_API_KEY>
LLM_MODEL=<MODEL_NAME>
LLM_VISION_MODEL=<VISION_MODEL_NAME>
```

`PRIORITY_DATA_DIR`이 비어 있으면 저장소의 `backend/priority_engine/data`를 사용합니다.

### 1.3.3 Frontend 환경 변수

파일:

```text
frontend/.env.production
frontend/.env.development
frontend/.env.example
```

운영 기본값:

```dotenv
VITE_API_ORIGIN=https://i15c102.p.ssafy.io
VITE_API_BASE_URL=/api
VITE_USE_MOCK=false
```

추가 설정 예시:

```dotenv
VITE_WS_URL=wss://i15c102.p.ssafy.io
VITE_VWORLD_KEY=<VWORLD_KEY>

VITE_AGENT_API_URL=http://127.0.0.1:8000
VITE_AGENT_WS_URL=ws://127.0.0.1:8000/ws/events
VITE_CAMERA_STREAM_URL=http://<PI_IP>:8081/stream.mjpg
VITE_CAMERA_HEALTH_URL=http://<PI_IP>:8081/health
```

> `VITE_*` 환경 변수는 Vite 빌드 시 브라우저 번들에 포함됩니다. 공개하면 안 되는 Secret을 `VITE_*`로 저장해서는 안 됩니다. 값 변경 후에는 Frontend를 다시 빌드해야 합니다.

VWorld Key는 Git 추적 파일에 입력하지 않고 로컬에서 다음과 같이 관리하는 것을 권장합니다.

```text
frontend/.env.local
```

### 1.3.4 Local AI Agent 환경 변수

파일:

```text
ai/AIAgent/.env
```

예제 생성:

```bash
cd ai/AIAgent
cp .env.example .env
```

주요 설정:

```dotenv
PIPECHECKKK_EDGE_URL=http://<PI_IP>:8765
PIPECHECKKK_EDGE_TOKEN=<PI와_AI에_동일하게_설정한_TOKEN>
PIPECHECKKK_CAMERA_SNAPSHOT_URL=http://<PI_IP>:8081/snapshot.jpg

PIPECHECKKK_AI_HOST=127.0.0.1
PIPECHECKKK_AI_PORT=8000

PIPECHECKKK_CLOUD_API_URL=https://i15c102.p.ssafy.io
PIPECHECKKK_AGENT_ID=<AGENT_ID>
PIPECHECKKK_CLOUD_PUBLISH_INTERVAL_S=0.2
PIPECHECKKK_CLOUD_PUBLISH_KEY=
```

실제 Motor Edge를 사용하는 AI Runtime은 `PIPECHECKKK_EDGE_URL`과 `PIPECHECKKK_EDGE_TOKEN`이 필수이며, 둘 중 하나라도 없으면 fail-closed 방식으로 실행되지 않습니다.

---

## 1.4 GitLab Clone

```bash
git clone <GITLAB_REPOSITORY_URL>
cd S15P11C102
```

현재 배포할 Commit 확인:

```bash
git rev-parse --short HEAD
```

이 Commit 값은 Backend `/api/health` 및 Frontend `/build-info.json`의 배포 버전 확인에 사용합니다.

---

## 1.5 Cloud 서비스 빌드 및 배포

### 1.5.1 운영 환경 변수 준비

```bash
cp infra/docker/.env.example infra/docker/.env
```

`infra/docker/.env`에 최소 다음 값을 입력합니다.

```dotenv
MONGO_USER=<MONGODB_USER>
MONGO_PASSWORD=<MONGODB_PASSWORD>
```

LLM 보고서 기능까지 사용할 경우 다음 값도 입력합니다.

```dotenv
LLM_API_KEY=<SSAFY_GMS_API_KEY>
```

> `LLM_API_KEY`가 비어 있어도 Backend PDF 출력 자체가 중단되지는 않으며, 보고서 요약은 규칙 기반 fallback을 사용합니다.

### 1.5.2 Docker Compose Build + Run

프로젝트 루트에서 실행합니다.

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) \
  docker-compose -f infra/docker/docker-compose.prod.yml \
  up -d --build --remove-orphans
```

환경에 따라 `docker-compose` 대신 아래 명령을 사용할 수 있습니다.

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) \
  docker compose -f infra/docker/docker-compose.prod.yml \
  up -d --build --remove-orphans
```

서비스 상태 확인:

```bash
docker-compose -f infra/docker/docker-compose.prod.yml ps
```

### 1.5.3 배포되는 Container

| Service | Container | Host Port | Container Port |
|---|---|---:|---:|
| Frontend | `sewer-frontend` | `127.0.0.1:8002` | `80` |
| Cloud API | `sewer-cloud-api` | `127.0.0.1:8001` | `8000` |
| MongoDB | `sewer-mongodb` | 외부 미노출 | `27017` |

Named Volume:

```text
mongodb_data  -> /data/db
media_data    -> /app/media
```

- `mongodb_data`: MongoDB 데이터 보존
- `media_data`: 탐사 결함 이미지 보존

Backend의 우선순위 산정 산출물은 bind mount가 아니라 Backend Docker Image 내부에 포함됩니다.

---

## 1.6 Frontend 개별 빌드

Cloud Docker 배포와 별도로 Frontend만 확인할 경우:

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

빌드 결과:

```text
frontend/dist/
```

운영 Dockerfile은 다음 순서로 동작합니다.

```text
node:20-alpine
  -> npm ci
  -> npm run build
  -> dist 생성
  -> nginx:alpine 이미지로 dist 복사
```

---

## 1.7 Backend 개별 실행

Linux 기준:

```bash
cd backend/cloud_api
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

`.env` 값을 실제 환경에 맞게 입력한 뒤:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

개발 환경에서 `8001`을 사용하는 이유는 Frontend Vite Proxy 및 운영 Host Port와 번호를 맞추기 위함입니다. Docker Container 내부에서는 `8000`을 사용합니다.

---

## 1.8 Local AI Agent 설치 및 실행

### 1.8.1 Python 환경

```bash
cd ai
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'AIAgent[dev,models]'
```

NVIDIA GPU 확인:

```bash
nvidia-smi
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

### 1.8.2 AI 모델 다운로드

모델 목록 확인:

```bash
.venv/bin/python AIAgent/scripts/download_models.py --list
```

Depth + Qwen 다운로드:

```bash
.venv/bin/python AIAgent/scripts/download_models.py --model all
```

프로젝트에서 지정한 모델:

| 용도 | 모델 |
|---|---|
| Depth | `depth-anything/Depth-Anything-V2-Small-hf` |
| 진행 안내 | `Qwen/Qwen2.5-VL-3B-Instruct-AWQ` |

모델 Weight는 Git에 포함하지 않습니다.

### 1.8.3 실제 로봇 연결

```bash
cd ai/AIAgent
cp .env.example .env
```

필수 값 입력:

```dotenv
PIPECHECKKK_EDGE_URL=http://<PI_IP>:8765
PIPECHECKKK_EDGE_TOKEN=<RANDOM_SHARED_TOKEN>
PIPECHECKKK_CAMERA_SNAPSHOT_URL=http://<PI_IP>:8081/snapshot.jpg
```

실행 스크립트:

```bash
chmod +x scripts/run_real_motor.sh
scripts/run_real_motor.sh
```

Local AI API는 기본적으로 `127.0.0.1:8000`에 bind됩니다.

---

## 1.9 EC2 Host Nginx 설정

저장소 예제:

```text
infra/nginx/i15c102.conf.example
```

주요 Proxy 규칙:

```text
/        -> http://127.0.0.1:8002
/api/    -> http://127.0.0.1:8001
/ws/     -> http://127.0.0.1:8001
/media/  -> http://127.0.0.1:8001
```

보고서 PDF 생성 및 WebSocket 장기 연결을 위해 `/api/`와 `/ws/`는 최대 300초 timeout 설정을 사용합니다.

결함 이미지 업로드를 위해 다음 값이 필요합니다.

```nginx
client_max_body_size 12m;
```

실제 서버 적용 시 `YOUR_DOMAIN`과 인증서 경로를 현재 서버에 맞게 수정합니다.

설정 검사 및 반영:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

> 기존 Certbot이 관리하는 인증서 설정을 무조건 덮어쓰지 말고 현재 서버 설정과 비교한 뒤 반영합니다.

---

## 1.10 Jenkins CI/CD

Pipeline 파일:

```text
infra/jenkins/Jenkinsfile
```

Jenkins Job의 Pipeline Script Path:

```text
infra/jenkins/Jenkinsfile
```

Pipeline 순서:

```text
1. Checkout
2. Frontend Verify
   - npm ci
   - npm run lint
   - npm run test
3. Setup Python
   - venv
   - pip install -r requirements.txt
4. Backend Sanity Check
   - compileall
   - app.main import 검사
5. develop Branch Deploy
   - docker-compose up -d --build --remove-orphans
6. Verify Deploy
   - API/Frontend commit 일치 확인
   - 우선순위 GeoJSON 확인
   - 공개 URL 상태 확인
```

필요한 Jenkins Credential:

| Credential ID | Type | 내용 |
|---|---|---|
| `cloud-api-env-file` | Secret file | 실제 `infra/docker/.env` 내용 전체 |

Credential 파일에는 최소한 다음 값이 있어야 합니다.

```dotenv
MONGO_USER=<VALUE>
MONGO_PASSWORD=<VALUE>
```

---

## 1.11 배포 확인

현재 운영 주소:

```text
https://i15c102.p.ssafy.io
```

### API Health Check

```bash
curl -s https://i15c102.p.ssafy.io/api/health
```

정상 예시:

```json
{
  "status": "ok",
  "server": "connected",
  "mongodb": "connected",
  "commit": "<CURRENT_COMMIT>"
}
```

### Frontend 배포 Commit 확인

```bash
curl -s https://i15c102.p.ssafy.io/build-info.json
```

### Git Commit과 비교

```bash
git rev-parse --short HEAD
```

`/api/health`와 `/build-info.json`의 `commit` 값이 위 Commit과 동일하면 최신 버전이 배포된 것입니다.

### 우선순위 데이터 확인

```bash
curl -f https://i15c102.p.ssafy.io/api/priority/districts/geojson
```

### 공개 경로 확인

```text
https://i15c102.p.ssafy.io/
https://i15c102.p.ssafy.io/api/health
https://i15c102.p.ssafy.io/docs
```

---

## 1.12 배포 시 특이사항 및 주의사항

### A. `GIT_COMMIT`을 전달해야 함

배포 명령에서 다음 값을 전달해야 합니다.

```bash
GIT_COMMIT=$(git rev-parse --short HEAD)
```

전달하지 않으면 `/build-info.json`과 `/api/health`의 Commit이 `unknown`으로 표시되어 배포 검증이 어려워집니다.

### B. MongoDB 계정은 최초 Volume 생성 시 적용됨

`MONGO_USER`, `MONGO_PASSWORD`는 `mongodb_data` Volume이 최초 생성될 때 MongoDB 관리자 계정 생성에 사용됩니다.

이미 DB Volume이 만들어진 후 `.env`의 값만 변경하면 실제 DB 계정과 환경 변수가 불일치할 수 있습니다. 비밀번호 변경 시 DB 내부 사용자 정보도 함께 변경해야 합니다.

### C. MongoDB는 외부 Port를 공개하지 않음

Cloud API가 Docker Network 내부에서 다음 주소로 접근합니다.

```text
mongodb://<USER>:<PASSWORD>@mongodb:27017/sewer_inspection?authSource=admin
```

### D. Frontend Host Port는 `8002`

Host Nginx가 Frontend를 다음 주소로 Proxy합니다.

```text
127.0.0.1:8002
```

Compose의 Frontend Port를 변경하고 Host Nginx를 수정하지 않으면 사이트 전체가 `502`가 됩니다.

### E. Backend Host Port는 `8001`

```text
127.0.0.1:8001 -> cloud-api:8000
```

### F. 우선순위 데이터는 Backend Image에 포함됨

`backend/priority_engine/data`는 운영 시 Host bind mount를 사용하지 않고 Backend Image Build 과정에서 `/app/priority_data`에 포함됩니다.

따라서 우선순위 산출물을 변경한 경우 Backend Image를 다시 빌드해야 합니다.

### G. 기존 DB 마이그레이션

구버전 DB의 `districts` 컬렉션이 `district_id` 필드를 사용하는 경우 `id` 필드로 마이그레이션해야 합니다.

Dry Run:

```bash
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py --dry-run
```

실제 적용:

```bash
docker exec sewer-cloud-api python3 scripts/migrate_districts_id.py
```

마이그레이션 전 데이터는 `districts_backup_pre_id_rename` 컬렉션에 백업됩니다.

### H. 알려진 데이터 제한

현재 저장소의 우선순위 데이터에는 `admin_dongs.geojson`이 없어 읍면동 경계 API가 `404`가 될 수 있습니다. 시도/시군구 수준 우선순위 기능과는 별개의 알려진 제한사항입니다.

---

## 1.13 주요 설정 및 프로퍼티 파일 목록

| 파일 | 역할 |
|---|---|
| `infra/docker/.env` | 운영 Cloud 실제 Secret/환경변수. Git 미추적 |
| `infra/docker/.env.example` | 운영 Cloud 환경변수 예제 |
| `infra/docker/docker-compose.prod.yml` | 운영 Container, Network, Volume, Port 정의 |
| `backend/cloud_api/.env` | Backend 단독 실행 환경변수. Git 미추적 |
| `backend/cloud_api/.env.example` | Backend 환경변수 예제 |
| `backend/cloud_api/requirements.txt` | Backend Python 의존성 |
| `backend/cloud_api/Dockerfile` | Backend Image Build |
| `frontend/.env.production` | Frontend 운영 환경 설정 |
| `frontend/.env.development` | Frontend 개발 환경 설정 |
| `frontend/.env.example` | Frontend 전체 환경변수 예제 |
| `frontend/package.json` | Frontend dependency 및 npm script |
| `frontend/package-lock.json` | Frontend dependency lock |
| `frontend/Dockerfile` | Frontend Build 및 Nginx Serving Image |
| `frontend/nginx.conf` | Frontend Container 내부 정적 파일 Nginx |
| `ai/AIAgent/.env` | AI Agent 실제 환경변수. Git 미추적 |
| `ai/AIAgent/.env.example` | AI Agent 환경변수 예제 |
| `ai/AIAgent/pyproject.toml` | AI Agent Python dependency |
| `ai/AIAgent/SETUP_GUIDE.md` | AI 모델 설치/실행 상세 절차 |
| `infra/jenkins/Jenkinsfile` | CI/CD Pipeline |
| `infra/nginx/i15c102.conf.example` | EC2 Host Nginx/SSL/Reverse Proxy 예시 |

---

# 2. 프로젝트에서 사용하는 외부 서비스 정보

외부 서비스는 **운영 시 직접 사용**, **선택 기능**, **오프라인 데이터 생성**, **AI 모델 준비**로 구분합니다.

| 서비스 | 용도 | 실행 필수 여부 | 인증 정보 |
|---|---|---|---|
| SSAFY GMS | 보고서 LLM 요약/이미지 설명 | 선택 | GMS API Key |
| VWorld OpenAPI | 지도 상세 배경 Tile | 선택 | VWorld Key |
| Kakao Local API | 지반침하 주소/좌표 지오코딩 | 데이터 재생성 시 | Kakao REST API Key |
| 공공데이터포털 ASOS API | 최근 30일 강수량 수집 | 데이터 재생성 시 | data.go.kr Service Key |
| 지하안전정보 OpenAPI | 지반침하 이력 수집 | 데이터 재생성 시 | data.go.kr Service Key |
| ODCloud | 하수관로 시설 현황 수집 | 데이터 재생성 시 | 공공데이터포털 인증키 |
| Hugging Face Hub | Depth/Qwen 모델 Weight 다운로드 | AI 신규 설치 시 | 공개 모델은 기본적으로 별도 Key 불필요 |
| Discord + Hermes CLI | 로컬 VL 분석 Relay/시연 경로 | 해당 기능 사용 시 | Hermes 측 Webhook 설정 |

> 이미 생성된 우선순위 데이터가 저장소에 포함되어 있으므로 Kakao / 공공데이터 / ODCloud API Key가 없어도 Cloud 웹 서비스 자체를 실행할 수 있습니다. 해당 키들은 원천 데이터를 다시 수집하거나 산출물을 재생성할 때 필요합니다.

---

## 2.1 SSAFY GMS

### 용도

Cloud Backend가 탐사 보고서의 서술형 요약 및 결함 이미지 설명을 생성할 때 사용합니다.

OpenAI API 호환 방식으로 호출합니다.

### API Base URL

```text
https://gms.ssafy.io/gmsapi/api.openai.com/v1
```

### 환경 변수

`infra/docker/.env`:

```dotenv
LLM_ENABLED=true
LLM_API_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1
LLM_API_KEY=<SSAFY_GMS_API_KEY>
LLM_MODEL=gpt-5.1
LLM_VISION_MODEL=gpt-5.1
```

Backend 단독 실행 시에는 `backend/cloud_api/.env`에 동일하게 설정합니다.

### Key가 없을 경우

`LLM_API_KEY`를 비워 두면 LLM 호출 대신 규칙 기반 보고서 요약으로 fallback합니다. PDF 출력 기능 자체는 사용할 수 있습니다.

### 보안

실제 Key는 다음 파일에만 저장하고 Git에 커밋하지 않습니다.

```text
infra/docker/.env
backend/cloud_api/.env
Jenkins Secret File Credential
```

---

## 2.2 VWorld OpenAPI

### 용도

우선순위 지도에서 상세 지역의 VWorld 배경지도를 표시할 때 사용합니다.

Frontend에서 WMTS Tile을 다음 형식으로 요청합니다.

```text
https://api.vworld.kr/req/wmts/1.0.0/<VWORLD_KEY>/Base/{z}/{y}/{x}.png
```

### 발급

VWorld OpenAPI에서 인증키를 발급하고 배경지도 API / WMTS·TMS API 사용 권한을 설정합니다.

### 설정

```dotenv
VITE_VWORLD_KEY=<VWORLD_API_KEY>
```

Git에 추적되는 `.env.production`, `.env.development`, `.env.example`에는 실제 Key를 작성하지 않습니다.

권장 위치:

```text
frontend/.env.local
```

### Key가 없을 경우

상세 VWorld Tile Layer만 제외되며 프로젝트의 자체 시도/시군구 우선순위 데이터 기능은 사용할 수 있습니다.

---

## 2.3 Kakao Local API

### 용도

우선순위 데이터 생성 과정에서 지반침하 사고 주소 또는 좌표를 행정구역에 매핑하는 지오코딩에 사용합니다.

사용 API:

```text
주소 검색
https://dapi.kakao.com/v2/local/search/address.json

키워드 검색
https://dapi.kakao.com/v2/local/search/keyword.json

좌표 -> 행정구역 변환
https://dapi.kakao.com/v2/local/geo/coord2regioncode.json
```

### 인증

Kakao Developers에서 애플리케이션을 생성하고 **REST API Key**를 발급합니다.

### 실행

```bash
cd backend/priority_engine
KAKAO_REST_KEY=<REST_API_KEY> python3 -m collectors.geocode --build
```

또는 CLI의 `--key` 인자로 전달할 수 있습니다.

### 보안

Key는 환경 변수 또는 CLI 인자로만 전달하며 코드/캐시/산출물에 저장하지 않습니다.

### 운영 서비스와의 관계

Cloud 웹 서비스가 사용자의 요청마다 Kakao API를 호출하는 구조가 아닙니다. 우선순위 원천 데이터를 다시 생성할 때 사용합니다.

---

## 2.4 공공데이터포털 - 기상청 ASOS 일자료

### 용도

시군구별 최근 30일 강수량을 수집하는 우선순위 데이터 파이프라인에 사용합니다.

API Endpoint:

```text
https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList
```

공공데이터포털 데이터:

```text
기상청 지상(종관, ASOS) 일자료 조회서비스
```

### 인증

공공데이터포털에서 해당 OpenAPI 활용 신청 후 Service Key를 발급받습니다.

코드상 `Encoding` Service Key를 사용합니다.

### 실행

```bash
cd backend/priority_engine
python3 -m collectors.rainfall --key '<DATA_GO_KR_ENCODING_KEY>'
```

필요한 경우 종료 기준일을 지정할 수 있습니다.

```bash
python3 -m collectors.rainfall \
  --key '<DATA_GO_KR_ENCODING_KEY>' \
  --end YYYYMMDD
```

### 운영 서비스와의 관계

강수 데이터는 오프라인 데이터 수집/갱신 단계에서 사용하며 Cloud 서비스의 일반적인 기동에는 필수 Key가 아닙니다.

---

## 2.5 공공데이터포털 - 지하안전정보 OpenAPI

### 용도

지반침하 사고 이력 및 관련 정보를 수집하여 우선순위 데이터 생성에 사용합니다.

Base URL:

```text
https://apis.data.go.kr/1613000/undergroundsafetyinfo01
```

### 인증

공공데이터포털의 해당 API 활용 신청 후 Service Key를 발급받습니다.

### 실행 예

```bash
cd backend/priority_engine
python3 -m collectors.subsidence --key '<DATA_GO_KR_SERVICE_KEY>'
```

추가 옵션은 다음으로 확인합니다.

```bash
python3 -m collectors.subsidence --help
```

### 운영 서비스와의 관계

원천 데이터 재수집/갱신용이며 이미 생성된 프로젝트 산출물을 사용하는 일반 Cloud 서비스 실행에는 필수 Key가 아닙니다.

---

## 2.6 ODCloud - 하수관로 시설 현황

### 용도

연도별 하수관로 시설 현황 데이터를 수집하여 시군구 단위 지표를 생성합니다.

Base URL:

```text
https://api.odcloud.kr/api
```

프로젝트에서 사용하는 연도별 데이터 Endpoint는 `backend/priority_engine/collectors/sewer.py`의 `ENDPOINTS_BY_YEAR`에 정의되어 있습니다.

### 인증

공공데이터포털 인증키를 사용합니다.

코드의 `--key` 옵션은 Decoding 인증키를 받습니다.

### 실행 예

```bash
cd backend/priority_engine
python3 -m collectors.sewer --key '<DATA_GO_KR_DECODING_KEY>'
```

API 대신 준비된 CSV로 실행할 수도 있습니다.

```bash
python3 -m collectors.sewer --csv
```

### 운영 서비스와의 관계

Cloud 서버가 실시간 호출하는 API가 아니라 우선순위 데이터셋 생성/갱신용입니다.

---

## 2.7 Hugging Face Hub

### 용도

Local AI Agent 신규 설치 시 다음 AI Model Weight를 다운로드합니다.

```text
depth-anything/Depth-Anything-V2-Small-hf
Qwen/Qwen2.5-VL-3B-Instruct-AWQ
```

### 설치

AI dependency에 `huggingface-hub`가 포함되어 있습니다.

```bash
cd ai
python3.11 -m venv .venv
.venv/bin/python -m pip install -e 'AIAgent[dev,models]'
```

### 모델 다운로드

```bash
.venv/bin/python AIAgent/scripts/download_models.py --model all
```

프로젝트는 재현성을 위해 다운로드 Script 내부에 모델 revision을 고정해 사용합니다.

### 인증

현재 사용하는 공개 모델 다운로드 자체에는 별도 Hugging Face Token이 필수로 설정되어 있지 않습니다. 향후 gated/private model로 변경하는 경우 별도 Hugging Face 계정과 Token이 필요할 수 있습니다.

### 보안 / 저장소 정책

Model Weight 및 Hugging Face Cache는 Git에 Commit하지 않습니다.

---

## 2.8 Discord + Hermes CLI

### 용도

Local AI Agent의 VL 점검 경로 중 실제 이미지와 센서 정보를 Hermes CLI Webhook으로 전달하고 Discord에서 분석/승인 메시지를 주고받는 기능에 사용합니다.

코드의 기본 Webhook 이름:

```text
pipecheckkk-vl
```

AI Agent는 실행 시 `hermes` CLI가 PATH에서 발견되어야 해당 기능을 사용할 수 있습니다.

확인:

```bash
which hermes
hermes --help
```

### 필요 설정

Hermes CLI 측에 `pipecheckkk-vl` Webhook과 Discord 연동이 사전에 구성되어 있어야 합니다.

> 해당 Hermes/Discord 계정 설정은 저장소에 Secret으로 포함하지 않습니다. 새 환경에서 동일 기능을 재현할 경우 팀이 사용한 Hermes 설정 및 Discord Webhook 권한을 별도로 인계해야 합니다.

### 운영 서비스와의 관계

Cloud API/Frontend/MongoDB의 기본 배포에는 필수 항목이 아니며, 로컬 VL 시연 기능을 사용할 때 필요합니다.

---

## 2.9 외부 서비스 Secret 관리 원칙

다음 정보는 GitLab에 커밋하지 않습니다.

```text
MongoDB Password
SSAFY GMS API Key
VWorld API Key
Kakao REST API Key
공공데이터포털 Service Key
Raspberry Pi / AI Agent Shared Token
Jenkins Secret File
Discord / Hermes 인증 정보
```

Git에 포함하는 것은 `.env.example`과 설정 방법뿐입니다.

운영 Cloud Secret:

```text
infra/docker/.env
```

Jenkins에서는 다음 Credential로 관리합니다.

```text
Credential ID: cloud-api-env-file
Type: Secret file
```

---

## 2.10 인수/포팅 체크리스트

- [ ] GitLab Clone 완료
- [ ] `infra/docker/.env` 생성
- [ ] `MONGO_USER` / `MONGO_PASSWORD` 입력
- [ ] 필요 시 SSAFY GMS Key 입력
- [ ] Docker Compose V2 확인
- [ ] `docker-compose ... up -d --build` 성공
- [ ] `sewer-mongodb`, `sewer-cloud-api`, `sewer-frontend` 정상 실행
- [ ] Host Nginx `/`, `/api/`, `/ws/`, `/media/` Proxy 확인
- [ ] `/api/health` 정상
- [ ] `/build-info.json` Commit 일치
- [ ] `/api/priority/districts/geojson` 정상
- [ ] 필요 시 Frontend VWorld Key 설정
- [ ] 필요 시 Local AI Agent Python 3.11 환경 구성
- [ ] 필요 시 Hugging Face Model 다운로드
- [ ] 필요 시 Raspberry Pi Edge URL/Token 설정
- [ ] 원천 데이터 재생성 시 Kakao / data.go.kr / ODCloud Key 준비

---

## 참고: 제출 전 실제 서버에서 한 번 확인할 값

다음 항목은 저장소에 정확한 Host 설치 버전이 고정되어 있지 않으므로 제출 직전 실제 서버에서 확인해 필요하면 이 문서의 1.2.4에 추가합니다.

```bash
lsb_release -a
docker --version
docker-compose version
nginx -v
```

Jenkins 버전은 Jenkins 관리 화면에서 확인할 수 있습니다.

