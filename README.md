# PIPE CHECKKK

AIoT 기반 하수관로 점검 우선순위 산정 및 자율 점검 시스템

## 프로젝트 구성

- frontend: React 19 + Vite
- backend: FastAPI
- database: MongoDB 7
- AI Agent: VLM GPT VL
- AI Detection: YOLO26n
- Infra: Docker Compose + Jenkins + Nginx
- Edge: Raspberry Pi 5

## VLM 모델을 통한 판단 구조

```text
Raspberry Pi
카메라 + 초음파 + IMU
        │
        ▼
노트북 AI Agent 서버
├─ Depth Anything V2
│   └─ 좌·중앙·우 공간 위험도 및 자유공간 분석
│
├─ YOLO26n
│   └─ 균열·표면 손상 탐지 및 결함 기록
│
├─ GPT VL
│   └─ 장면 이해 및 주행 행동 제안
│
├─ 규칙 기반 안전 정책
│
├─ 상태 머신
│   └─ 주행·정지·장애물 회피·경로 복귀·완료 관리
│
└─ 안전 검증기
    ├─ 속도·회전량·실행시간 제한
    └─ 안전 조건 통과 시에만 Motor Edge로 전달
        │
        ▼
Pi Motor Edge
├─ 명령 TTL
├─ Heartbeat·Watchdog
├─ Emergency Stop
├─ 최대 속도 제한
├─ 원격 명령 만료·통신 단절 시 자동 STOP
└─ 승인된 제한 동작만 모터 출력으로 변환
        │
        ▼
모터
        │
        ▼
동작 완료 후 STOP
        │
        ▼
새 이미지 + Depth + 초음파 + IMU 재확인
        │
        └──────────────→ 다음 행동 재판단
```

### 1. Depth Anything V2

카메라 영상으로 상대 깊이 맵을 생성합니다. 구역별 장애물 위험도와 자유공간 점수를 계산하여 회피 방향을 제안합니다.

### 2. GPT VL

#### 입력

- 현재 카메라 이미지
- Depth 공간 위험도
- 초음파 정면 거리
- IMU 자세 정보
- 현재 주행 상태
- 이전 행동과 실행 결과
- 허용된 행동 목록

#### 출력

- 카메라 영상의 전체 장면 이해
- 장애물 위치와 통과 가능 경로 판단
- 이전 행동의 성공·실패 결과 반영
- 허용된 주행 행동 중 하나 제안

GPT VL의 출력은 직접 모터에 전달되지 않습니다. 규칙 기반 안전 정책, 상태 머신 및 안전 검증기를 통과한 허용 동작만 Pi Motor Edge로 전달됩니다.

## 디렉터리

```text
frontend/    관제 웹
backend/     Cloud API 및 우선순위 엔진
ai/          AI Agent / YOLO / 3DGS
embedded/    로봇 제어 및 Odometry
hardware/    로봇 3D 모델
infra/       Docker / Jenkins / Nginx
exec/        포팅 매뉴얼 및 시연 관련 산출물
```

## Cloud 서비스 빌드

1. 환경변수 생성

```bash
cp infra/docker/.env.example infra/docker/.env
```

2. `infra/docker/.env` 값 설정

3. Docker Build & Run

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) \
docker compose \
-f infra/docker/docker-compose.prod.yml \
up -d --build
```

## Frontend 개별 빌드

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

## Backend 개별 실행

```bash
cd backend/cloud_api

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env

uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## AI Agent

자세한 설치 방법: [`ai/AIAgent/SETUP_GUIDE.md`](ai/AIAgent/SETUP_GUIDE.md)
