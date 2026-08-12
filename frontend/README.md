# 스마트 하수관로 관리 시스템

React 19, TypeScript, Vite 기반 PipeCheckkk 관제 대시보드입니다.

## 주요 기능

- Raspberry Pi 카메라 및 YOLO 실시간 영상
- AI Agent 연결 상태, 미션, 주행·센서 정보 표시
- FastAPI 세션·결함·보고서 조회
- 점검 로봇 및 승인 운영자 선택
- 결함 검토와 점검 보고서 관리

## 로컬 실행

```bash
npm install
npm run dev
```

기본 개발 서버 주소는 `http://localhost:3000`입니다.

환경 설정은 [.env.example](./.env.example)을 참고하여 `.env`에 작성합니다.

## Backend 모드

```env
VITE_API_BASE_URL=/backend-api
VITE_USE_MOCK=false
```

Mock 데이터로 실행하려면 다음과 같이 설정합니다.

```env
VITE_USE_MOCK=true
```

API 응답 형태와 enum 값은 [backend-contract/](./backend-contract/README.md)를 참고하세요.
(예전에 이 자리에 있던 `FASTAPI_INTEGRATION.md` 링크는 파일이 저장소에 없어 제거했습니다.)

## 카메라 및 Agent

```env
VITE_CAMERA_STREAM_URL=http://192.168.100.246:8081/stream.mjpg
VITE_CAMERA_HEALTH_URL=http://192.168.100.246:8081/health
VITE_AGENT_API_URL=http://127.0.0.1:8000
VITE_AGENT_WS_URL=ws://127.0.0.1:8000/ws/events
VITE_YOLO_STREAM_URL=http://127.0.0.1:8000/api/vision/yolo-stream.mjpg
```

## 배포 빌드 (Docker)

`Dockerfile`은 multi-stage입니다. `node:20-alpine`에서 `npm ci && npm run build`를
돌리고, 결과 `dist/`를 `nginx:alpine`에 얹어 컨테이너 내부 80 포트로 서빙합니다.
호스트 nginx가 `127.0.0.1:8002`로 프록시하므로 compose의 포트 매핑을 바꾸면
컨테이너는 멀쩡한데 사이트 전체가 502가 됩니다. 전체 구조는
[../infra/README.md](../infra/README.md)에 있습니다.

```bash
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t sewer-cloud-frontend .
```

`GIT_COMMIT`은 `dist/build-info.json`에 박혀 `/build-info.json`으로 노출됩니다.
배포된 커밋을 컨테이너 밖에서 확인하는 유일한 수단이라, 빼먹으면 `unknown`이 찍히고
Jenkins `Verify Deploy`가 실패합니다.

`nginx.conf`의 캐시 정책: `/assets/`는 vite가 해시를 붙이니 1년 immutable,
`index.html`과 `build-info.json`은 재검증·no-store(캐시되면 배포해도 구버전이
남습니다), `/maplibre/`는 아래를 참고하세요.

### maplibre 워커 — 건드리면 지도가 조용히 죽습니다

우선순위 지도 폴리곤이 **배포에서만** 하나도 안 그려지던 문제가 있었습니다
(2026-08-03). 원인이 두 개였고 둘 다 개발 서버에서는 드러나지 않습니다.

1. maplibre-gl 6은 워커 경로를 런타임에 조립합니다
   (`new URL('./maplibre-gl-worker.mjs', import.meta.url)`). 문자열이 정적으로
   드러나지 않아 rollup이 워커를 청크로 내보내지 못하고, 프로덕션에서는
   `import.meta.url`이 `/assets/index-*.js`로 풀려 워커가 404가 됩니다. 개발
   서버는 `optimizeDeps.exclude` 덕에 `node_modules` 원본을 서빙하니 형제 파일이
   그대로 있어 정상으로 보입니다.
   → `vite.config.ts`의 `maplibreWorkerAssets` 플러그인이 워커와 그 shared 청크를
   `dist/maplibre/`로 복사하고, `PriorityMap.tsx`가 프로덕션에서만
   `maplibregl.setWorkerUrl()`로 그 경로를 지정합니다.
2. nginx 기본 `mime.types`에 `.mjs` 매핑이 없어 `application/octet-stream`으로
   나가고, 브라우저는 **MIME이 JS가 아닌 모듈 스크립트의 실행을 거부합니다.**
   HTTP 200으로 받아가면서 워커만 빈 메시지로 죽으니 추적이 특히 어렵습니다.
   → `nginx.conf`의 `location ^~ /maplibre/`가 `default_type application/javascript`를
   줍니다.

두 파일(`maplibre-gl-worker.mjs`, `maplibre-gl-shared.mjs`)은 워커가 서로를 상대
경로로 import하므로 **같은 디렉터리에, 해시 없는 고정 이름**으로 있어야 맞물립니다.
maplibre 버전을 올려 파일명이 바뀌면 플러그인이 빌드를 멈춥니다 — 조용히 넘기면
배포 후에야 404로 드러나기 때문에 일부러 그렇게 했습니다.

증상이 재발하면 이 두 가지를 먼저 확인

```bash
curl -sI https://i15c102.p.ssafy.io/maplibre/maplibre-gl-worker.mjs   # 200 + application/javascript
curl -sI https://i15c102.p.ssafy.io/maplibre/maplibre-gl-shared.mjs   # 200 + application/javascript
```

## 검증

```bash
npm run lint
npm test
npm run build
```

Jenkins의 `Frontend Verify` 단계가 같은 명령을 돌립니다.

`npm test`는 현재 **34건 중 2건이 실패합니다.** 이번 배포 작업과 무관하게 이전부터
깨져 있던 것으로, 변경 전 커밋에서도 동일하게 실패하는 것을 확인했습니다.

* App.tsx에 `useState<number>(0)`이 있을 것으로 기대하는 테스트
* 대시보드가 런타임에 Google Fonts를 요구하지 않아야 한다는 테스트 —
  `public/fonts/AritaDotumKR-*.ttf`를 self-host하려던 작업이 `@font-face` 선언 없이
  중단돼 있어 실패합니다. 지금 그 폰트 파일은 저장소 어디서도 참조되지 않고,
  실제로 쓰이는 폰트는 프로젝트에 포함된 Noto Sans KR입니다.

파이프라인이 이 단계에서 멈추면 Deploy까지 가지 않습니다. Jenkins로 배포하려면
두 테스트를 먼저 정리해야 합니다.
