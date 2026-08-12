# AI Agent 결함 이미지 자동 업로드 및 보고서 연동 가이드

## 1. 목표

AI Agent가 주행 중 결함을 탐지하면 다음 작업을 자동으로 수행한다.

1. 결함이 탐지된 영상 프레임을 캡처한다.
2. 캡처 이미지를 Cloud API에 업로드한다.
3. 업로드 결과로 받은 `snapshot_path`와 결함 정보를 MongoDB에 저장한다.
4. 실시간 관제 화면에 새 결함을 표시한다.
5. 점검 종료 후 생성된 보고서가 해당 결함 이미지를 자동으로 표시한다.

최종 흐름은 다음과 같다.

```text
Camera / YOLO
  → AI Agent 결함 탐지
  → 탐지 프레임 JPEG 캡처
  → POST /api/media/captures
  → /media/defects/{uuid}.jpg 반환
  → POST /api/sessions/{session_id}/captures
  → MongoDB defects 저장
  → 프론트 실시간 결함 목록 갱신
  → 보고서에서 snapshot_path 이미지 표시
```

## 2. 서비스별 책임

### AI Agent

- YOLO 추론 결과에서 결함을 판정한다.
- 동일 결함이 여러 프레임에서 반복 저장되지 않도록 중복을 억제한다.
- 결함이 확정된 시점의 원본 또는 주석 프레임을 JPEG/PNG로 생성한다.
- Cloud API 이미지 업로드 API를 호출한다.
- 이미지 업로드 성공 후 결함 저장 API를 호출한다.
- 저장된 결함 정보를 Agent WebSocket 이벤트로 프론트에 전달한다.

### Cloud API

- 이미지 파일을 Docker `media_data` 볼륨에 영구 저장한다.
- `/media/defects/...` 경로로 이미지를 공개한다.
- 결함 메타데이터와 `snapshot_path`를 MongoDB에 저장한다.
- 세션별 결함과 보고서 데이터를 제공한다.

### 프론트엔드

- Agent가 전달한 새 결함 이벤트를 실시간 목록에 반영한다.
- API에서 받은 `snapshot_path`를 공개 이미지 URL로 변환한다.
- 보고서 조회 시 저장된 결함 이미지를 표시한다.
- 자동 탐지 과정에서 이미지를 직접 업로드하지 않는다.

## 3. 현재 사용 가능한 Cloud API

운영 API 주소:

```text
https://i15c102.p.ssafy.io
```

### 이미지 업로드

```http
POST /api/media/captures
Content-Type: multipart/form-data
```

폼 필드:

```text
file: JPEG, PNG 또는 WebP 이미지
```

제한:

- 허용 형식: JPEG, PNG, WebP
- 최대 크기: 10MB

응답 예:

```json
{
  "snapshot_path": "/media/defects/550e8400e29b41d4a716446655440000.jpg"
}
```

### 결함 저장

```http
POST /api/sessions/{session_id}/captures
Content-Type: application/json
```

요청 예:

```json
{
  "defect_type": "BLOCKAGE",
  "severity": "CRITICAL",
  "confidence": 0.94,
  "blockage_ratio": 0.72,
  "approx_position_m": 18.4,
  "snapshot_path": "/media/defects/550e8400e29b41d4a716446655440000.jpg",
  "detected_at": "2026-07-30T03:10:25.000Z"
}
```

결함 종류:

```text
BLOCKAGE
CRACK
SURFACE_DAMAGE
DEBRIS
ROOT
OTHER
```

심각도:

```text
CRITICAL
WARNING
NORMAL
```

주의:

- `confidence`는 `0.0`부터 `1.0` 사이로 전송한다.
- `blockage_ratio`도 `0.0`부터 `1.0` 사이로 전송한다.
- 거리를 알 수 없으면 `approx_position_m`은 `null`로 전송할 수 있다.
- 이미지 업로드가 성공한 뒤 결함 저장 API를 호출해야 한다.

## 4. AI Agent 구현 순서

### 4.1 Cloud 세션 ID 전달

결함은 반드시 점검 세션에 속해야 한다. 현재 프론트는 점검 시작 시 Cloud API에서 세션을 생성하므로, 생성된 `session_id`를 Agent 임무 시작 요청에도 전달해야 한다.

권장 임무 시작 요청:

```json
{
  "area": "광주광역시 하남산단 6번로",
  "cloud_session_id": 12
}
```

Agent는 임무 상태에 다음 값을 보관한다.

```text
cloud_session_id
```

`cloud_session_id`가 없으면 자동 결함 저장을 실행하지 않고 오류 로그를 남긴다.

### 4.2 결함 확정 조건

한 프레임의 탐지만으로 바로 저장하지 않는 것이 좋다.

권장 조건:

- 신뢰도 임계값 이상
- 최근 N개 프레임 중 일정 횟수 이상 탐지
- 동일 클래스와 유사 위치에 대한 cooldown 적용
- IoU 또는 추적 ID를 이용한 동일 결함 중복 제거

예:

```text
confidence >= 0.80
최근 5프레임 중 3프레임 이상 탐지
동일 결함 저장 후 5초 cooldown
```

### 4.3 프레임 생성

결함 확정 시점의 프레임을 JPEG로 인코딩한다.

권장 사항:

- 원본 프레임 또는 YOLO bounding box가 표시된 주석 프레임 사용
- JPEG 품질 85~90
- 최대 해상도 제한
- 파일 크기 10MB 이하 유지

OpenCV 예:

```python
success, encoded = cv2.imencode(
    ".jpg",
    annotated_frame,
    [cv2.IMWRITE_JPEG_QUALITY, 90],
)

if not success:
    raise RuntimeError("결함 프레임 JPEG 변환 실패")

image_bytes = encoded.tobytes()
```

### 4.4 이미지 업로드

Python `httpx` 예:

```python
import httpx

CLOUD_API_ORIGIN = "https://i15c102.p.ssafy.io"


async def upload_defect_image(image_bytes: bytes) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{CLOUD_API_ORIGIN}/api/media/captures",
            files={
                "file": (
                    "capture.jpg",
                    image_bytes,
                    "image/jpeg",
                )
            },
        )
        response.raise_for_status()
        return str(response.json()["snapshot_path"])
```

### 4.5 결함 DB 저장

```python
from datetime import datetime, timezone


async def save_defect(
    session_id: int,
    snapshot_path: str,
    defect_type: str,
    severity: str,
    confidence: float,
    distance_m: float | None,
    blockage_ratio: float | None = None,
) -> dict:
    payload = {
        "defect_type": defect_type,
        "severity": severity,
        "confidence": confidence,
        "blockage_ratio": blockage_ratio,
        "approx_position_m": distance_m,
        "snapshot_path": snapshot_path,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{CLOUD_API_ORIGIN}/api/sessions/{session_id}/captures",
            json=payload,
        )
        response.raise_for_status()
        return dict(response.json())
```

### 4.6 전체 자동 저장 함수

```python
async def persist_detected_defect(
    session_id: int,
    annotated_frame,
    defect_type: str,
    severity: str,
    confidence: float,
    distance_m: float | None,
    blockage_ratio: float | None = None,
) -> dict:
    success, encoded = cv2.imencode(
        ".jpg",
        annotated_frame,
        [cv2.IMWRITE_JPEG_QUALITY, 90],
    )
    if not success:
        raise RuntimeError("결함 이미지 인코딩 실패")

    snapshot_path = await upload_defect_image(encoded.tobytes())

    return await save_defect(
        session_id=session_id,
        snapshot_path=snapshot_path,
        defect_type=defect_type,
        severity=severity,
        confidence=confidence,
        distance_m=distance_m,
        blockage_ratio=blockage_ratio,
    )
```

## 5. Agent WebSocket 이벤트

DB 저장이 성공하면 Agent가 프론트에 다음 이벤트를 전송하는 방식을 권장한다.

```json
{
  "type": "defect.created",
  "defect": {
    "id": 31,
    "session_id": 12,
    "defect_type": "BLOCKAGE",
    "severity": "CRITICAL",
    "confidence": 0.94,
    "blockage_ratio": 0.72,
    "approx_position_m": 18.4,
    "snapshot_path": "/media/defects/550e8400e29b41d4a716446655440000.jpg",
    "repair_status": "BEFORE",
    "action_type": null,
    "review_status": "PENDING",
    "detected_at": "2026-07-30T03:10:25.000Z"
  }
}
```

프론트 `RealTimeMonitor.tsx`는 `payload.type === "defect.created"`를 처리하여 `apiDefectToInspectionItem()`으로 변환한 뒤 결함 목록에 추가한다.

WebSocket 연동 전 임시 방식으로는 활성 세션의 결함 API를 일정 주기로 조회할 수 있다.

```http
GET /api/sessions/{session_id}/defects
```

단, 장기적으로는 불필요한 API 호출을 줄이기 위해 WebSocket 이벤트 방식을 권장한다.

## 6. 보고서 이미지 표시

보고서 화면은 결함 응답의 다음 값을 사용한다.

```json
{
  "snapshot_path": "/media/defects/550e8400e29b41d4a716446655440000.jpg"
}
```

프론트는 이를 다음 주소로 변환한다.

```text
https://i15c102.p.ssafy.io/media/defects/550e8400e29b41d4a716446655440000.jpg
```

현재 프론트의 `resolveMediaUrl()`과 이미지 fallback 처리가 이 역할을 담당한다.

주의:

- 현재 `App.tsx`의 과거 데이터 조회는 최초 진입 시 한 번 실행된다.
- 다른 서비스가 결함을 새로 저장한 직후 보고서 화면을 자동 갱신하려면 재조회, polling 또는 WebSocket 반영이 필요하다.
- 보고서는 점검 종료 후 `POST /api/sessions/{session_id}/reports`로 생성한다.

## 7. 실패 처리

### 이미지 업로드 실패

- 결함 DB 저장을 실행하지 않는다.
- Agent 로그에 업로드 실패 원인을 남긴다.
- 제한 횟수만 재시도한다.

### 이미지 업로드 성공 후 결함 DB 저장 실패

- 이미지가 고아 파일로 남을 수 있다.
- 추후 이미지 삭제 API 또는 주기적인 고아 파일 정리 작업이 필요하다.

### 네트워크 단절

운영 환경에서는 다음 방식이 안전하다.

1. 이미지와 결함 메타데이터를 로컬 큐에 임시 저장한다.
2. 네트워크 복구 후 순서대로 재전송한다.
3. 중복 저장 방지를 위해 Agent 측 고유 이벤트 ID를 사용한다.

현재 Cloud API에는 idempotency key 처리가 없으므로 재시도 시 동일 결함이 중복 생성될 수 있다.

## 8. 서버 구성 확인

Docker 볼륨:

```yaml
cloud-api:
  volumes:
    - media_data:/app/media

volumes:
  media_data:
```

Nginx:

```nginx
location /media/ {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

확인 명령:

```bash
curl -i https://i15c102.p.ssafy.io/api/health
curl -I https://i15c102.p.ssafy.io/media/defects/blockage-01.jpg
```

## 9. 필요한 추가 수정 파일

### AI Agent 프로젝트

Agent의 실제 저장소 경로가 확정되면 다음 영역을 수정한다.

- 임무 상태 모델: `cloud_session_id` 추가
- YOLO 탐지 처리기: 결함 확정 및 중복 제거
- Cloud API 클라이언트: 이미지 업로드와 결함 저장
- WebSocket 이벤트: `defect.created` 추가
- 재시도 및 로컬 큐 처리

### 프론트엔드

```text
frontend/src/components/RealTimeMonitor.tsx
```

- 점검 시작 요청에 `cloud_session_id` 전달
- `defect.created` WebSocket 이벤트 처리
- 자동 탐지 결함을 실시간 목록에 추가

필요하면 다음 API 모듈을 재사용한다.

```text
frontend/src/api/adapters.ts
frontend/src/api/defects.ts
frontend/src/api/media.ts
```

### Cloud API

현재 이미지 업로드와 결함 저장 API는 구현되어 있다. 운영 안정화를 위해 이후 다음 기능을 고려한다.

- 인증 및 권한 확인
- 업로드 rate limit
- 실제 이미지 포맷 검증
- 고아 이미지 삭제 API
- 저장 공간 모니터링
- idempotency key 기반 중복 방지

## 10. 전체 통합 테스트 순서

1. Cloud API와 MongoDB 컨테이너가 실행 중인지 확인한다.
2. `/api/health`가 200인지 확인한다.
3. Agent 점검 시작 시 `cloud_session_id`가 전달되는지 확인한다.
4. YOLO 결함 탐지를 발생시킨다.
5. `/api/media/captures`가 201을 반환하는지 확인한다.
6. `/api/sessions/{session_id}/captures`가 201을 반환하는지 확인한다.
7. 반환된 `/media/defects/...` 이미지가 브라우저에서 보이는지 확인한다.
8. 실시간 관제 화면에 결함이 한 번만 추가되는지 확인한다.
9. 점검을 종료하고 보고서를 생성한다.
10. 보고서 화면에서 결함 이미지가 표시되는지 확인한다.
11. 컨테이너를 재빌드한 뒤에도 이미지가 유지되는지 확인한다.

## 11. 완료 기준

- 동일 결함이 중복 저장되지 않는다.
- 이미지와 결함 DB 레코드가 동일한 `snapshot_path`로 연결된다.
- 컨테이너 재시작 및 재빌드 후에도 이미지가 유지된다.
- 실시간 화면과 보고서 화면에서 동일한 이미지가 표시된다.
- 네트워크 또는 API 실패가 Agent 임무 전체를 중단시키지 않는다.
- 실패 원인이 Agent 로그에 명확하게 기록된다.
