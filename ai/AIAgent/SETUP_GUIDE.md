# PipeCheckkk AI 모델 설치 및 서버 실행 안내

이 문서는 새 컴퓨터에서 **로컬 AI 서버 + Qwen Dashboard 안내 + Frontend**를 준비하는 절차입니다. 실제 AI runtime은 Raspberry Pi Motor Edge 연결이 필수이며, bridge 없는 app은 단위 테스트에서만 사용합니다.

## 1. 권장 환경

- Linux
- Python `3.11` (`3.12+`는 현재 `pyproject.toml` 범위 밖)
- Node.js 20 이상
- Qwen 사용 시 NVIDIA GPU 권장
  - RTX 4070 Laptop 8GB VRAM에서 검증됨
  - CPU 실행은 매우 느리므로 권장하지 않음
- 모델 다운로드를 위해 여유 디스크 5GB 이상 권장

## 2. 필요한 모델

| 역할 | 모델 | 저장 위치 | 크기(약) | 준비 방법 |
|---|---|---|---:|---|
| 자유공간/깊이 | `depth-anything/Depth-Anything-V2-Small-hf` | `ai/AIAgent/models/depth-anything-v2-small/weights/` | 0.09 GiB | 다운로드 script |
| Qwen 진행 안내 | `Qwen/Qwen2.5-VL-3B-Instruct-AWQ` | `ai/AIAgent/models/qwen2.5-vl-3b-instruct-awq/weights/` | 3.18 GiB | 다운로드 script |
### 자동 다운로드 정책

서버 실행 중에는 모델을 자동 다운로드하지 않습니다. Depth/Qwen adapter는 로컬 파일만 사용하며, 누락 시 해당 기능을 사용할 수 없습니다. 다음 script를 명시적으로 한 번 실행해야 합니다.

모델 weight, Hugging Face cache, `.env`, 가상환경은 Git에 commit하지 않습니다.

## 3. AI 환경 설치

```bash
cd /path/to/S15P11C102/ai
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'AIAgent[dev,models]'
```

NVIDIA GPU 확인:

```bash
nvidia-smi
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

`torch.cuda.is_available()`이 `False`이면 PyTorch 공식 설치 선택기에서 현재 CUDA 환경에 맞는 wheel을 먼저 설치한 뒤 프로젝트 dependency를 설치합니다.

## 4. 모델 다운로드

목록만 확인:

```bash
cd /path/to/S15P11C102/ai
.venv/bin/python AIAgent/scripts/download_models.py --list
```

Depth + Qwen 전체 다운로드:

```bash
.venv/bin/python AIAgent/scripts/download_models.py --model all
```

개별 다운로드:

```bash
.venv/bin/python AIAgent/scripts/download_models.py --model depth
.venv/bin/python AIAgent/scripts/download_models.py --model qwen
```

다운로드 script는 재현 가능한 Hugging Face revision을 사용합니다.

- Depth: `5426e4f0f36572d16453bbda7a8389317b1bef99`
- Qwen: `e7b623934290c5a4da0ee3c6e1e57bfb6b5abbf2`

## 5. Frontend 연결 및 실행

새 terminal에서:

```bash
cd /path/to/S15P11C102/frontend
npm ci
VITE_AGENT_API_URL=http://127.0.0.1:8000 \
VITE_AGENT_WS_URL=ws://127.0.0.1:8000/ws/events \
npm run dev
```

브라우저에서 Vite가 출력한 주소(기본 `http://localhost:3000`)를 엽니다. Dashboard의 **진행 상황 요약** 카드는 다음 경로를 사용합니다.

```text
deterministic event detector
→ ProgressAnnouncementService
→ Qwen/Qwen2.5-VL-3B-Instruct-AWQ
→ GET /api/progress-summary + progress.summary WebSocket
→ Dashboard 진행 상황 요약
```

Qwen 출력은 안내 문장으로만 사용하며 navigation 또는 motor control 입력으로 사용하지 않습니다.

## 6. Real Motor 필수 실행 모드

실제 AI runtime은 Raspberry Pi Motor Edge 연결을 필수로 요구합니다. Edge URL 또는
token이 없으면 서버가 fail-closed로 시작되지 않습니다. `create_app()`의 bridge 없는
형태는 단위 테스트 dependency injection에만 사용합니다.

`ai/AIAgent/.env.example`을 복사해 실제 값으로 채웁니다. `.env`는 Git에 올리지 않습니다.

```bash
cd /path/to/S15P11C102/ai/AIAgent
cp .env.example .env
```

필수 항목:

```dotenv
PIPECHECKKK_EDGE_URL=http://192.168.100.246:8765
PIPECHECKKK_EDGE_TOKEN=PI와_AI서버에_동일하게_설정한_토큰
PIPECHECKKK_CAMERA_SNAPSHOT_URL=http://192.168.100.246:8081/snapshot.jpg
```

Pi의 `/home/ssafy/sewer-robot-edge/motor.env`에는 다음을 추가합니다.

```dotenv
PIPECHECKKK_IMU_ENABLED=true
PIPECHECKKK_IMU_BUS=1
PIPECHECKKK_IMU_ADDRESS=0x68
```

`pipecheckkk-motor.service` 재시작 후 `GET http://PI_IP:8765/health`에서
`armed=false`, `imu.available=true`를 확인합니다. V2 점검 시작 요청이 motor arm보다 먼저
100회 정지 보정을 수행하고 현재 yaw/roll/pitch를 상대 `0°`로 캡처합니다.

실행:

```bash
chmod +x scripts/run_real_motor.sh
scripts/run_real_motor.sh
```

`PIPECHECKKK_EDGE_URL`과 `PIPECHECKKK_EDGE_TOKEN`은 모두 필수입니다. AI API는 기본적으로
`127.0.0.1:8000`에만 bind합니다. no-motor runtime launcher는 제공하지 않습니다.

## 7. 테스트

```bash
cd /path/to/S15P11C102/ai
.venv/bin/python -m pytest -q AIAgent/tests
.venv/bin/python -m ruff check AIAgent/src AIAgent/tests AIAgent/scripts

cd ../frontend
npm test
npm run lint
npm run build
```
