# PIPE CHECKKK — AIoT 기반 하수관로 자율 점검 및 관리 시스템

> 포트폴리오 정리 문서 · 저장소 코드/설정/학습 산출물 기준 · 2026-08-11 분석

## 1. 프로젝트 한눈에 보기

**PIPE CHECKKK**는 노후 하수관로의 점검 대상을 데이터로 우선 선정하고, AI가 탑재된 로봇이 관로를 자율 주행하며 결함을 탐지한 뒤, 관제·3D 재구성·보고서 생성까지 연결하는 AIoT 통합 시스템이다.

- 문제: 넓은 관로망을 동일한 빈도로 사람이 직접 점검하기 어렵고, 밀폐·협소 공간의 현장 점검에는 안전 위험과 높은 비용이 따른다.
- 해결: 공공데이터 기반 위험도 산정 → 점검 구역 선정 → 로봇 자율 주행 → 결함 탐지 → 실시간 관제 → 보고서/PDF 및 3D 결과 관리의 전 과정을 하나의 서비스로 구성했다.
- 핵심 가치: 한정된 점검 자원을 고위험 지역에 먼저 배분하고, 현장 인력의 위험 노출을 줄이며, 점검 결과를 구조화된 데이터로 축적한다.
- 시스템 성격: Web, Cloud API, Edge AI, Embedded, Computer Vision, Data Analysis, 3D Visualization, DevOps가 결합된 end-to-end 프로젝트다.

### 대표 사용자 흐름

1. 전국·시도·시군구·읍면동 우선순위 지도에서 점검 대상 지역을 선택한다.
2. 로봇과 승인 운영자를 선택하고 점검 세션을 생성한다.
3. 로컬 AI Agent가 Raspberry Pi의 카메라·초음파·IMU 데이터를 수집한다.
4. Depth Anything, VLM, YOLO 결과와 규칙 기반 안전 정책을 결합해 다음 행동을 결정한다.
5. 안전 검증을 통과한 제한 명령만 Motor Edge에 전달한다.
6. 실시간 영상, YOLO overlay, 센서, 주행 상태, 판단 로그를 관제 화면과 Cloud API에 전송한다.
7. 탐지 결함, 주행 로그, 3D 재구성 결과를 세션에 연결하고 점검 보고서를 PDF로 출력한다.

## 2. 시스템 아키텍처

```text
[공공데이터 수집/분석]
하수도·관로 노후도·지반침하·강수·인구·행정경계
        │
        ▼
Priority Engine ── JSON/GeoJSON 산출물 ── Cloud API ── MongoDB
                                                │
                                      REST / WebSocket / Media
                                                │
                                                ▼
                                      React 관제 Dashboard
                                                ▲
                                                │
[Raspberry Pi 5]  ◀── 제한 모터 명령 ── [Laptop AI Agent]
카메라·초음파·IMU                    Depth Anything / VLM / YOLO
Motor Edge·50 Hz 안전 감시            상태 머신 / Safety Gate
Heartbeat·TTL·Watchdog                 Telemetry / Frame Publisher
        │                                      │
        └──────── 센서·영상·상태 ──────────────┘
                                                │
                                                ▼
                              결함 저장 → 3D Gaussian Splatting
                                         → LLM 요약/PDF 보고서
```

### 저장소 구성

| 영역 | 역할 |
|---|---|
| `frontend/` | React 기반 관제 웹, 우선순위 지도, 실시간 모니터링, 보고서/결함 관리 |
| `backend/cloud_api/` | FastAPI REST·WebSocket API, MongoDB 저장, PDF 생성, 미디어 관리 |
| `backend/priority_engine/` | 공공데이터 수집·정제·위험도 산정·통계 검증 오프라인 파이프라인 |
| `ai/AIAgent/` | 로봇 임무·판단·주행 제어, 인식 모델 연동, Cloud/Edge 중계 |
| `ai/yolo_detection/` | 관로 결함 detection/segmentation 데이터·학습 파이프라인 |
| `ai/3d_gsplat/` | COLMAP 입력 기반 3D Gaussian Splatting 학습·렌더링 |
| `ai/llm_report/` | 사실 추출, LLM 요약, 실패 시 deterministic fallback |
| `embedded/` | IMU·모터·odometry 보정과 검증 유틸리티 |
| `hardware/` | 로봇 프레임, 배터리·IMU 케이스, 휠 허브 등 STL 설계 파일 |
| `infra/` | Docker Compose, Jenkins CI/CD, Nginx 운영 설정 |

## 3. 기술 스택 요약

| 구분 | 기술 |
|---|---|
| Frontend | React 19, TypeScript 5.8, Vite 6, Tailwind CSS 4, MapLibre GL 6, Three.js, GaussianSplats3D, Motion, Playwright, Node Test Runner |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic v2, PyMongo, MongoDB 7, WebSocket, Jinja2, WeasyPrint, Pillow |
| AI Agent | Python 3.11, FastAPI, PyTorch, Transformers, Qwen-VL 계열, AWQ, Depth Anything V2, OpenCV, Ultralytics, Hermes relay |
| Vision | YOLO11n, YOLO11n-seg, SAM 2.1, COCO/YOLO format, bbox·segmentation |
| Data/Scoring | 공공데이터 API, GeoJSON, 백분위 기반 가중합, Poisson GLM, PCA, 군집·민감도·상관 분석 |
| 3D | 3D Gaussian Splatting(gsplat), COLMAP, CUDA/C++, PyTorch, PLY/KSplat, WebGL |
| Embedded/IoT | Raspberry Pi 5, MPU6050 IMU, ultrasonic sensor, differential motor control, odometry, GPIO, heartbeat/watchdog/TTL |
| Infra | Docker, Docker Compose, multi-stage build, Nginx, Jenkins Pipeline, EC2형 Linux 배포, named volume, health check |
| Quality | pytest, pytest-asyncio, Ruff, TypeScript type check, Playwright, API·WebSocket·안전 정책 단위 테스트 |

## 4. 기술 스택별 상세 구현

### 4.1 Frontend — React, TypeScript, MapLibre GL, Three.js

관제 웹은 단순 조회 화면이 아니라 우선순위 선정부터 실시간 임무 관제, 결함 검토, 보수 상태 변경, 보고서 출력까지 운영 흐름을 연결한다.

주요 구현:

- 전국 → 시도 → 시군구 → 읍면동으로 이어지는 GeoJSON 기반 드릴다운 우선순위 지도
- 공식 위험도와 최근 강수량을 반영한 참고용 동적 위험도 보기 분리
- 지표별 기여도, 영향도, 결측 항목, 산정 이력을 상세 패널에 표시해 점수의 설명 가능성 확보
- 카메라 원본 MJPEG와 YOLO overlay 영상, Agent 연결 상태, mission state, 초음파·IMU·주행 정보를 실시간 표시
- Cloud API와 Local Agent를 각각 연동하고 mock/backend 모드를 환경변수로 전환
- 세션·로봇·운영자 선택, 운영자 코드 검증, 점검 시작/정지/종료 흐름 구현
- 결함 심각도·신뢰도·위치·이미지 조회와 보수 이전/진행/완료 상태 관리
- 날짜·보고서별 필터, 페이지네이션, 서버 생성 PDF blob 다운로드 및 오류 처리
- Three.js와 `@mkkellogg/gaussian-splats-3d`를 이용한 KSplat 결과 표시 기반 마련

문제 해결 사례:

- MapLibre GL 6의 worker URL이 Vite production bundle에서 해석되지 않아 지도 polygon이 사라지는 문제를 분석했다.
- Vite custom plugin으로 worker와 shared module을 고정 경로에 복사하고, production에서 `setWorkerUrl()`을 지정했다.
- Nginx에서 `.mjs`가 `application/octet-stream`으로 응답해 module worker가 차단되는 문제를 `application/javascript` MIME 설정으로 해결했다.
- React StrictMode의 effect 재실행과 요청 취소가 결합되어 무한 로딩이 발생하는 조건을 성공 완료 후 flag를 설정하도록 보완했다.
- 빠른 지역 선택 시 취소된 이전 요청의 `finally`가 새 요청의 loading state를 끄는 race condition을 방지했다.

### 4.2 Backend — FastAPI, MongoDB, WebSocket

Cloud API는 점검 도메인 데이터와 엣지 Agent를 연결하는 중앙 허브다. 기능별 router/schema로 도메인을 분리하고 MongoDB lifespan 및 index 초기화를 적용했다.

주요 도메인:

- Session: 점검 세션 생성·조회·상태 변경, 로봇/운영자 연결
- Defect: Agent 결함 업로드, 중복 방지, 이미지 crop/snapshot, 심각도·보수 상태 변경
- Drive Log: 세션별 주행 이벤트와 판단 기록 저장
- Agent Telemetry: 실시간 상태 publish/read, latest frame 및 YOLO frame 제공
- Agent Command: Cloud에서 로컬 Agent로 start/stop/finish 명령 전달 및 결과 추적
- Frame Stream: publisher/viewer WebSocket을 분리한 영상 fan-out
- Priority: 행정구역 목록·상세·GeoJSON·읍면동·배치·산정 이력 API
- Reconstruction: 세션별 3D 재구성 작업과 결과 상태 관리
- Report: 보고서 조회·수정, HTML preview, PDF 다운로드, 요약 생성
- Resource/Auth: 로봇·운영자 조회와 운영자 passcode 검증
- Media: 파일 업로드와 static media 제공

설계 포인트:

- Pydantic schema를 통해 API 계약과 enum을 명시하고 프론트용 contract 문서를 별도 관리했다.
- Agent publish key와 operator passcode를 분리해 머신 간 쓰기 권한과 사용자 승인을 구분했다.
- 결함 수신에 idempotency 처리를 적용해 네트워크 재시도 시 중복 레코드 생성을 방지했다.
- Jinja2 HTML template + WeasyPrint로 서버 측 PDF를 생성하고 한글 폰트·이미지·요약을 하나의 문서로 렌더링했다.
- `/api/health`에서 MongoDB ping과 배포 commit을 함께 노출해 실제 배포본 검증에 사용했다.

### 4.3 AI Agent — VLM 판단과 규칙 기반 안전 제어

AI Agent는 노트북 GPU 환경에서 카메라와 센서를 종합해 다음 동작을 판단한다. 생성형 모델이 모터를 직접 제어하지 않도록 인식·제안·검증·실행 계층을 분리한 것이 핵심이다.

판단 구성:

- Depth Anything V2: 영상에서 좌·중앙·우 자유공간과 장애물 위험도 계산
- VLM/Qwen-VL 계열: 관로 장면, 장애물, 결함 후보와 진행 방향을 구조적으로 해석
- YOLO: 균열 등 결함 탐지와 overlay 생성, 탐지 history 관리
- Local Planner/State Machine: SEARCH, STOP, 회피, 복구, 완료 등 임무 상태 관리
- Safety Gate: 초음파, IMU, 현재 상태와 출력 제한을 검사해 허용 동작만 command bridge로 전달
- Cloud Publisher: telemetry, progress summary, 최신 frame, defect를 중앙 API로 전송
- Hermes relay: 이미지 분석과 대처 동작을 외부 채널에 전달할 수 있는 중계 구조

안전 설계:

- 임무 시작 전 motor STOP·disarm 및 IMU 100 sample 정지 보정
- 전방 초음파가 0.10m 이하이면 방향 판단 전에 즉시 정지
- 10° → 10° → 10° → 5° 단위 회전, 누적 yaw ±35° 제한
- 상대 pitch 35° 또는 roll 45° 이상이면 Raspberry Pi 로컬에서 즉시 정지
- yaw/roll 복구 허용 오차 ±3°를 10회 연속 만족해야 완료
- 50Hz IMU guard, IMU stale 0.2초, local command TTL 0.25초 적용
- heartbeat 유실, NaN/Inf 센서값, timeout, calibration 누락, 명령 거절 시 fail-closed STOP/disarm
- 후진은 시각 정체 탈출을 위한 짧은 복구 동작으로만 제한

이 구조는 VLM의 장면 이해 능력을 활용하면서도 비결정적 출력을 물리 구동부와 분리해 로봇 안전성을 확보한다.

### 4.4 Computer Vision — YOLO11, SAM 2.1

하수관 내부 CCTV 이미지의 결함을 자동 탐지하고 면적 기반 분석이 가능하도록 detection과 segmentation 모델을 함께 구축했다.

- 데이터: 640×360 PNG 10,000장, annotation 10,868개
- class: `EX`, `CL(종방향 균열)` 2종
- split: train 8,000 / validation 1,000 / test 1,000, seed 42
- detection: YOLO11n fine-tuning
- segmentation: YOLO11n-seg fine-tuning
- pseudo-label: SAM 2.1 base로 COCO bbox를 polygon mask로 확장
- pipeline: 데이터 EDA → seed 고정 분할 → COCO-to-YOLO 변환 → SAM mask 생성 → 80 epoch 학습 → 지표/시각화/weight 저장

저장된 80 epoch detection 학습 로그의 마지막 validation 지표는 Precision 0.8246, Recall 0.2968, mAP50 0.3113, mAP50-95 0.1721이다. 이는 저장소의 validation 로그 기준 수치이며, 별도의 hold-out test 최종 성능으로 과장해 표현하지 않는 것이 적절하다.

### 4.5 위험도 우선순위 엔진 — 공공데이터, 통계 분석, GeoJSON

오프라인 엔진이 여러 공공데이터를 시군구 코드 기준으로 정규화·결합하고 결과 JSON/GeoJSON을 생성한다. API 서버는 검증된 산출물만 읽어 serving하므로 데이터 수집·모델 계산과 온라인 요청 처리가 분리된다.

활용 지표:

- 발생 가능성: 합류식 관로 비율 0.35, 25년 이상 노후 관로 비율 0.25, km당 지반침하 0.20, 콘크리트관 비율 0.15, 집중강우 중점관리구역 비율 0.05
- 피해 영향도: 대구경 관로 비율과 인구의 전국 백분위 평균을 0.5~1.5 배율로 반영
- 강수 trigger: 최근 30일 강수 백분위로 1.0~1.2 참고 점수 산출. 공식 등급·순위는 시점에 따라 흔들리지 않도록 기본 점수 유지

분석·설계 특징:

- 지표의 단위 차이를 제거하기 위해 전국 percentile을 계산한 뒤 사전 등록한 가중치로 결합했다.
- 지반침하 건수는 관로 총연장 km로 나누어 관로가 긴 지역이 무조건 위험하게 평가되는 노출량 편향을 줄였다.
- 원인 미기재가 84.7%인 데이터에서 미기재를 하수 원인 0건으로 간주하면 조사 부실 지역이 안전해 보이는 문제를 발견하고 전체 침하 이력 지표로 교체했다.
- Poisson GLM, PCA, 군집, 상관·결측·민감도 분석으로 지표를 검토했다.
- 평균 관령은 실측 노후율과 상관계수 0.941로 중복되어 제거했고, 통계적으로 유의하지 않은 미연결률도 모델에서 제외했다.
- 지표별 contribution과 missing feature를 결과에 포함해 지도에서 순위의 산정 근거를 설명할 수 있게 했다.
- checksum, model version, batch history를 관리해 산출물 재현성과 변경 추적성을 높였다.

### 4.6 3D Reconstruction — Gaussian Splatting, COLMAP, CUDA

로봇이 수집한 다시점 이미지를 COLMAP camera pose와 함께 학습해 관로 및 결함 주변을 3D Gaussian Splatting으로 재구성하는 모듈이다.

- PyTorch/CUDA 기반 gsplat 학습·rasterization
- PLY/KSplat 결과 생성과 frontend WebGL viewer 연동
- 학습 결과·fly-through 영상·render image 산출 구조
- AbsGrad, antialiasing, densification 설정에 따른 학습 시간·메모리·SSIM·PSNR·LPIPS 비교 기록
- 세션/결함 ID와 3D reconstruction metadata를 Cloud API에 연결할 수 있는 도메인 구성

주의: `ai/3d_gsplat`에는 upstream gsplat 라이브러리 코드와 third-party GLM 코드가 포함되어 있으므로, 포트폴리오에서는 라이브러리 자체 구현보다 **프로젝트 데이터 파이프라인 구성, 학습 검증, 결과 변환 및 웹/백엔드 연동**을 기여 내용으로 표현하는 것이 정확하다.

### 4.7 LLM 보고서 — 사실 기반 요약과 PDF

- 세션·결함·주행 데이터를 report facts로 먼저 구조화
- prompt와 provider 계층을 분리하고 Qwen 계열 provider 연동
- LLM 응답을 Pydantic model로 검증
- 모델 장애·형식 오류 시 deterministic fallback 문장을 생성
- backend report domain에도 summary grading, prereading, fallback을 두어 신뢰 가능한 보고서 생성 흐름 구성
- Jinja2/WeasyPrint를 통해 브라우저와 독립적인 HTML/PDF 출력 제공

핵심은 LLM이 원본 데이터를 임의로 해석하도록 두지 않고, 코드에서 확정한 facts를 입력으로 제공하며 실패 시에도 보고서 생성을 계속할 수 있게 한 점이다.

### 4.8 Embedded/Hardware — Raspberry Pi, IMU, Odometry

- Raspberry Pi 5가 카메라·MPU6050 IMU·초음파 센서·좌우 모터를 담당
- laptop AI 판단과 별개로 Pi에서 50Hz safety guard와 즉시 STOP 수행
- heartbeat, watchdog, command TTL, emergency stop, 속도 상한으로 통신 장애에 대비
- IMU heading/pitch/roll 보정, 최소 duty 탐색, 속도 calibration, motion classification, position estimation 도구 구현
- differential drive 기반 odometry 검증 스크립트 구성
- 1·2층 프레임, 배터리/IMU case, wheel hub, safety shell의 STL을 반복 설계

### 4.9 Infra/DevOps — Docker, Jenkins, Nginx

운영 배포는 frontend, FastAPI, MongoDB를 Docker Compose로 구성하고 호스트 Nginx가 HTTPS endpoint를 reverse proxy한다.

- frontend: Node 20 build → Nginx Alpine serving의 multi-stage Docker image
- backend: Python 3.12 image에 priority engine 산출물을 포함해 환경별 path mount 차이를 제거
- database/media: MongoDB와 media를 named volume으로 영속화
- healthcheck: MongoDB가 준비된 후 API를 시작하도록 dependency 구성
- network: DB는 내부 network에만 두고 frontend/API port는 localhost에 bind
- Jenkins: checkout → frontend type check/test → Python dependency 설치 → backend compile/import sanity → develop 배포 → 공개 URL smoke test
- commit별 image tag와 API/frontend build commit 비교로 잘못된 버전 배포 감지
- `/`, `/api/health`, `/docs`, priority GeoJSON을 실제 public origin으로 확인해 container 내부 정상과 외부 proxy 정상 여부를 함께 검증
- credential file의 legacy Mongo key를 안전하게 정규화하고 shell trace를 꺼 secret log 노출 방지
- bind mount 대신 image/named volume을 사용해 Jenkins container와 host 사이 경로 불일치 문제 해결

## 5. 데이터와 API 흐름

### 점검 세션 흐름

```text
Dashboard가 session 생성
→ Cloud command 등록
→ Local Agent가 command polling/relay
→ Pi 연결·보정·arm
→ frame/sensor 수집
→ VLM + Depth + YOLO + Safety Gate
→ 제한 motor command
→ telemetry/frame/drive log/defect publish
→ session 종료
→ reconstruction/report 생성
```

### 주요 통신 방식

- REST: 세션, 결함, 로그, 보고서, 우선순위, 리소스, 명령 상태
- WebSocket: Agent frame publisher와 dashboard viewer 사이 최신 영상 fan-out
- MJPEG/JPEG: 로컬 카메라와 YOLO overlay 관제
- Static media: 업로드 이미지와 보고서 자산 제공
- JSON/GeoJSON: 위험도 점수, 행정경계, 지도 drill-down

## 6. 품질 관리와 검증

저장소 검색 기준 테스트 선언 수는 다음과 같다. 매개변수화에 따라 실제 실행 case 수와 다를 수 있다.

| 영역 | 테스트 선언 수 | 검증 내용 예시 |
|---|---:|---|
| AI Agent | 239 | safety control, navigation V2, stall, model registry, Pi discovery, WebSocket lifecycle, cloud relay/publisher |
| Frontend | 48 | Agent/camera service, UI safety, priority map E2E |
| LLM Report | 41 | facts, provider, fallback, output validation |
| Cloud API | 31 | Agent command/telemetry/frame, defect idempotency |

그 밖의 검증:

- TypeScript `tsc --noEmit`을 lint gate로 사용
- Python compile/import sanity check를 CI에 포함
- scoring engine 내부 self-test와 raw checksum 검증 도구 제공
- odometry, IMU, heading, motion classification을 hardware-independent script로 검증
- 배포 후 API/frontend commit hash 일치 및 공개 route smoke test

현재 저장소 문서에 기록된 상태상 frontend test는 34 runtime case 중 기존 2건이 실패한다. 원인은 과거 구현 형태를 고정한 `useState` 검사와 self-host font 정책 불일치이며, Jenkins 배포 gate에도 포함되어 있어 정리 전에는 pipeline이 중단될 수 있다. 또한 읍면동 경계 원본/산출물 누락 시 `/api/priority/dongs/geojson`이 404가 되는 알려진 제약이 있다.

## 7. 포트폴리오에서 강조할 문제 해결 경험

1. **비결정적 AI와 물리 제어의 안전한 결합**  
   VLM 출력을 모터에 직접 연결하지 않고 Depth·초음파·IMU·상태 머신·Safety Gate를 통과한 bounded command만 실행했다. Raspberry Pi에도 별도 50Hz guard와 fail-closed 정책을 배치해 네트워크나 AI 장애 시 STOP하도록 설계했다.

2. **다중 공공데이터의 편향을 고려한 설명 가능한 우선순위 모델**  
   단순 건수 집계가 아닌 km당 침하, 전국 백분위, 발생 가능성과 피해 영향도 분리, 지표별 contribution을 적용했다. 결측·원인 미기재·상관 중복을 통계적으로 검토하고 가중치 변경 이력을 사전 등록했다.

3. **실시간 Edge–Cloud–Web 통합**  
   로컬 장치의 영상·센서·판단 로그를 REST/WebSocket으로 Cloud API에 전송하고 React dashboard에서 세션 상태와 함께 표시했다. 연결 단절, 명령 만료, 중복 업로드를 정상 흐름으로 가정해 방어 로직을 구성했다.

4. **개발 환경에서는 재현되지 않던 production 지도 장애 해결**  
   MapLibre worker bundling과 Nginx MIME type이라는 두 원인을 분리해 custom Vite plugin과 서버 설정으로 해결하고, CI smoke test에서 GeoJSON endpoint까지 확인하도록 재발 방지 장치를 추가했다.

5. **배포 환경 경로 차이 제거와 검증 가능한 롤백 구조**  
   Jenkins workspace bind mount가 host에서 빈 경로로 해석되던 문제를 산출물 image bake와 named volume로 해결했다. commit별 image tag, build-info, health commit을 연계해 실제 공개 서비스가 의도한 commit인지 자동 검증했다.

6. **AI 결과의 운영 문서화**  
   결함·주행 facts를 먼저 고정하고 LLM 요약을 생성하며, 모델 실패 시 deterministic fallback을 적용했다. 보고서 결과를 HTML/PDF로 제공해 탐지 결과가 현업 보수 프로세스로 이어지게 했다.

## 8. 포트폴리오용 소개 문구

### 한 줄 소개

> 공공데이터 기반 하수관로 위험도 산정부터 AI 로봇 자율 점검, 실시간 관제, 결함 탐지, 3D 재구성 및 PDF 보고서까지 연결한 AIoT 통합 플랫폼

### 짧은 소개

> PIPE CHECKKK는 노후 하수관로의 점검 우선순위를 데이터로 산정하고, Raspberry Pi 기반 로봇과 로컬 AI Agent가 관로 내부를 안전하게 탐사하도록 만든 시스템입니다. VLM·Depth Anything·YOLO 결과를 초음파·IMU 및 규칙 기반 Safety Gate와 결합해 제한된 동작만 실행하며, 점검 영상과 결함·주행 로그를 FastAPI/MongoDB에 축적합니다. React 관제 화면에서는 위험도 지도, 실시간 상태, 결함 및 보고서를 통합 관리하고 3D Gaussian Splatting 결과까지 연결합니다.

### 이력서용 핵심 bullet 예시

- React 19·MapLibre GL 기반 전국→읍면동 위험도 drill-down 지도와 실시간 로봇 관제 dashboard 구현
- FastAPI·MongoDB 기반 세션/결함/주행 로그/보고서/3D 재구성 도메인 API 및 WebSocket 영상 중계 구축
- VLM·Depth Anything·YOLO와 초음파·IMU safety policy를 결합한 fail-closed 자율주행 Agent 설계
- 10,000장 관로 데이터로 YOLO11 detection/segmentation 학습 pipeline 구축, SAM 2.1 bbox-to-mask pseudo-label 적용
- 공공데이터를 시군구 단위로 통합하고 Poisson GLM·PCA·민감도 분석을 반영한 설명 가능한 위험도 산정 engine 개발
- Docker Compose·Jenkins·Nginx 기반 CI/CD와 commit 단위 배포 검증·rollback 구조 구성
- COLMAP–3D Gaussian Splatting–KSplat/WebGL로 이어지는 관로 3D 재구성 pipeline 연동

## 9. 면접 설명 포인트

### “왜 VLM만으로 주행하지 않았나요?”

VLM은 복잡한 장면 이해에는 유리하지만 출력 지연과 비결정성이 있다. 따라서 VLM은 행동을 제안하고, 실제 실행 여부는 빠르고 예측 가능한 초음파·IMU 규칙과 상태 머신이 결정하도록 책임을 분리했다. 노트북 Agent 장애까지 고려해 최종 STOP 권한은 Raspberry Pi 로컬 guard에도 두었다.

### “우선순위 점수는 어떻게 신뢰할 수 있나요?”

가중치를 코드에 임의로 넣는 데 그치지 않고 등록·개정 이력을 보존했으며, Poisson 회귀와 상관 분석으로 불필요하거나 중복된 지표를 제거했다. 원 건수가 만드는 지역 규모 편향은 km당 발생률로 보정하고, 화면에는 지표별 contribution과 결측 여부를 노출해 결과를 설명할 수 있게 했다.

### “실시간 시스템에서 가장 중요하게 다룬 장애는 무엇인가요?”

통신 단절과 늦게 도착한 명령을 정상적인 실패 조건으로 보았다. heartbeat/watchdog/TTL, idempotent defect upload, WebSocket viewer/publisher 분리, 요청 취소 race 방지, fail-closed STOP을 각각 Edge·Cloud·Web 계층에 적용했다.

### “프로젝트에서 직접 개발한 범위와 오픈소스의 경계는?”

React/FastAPI/Agent orchestration, 안전 정책, 데이터 scoring, 학습·변환 pipeline, Cloud 연동과 dashboard는 프로젝트 구현 영역이다. YOLO, SAM, Depth Anything, Transformers, gsplat, MapLibre 같은 모델·라이브러리는 기반 기술로 활용했으며, 특히 `ai/3d_gsplat`의 CUDA rasterizer와 GLM third-party 코드는 upstream 영역으로 구분해야 한다.

## 10. 현재 한계와 개선 방향

- YOLO validation recall이 precision 대비 낮으므로 hard-negative/rare-case 보강, class별 threshold 조정, 실제 로봇 촬영 도메인 데이터 fine-tuning이 필요하다.
- 학습 로그와 별도로 고정 hold-out test set에 대한 class별 precision/recall/mAP와 실제 관로 현장 성능을 확정해야 한다.
- 읍면동 GeoJSON 원본과 생성 산출물의 배포 경로를 복구하고 checksum 검증에 포함해야 한다.
- 기존 frontend test 2건을 현재 UI/font 정책에 맞게 정리해 CI gate를 정상화해야 한다.
- Agent/Cloud/Frontend가 각각 가진 모델명과 용어를 단일 configuration source로 통합할 필요가 있다.
- 로봇의 실제 주행 성공률, 평균 탐사 거리, emergency stop 발생률, end-to-end latency를 telemetry 기반 KPI로 축적하면 포트폴리오의 정량성이 높아진다.
- operator 인증은 현재 passcode 중심이므로 실제 서비스화 시 계정·권한·감사 로그와 secret rotation을 강화해야 한다.

## 11. 포트폴리오 작성 시 확인할 항목

아래 내용은 코드만으로 개인별 기여를 확정할 수 없으므로 실제 담당 범위에 맞춰 선택해야 한다.

- 본인이 직접 담당한 영역과 기여율
- 프로젝트 기간과 팀 인원/역할
- 실제 사용한 GPU·Raspberry Pi 주변 장치의 정확한 사양
- 현장/시연에서 측정한 주행 성공률, 추론 FPS, API latency
- 최종 YOLO best checkpoint의 test set 성능
- 배포 서버/클라우드 환경의 공개 가능한 범위
- 수상·발표·사용자 피드백 등 외부 성과

---

이 문서는 저장소의 구현과 기록을 근거로 작성했다. README 사이에 모델명이 `YOLO26n`, `YOLO11n`, `GPT VL`, `Qwen-VL/Codex 계열` 등으로 혼재하므로, 외부 포트폴리오에는 실제 시연·배포에 사용한 최종 모델을 팀과 확인한 뒤 하나로 통일하는 것이 안전하다.
