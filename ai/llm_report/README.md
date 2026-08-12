# LLM Report — 탐사 보고서 요약 모듈

탐사 세션의 결함 데이터를 로컬 Qwen으로 요약해 **보고서 종합 평가 문단과 조치 권고**를 생성합니다.
현재 백엔드(`backend/cloud_api/app/domain/reports/`)의 `summary`는 결함명을 문자열로 이어 붙이는
수준(`"CRACK CRITICAL, DEBRIS WARNING"`)인데, 이 모듈이 그것을 서술형으로 대체합니다.

## 설계 원칙 — 숫자는 코드, 문장만 모델

| 항목 | 생성 주체 | 이유 |
|---|---|---|
| 결함 수, 심각도 분포 | 코드 | 집계는 틀리면 안 되고 LLM이 필요 없음 |
| 구조적·운영적 상태등급 | 코드 (`facts.compute_grades`) | 등급은 보고서의 근거값 — 모델이 판정하면 안 됨 |
| 종합 평가 문단 | **Qwen** | 서술형 문장은 모델의 영역 |
| 조치 권고 | **Qwen** | 위와 같음 |
| 결함 사진 판독 소견 | **Qwen (비전)** | 사진에 무엇이 보이는지는 모델만 알 수 있음 |

모델은 이미 계산된 등급을 **인용만** 하도록 프롬프트로 제약하고, 출력은 파서가 검증합니다.

## 문체 — 개조식

관공서 조사보고서 문체에 맞춰 **개조식(명사형 종결)** 으로 작성합니다.

```
확인됨 · 산정됨 · 관찰됨 · 검토 필요       ← 사용
확인되었다 · ~한다 · ~습니다               ← 파서가 거부
```

`_DECLARATIVE_PATTERN`이 평서형 종결(`~다.`)을 잡아내며, 모델 출력과 규칙 기반 출력 양쪽에
동일하게 적용됩니다.

## 결함 사진 판독

Qwen2.5-VL은 비전 모델이라 결함 스냅샷을 직접 읽습니다. 결함 1건마다 별도 추론을 돌려
`observations[]`에 소견을 남깁니다.

```
결함 메타데이터 (종류·위치·심각도 확정값)
        +                                  →  Qwen VL  →  판독 소견
결함 사진 (/media/defects/*.jpg)
```

`source` 필드로 출처를 구분합니다.

| `source` | 의미 |
|---|---|
| `vision` | 사진을 실제로 읽고 작성한 소견 |
| `metadata` | 사진 미확보·다운로드 실패·검증 실패 → 메타데이터만으로 작성 |

결함 종류·위치·심각도는 **확정값으로 프롬프트에 주입**하고, 모델은 그 전제 위에서 사진에
보이는 것만 기술합니다. 다른 종류로 판정하면 파서가 거부합니다.

사진 판독은 결함당 수 초가 걸리므로 `MAX_VISION_DEFECTS`(기본 8)로 제한하고, 초과분은
`metadata` 소견으로 처리하면서 경고 로그를 남깁니다.

## 구성

```
llm_report/
├── models.py         Pydantic 모델 (DefectFact, DefectObservation, StateGrades, ReportSummary)
├── facts.py          원본 데이터 정제 + 상태등급 계산 + snapshot_path 검증
├── cloud_client.py   EC2 조회 + 결함 사진 다운로드 (urllib, 의존성 추가 없음)
├── qwen_provider.py  Qwen2.5-VL-3B-AWQ 문장·사진 판독 생성 + 출력 검증
├── fallback.py       모델을 못 쓸 때의 규칙 기반 요약·소견
├── service.py        오케스트레이션 (ReportSummaryService)
├── generate.py       CLI 진입점
└── tests/            단위 테스트 32개
```

## 실행

`ai/` 디렉터리에서 venv를 활성화한 뒤:

```bash
# Qwen으로 요약 + 결함 사진 판독까지
python -m llm_report.generate --session-id 4

# 문단 요약만, 사진 판독은 생략 (결함이 많을 때 빠르게)
python -m llm_report.generate --session-id 4 --no-images

# 모델 없이 규칙 기반만 (GPU 불필요, 통합 확인용)
python -m llm_report.generate --session-id 4 --no-model

# 파일로 저장
python -m llm_report.generate --session-id 4 --output out/summary_4.json
```

Cloud API 주소는 `PIPECHECKKK_CLOUD_API_URL`로 바꿀 수 있습니다
(기본 `https://i15c102.p.ssafy.io`). Qwen weight가 없으면 경고를 출력하고 규칙 기반으로 진행합니다.

### 출력 예 (세션 1, 실제 배포 데이터 · `--no-model`)

```json
{
  "sessionId": 1,
  "assessment": "101번 점검 구역의 이번 탐사에서 결함 1건 확인됨. 심각 등급 결함 1건으로 25.8m 지점 막힘(심각) 등이 해당됨. 구조적 상태등급 0등급(결함 미확인), 운영적 상태등급 4등급(불량)으로 산정됨.",
  "recommendations": [
    "25.8m 지점 막힘에 대한 정밀 조사 및 보수 우선 검토 필요.",
    "통수 능력 저해 구간에 대한 고압 세정 검토 필요."
  ],
  "observations": [
    {
      "defectId": 1,
      "positionM": 25.8,
      "defectType": "BLOCKAGE",
      "severity": "CRITICAL",
      "imageUrl": "https://i15c102.p.ssafy.io/media/defects/blockage-01.jpg",
      "description": "25.8m 지점에서 막힘 확인됨. 심각도 심각 등급으로 분류됨. 단면 폐색률 72%로 측정됨. 탐지 신뢰도 94%. 사진 판독은 미수행 상태이며 메타데이터 기준 소견임.",
      "source": "metadata"
    }
  ],
  "grades": { "structural": 0, "operational": 4 },
  "defectTotal": 1,
  "criticalCount": 1,
  "visionDescribedCount": 0,
  "sourceModel": "rule-based",
  "status": "fallback"
}
```

`status`는 `ready`(Qwen 생성) 또는 `fallback`(규칙 기반)이고,
`visionDescribedCount`는 사진을 실제로 읽은 결함 수입니다.

## 테스트

`pyproject.toml`의 `testpaths = ["tests"]` 때문에 기본 `pytest` 실행에는 포함되지 않습니다.
경로를 명시해 실행하세요.

```bash
pytest llm_report/tests/
```

## 환각 방지 장치

3B 모델은 없는 수치를 만들어내기 쉬워서 출력을 통과시키기 전에 검증합니다
(`qwen_provider.parse_narrative_text`).

- **거리 검증** — 문장에 나오는 `N m` 값이 실제 결함 위치에 없으면 거부. 가장 중요한 장치입니다.
  사진 판독에서는 해당 결함 자신의 위치만 허용합니다.
- **결함 종류 일치** — 판독 소견이 확정된 결함 종류를 언급하지 않으면 거부. 3B 비전 모델이
  다른 결함으로 흘러가는 것이 이 경로의 주된 실패 유형입니다.
- **개조식 강제** — 평서형 종결(`~다.`)이 있으면 거부
- **지시형 금지** — `하세요`, `하십시오` 등이 있으면 거부
- **마크다운 금지** — `**`, `##` 등이 있으면 거부
- **길이 제한** — 평가 30~1200자, 권고 항목당 200자·최대 6개, 판독 소견 15~400자
- **JSON 강제** — 응답에서 JSON 객체를 찾지 못하면 거부

프롬프트에 들어가는 데이터도 `facts.py`에서 미리 정제합니다: enum은 허용 목록으로 강제, 수치는
범위 클램프, 세션 ID·타임스탬프 같은 식별자는 아예 제외.

**`snapshot_path`는 `/media/` 하위만 허용**합니다(`facts.sanitize_snapshot_path`). DB 레코드가
오염되면 절대 URL이나 `..`로 이미지 다운로드를 임의 호스트로 돌릴 수 있어서, 경로 검증과
다운로드 시점 재검증을 모두 겁니다. 이미지 크기는 10MB, content-type은 JPEG/PNG/WebP로 제한합니다.

검증에 실패하면 예외를 던지고 **해당 조각만 규칙 기반으로 강등**합니다. 사진 판독이 실패해도
문단 요약은 남고, 결함 하나의 사진이 깨져도 나머지 결함 판독은 그대로 진행됩니다.

## 판독 모델 — 7B 우선, 3B 대체

보고서 판독은 배치로 돌아 지연에 둔감하므로 Agent보다 큰 모델을 씁니다.

| 우선순위 | 모델 | 받는 법 |
|---|---|---|
| 1 | `Qwen2.5-VL-7B-Instruct-AWQ` | `python scripts/download_models.py --model qwen-report` |
| 2 | `Qwen2.5-VL-3B-Instruct-AWQ` | `--model qwen` (Agent와 공용) |

`resolve_model_dir()`이 받아둔 첫 모델을 고릅니다. **Agent의 실시간 진행 안내는 계속 3B를
씁니다** — 7B로 바꾸면 안내 지연이 늘어나기 때문입니다.

어느 모델이 소견을 썼는지는 출력의 `sourceModel`과 보고서의 `summary_model`에 남습니다.

7B는 weight가 여러 파일로 샤딩되므로 `has_weights()`는 단일 `model.safetensors`를 요구하지
않고 `*.safetensors`가 하나라도 있는지만 봅니다.

## ⚠️ VRAM 주의

`QwenReportProvider`는 weight를 **자체 로드**합니다(7B 약 6GB). Agent 서버가 이미 Qwen을 올린
상태에서 이 CLI를 실행하면 8GB GPU에서 OOM이 납니다.

- **CLI 실행 시 Agent 서버를 먼저 종료**하세요.
- 동시 사용이 필요하면 Agent API(`ai/src/pipecheckkk_ai/api.py`)에 엔드포인트를 추가해
  이미 로드된 provider를 공유하는 방식이 맞습니다. `ReportSummaryService`가 provider를
  주입받는 구조라서 그대로 재사용할 수 있습니다.
- `--no-model`은 GPU를 쓰지 않으므로 Agent 실행 중에도 안전합니다.

## 이 모듈은 오프라인 경로입니다

프론트의 「PDF 내려받기」가 타는 경로는 **여기가 아닙니다**. Cloud API가 GPU 머신에
닿을 수 없어서, 서비스 경로는 백엔드가 원격 LLM API를 직접 부르는 쪽으로 따로
구현돼 있습니다 — [`backend/cloud_api/app/domain/reports/summary/`](../../backend/cloud_api/app/domain/reports/summary/).
사실 정제·상태등급 산정·출력 검증 로직은 이 모듈에서 그대로 옮겨 간 것이라,
`facts.py`나 `qwen_provider.py`의 검증 규칙을 고치면 **양쪽을 같이 고쳐야 합니다.**

| | 이 모듈 (`ai/llm_report`) | 백엔드 (`reports/summary`) |
|---|---|---|
| 실행 | 사람이 CLI로 | 「PDF 내려받기」 요청 시 자동 |
| 모델 | 로컬 Qwen2.5-VL | 원격 API (GMS 등) |
| 용도 | 로컬 실험·프롬프트 튜닝 | 서비스 |

## 현재 범위와 남은 작업

CLI 기준 구현 범위는 **요약 생성과 저장까지**입니다(`--report-id`로 `PATCH
/api/reports/{id}` 호출). 다음은 아직 없습니다.

**① 양식 필드 일부가 DB에 없습니다.** 양식 렌더러 자체는 백엔드에 있습니다
(`backend/cloud_api/app/domain/reports/document.py`). 다음 필드는 아직 채울 소스가 없습니다.

| 양식 필드 | 상태 |
|---|---|
| 조사일자, 조사자, 결함 정보 | ✅ DB에 있음 |
| 구조적·운영적 상태등급 | ✅ 요약 모듈이 계산 |
| 관로 제원, 조사위치, 주행거리 | ✅ `reports` 문서에 스냅샷으로 저장 |
| 사업명, 발주처, 사업기간 | ❌ 설정값으로 주입 필요 |
| 처리구역, 처리분구, 시공자 | ❌ `districts` 컬렉션 미존재 |
| 맨홀번호·종류·재질·깊이·인버트·크기·좌표 | ❌ `manholes` 컬렉션 미존재 |

**② 결함 이미지 첨부.** `snapshot_path`는 있지만 현재 DB의 실제 탐지 결함이 0건입니다
(seed 1건뿐). Agent → EC2 업로드 구간이 이어져야 의미 있는 요약이 나옵니다.

## 상태등급 규칙은 검증이 필요합니다

`facts._grade_for`의 0~5등급 산정 규칙은 **환경부 하수관로 상태평가 기준으로 검증되지 않은
임시 규칙**입니다.

```
결함 없음            → 0
CRITICAL 2건 이상    → 5      CRITICAL 1건 → 4
WARNING  2건 이상    → 3      WARNING  1건 → 2
그 외 (NORMAL만)     → 1
```

결함 분류도 임시입니다 — 구조적: `CRACK`, `SURFACE_DAMAGE` / 운영적: `BLOCKAGE`, `DEBRIS`, `ROOT`.
실제 기준이 확정되면 `_grade_for`와 두 개의 `frozenset`만 교체하면 됩니다.

보고서의 근거값이므로 발표 전에 기준 출처를 확인하고 문서화하는 편이 좋습니다.

## 모델이 탐지할 수 없는 결함

학습된 YOLO 모델의 클래스는 `EX`, `CL`(종방향 균열)입니다([../README.md](../README.md)).
따라서 실제로 저장되는 결함은 대부분 `CRACK`이고, `DEBRIS`·`ROOT`·`BLOCKAGE`는 수동 입력이
아니면 들어오지 않습니다. **운영적 상태등급이 계속 0으로 나오는 것은 버그가 아니라 모델
범위의 한계**입니다.
