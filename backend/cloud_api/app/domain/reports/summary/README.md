# reports/summary — 보고서 서술형 요약

프론트의 「PDF 내려받기」가 타는 경로입니다. `GET /api/reports/{id}/document.pdf`가
들어왔을 때 그 보고서에 `assessment`가 없으면 여기서 LLM을 돌려 채우고, 채워진
문서로 양식을 렌더합니다.

```
[PDF 내려받기]
      │
      ▼
GET /reports/{id}/document.pdf
      │
      ├─ assessment 있음 ──────────────────────────────► 바로 렌더 (LLM 미호출)
      │
      └─ 없음 → ensure_report_summary()
                    │
                    ├─ facts.py      결함 정제 + 상태등급 계산   (코드)
                    ├─ provider.py   원격 LLM 호출               (모델)
                    ├─ prompts.py    출력 검증                   (코드)
                    ├─ fallback.py   실패 조각만 규칙 기반 강등   (코드)
                    └─ reports 문서에 $set → 렌더
```

두 번째 다운로드부터는 저장된 요약을 그대로 씁니다. 다시 만들려면 `?refresh=true`.

## 설계 원칙 — 숫자는 코드, 문장만 모델

| 항목 | 생성 주체 |
|---|---|
| 결함 수, 심각도 분포 | 코드 |
| 구조적·운영적 상태등급 | 코드 (`facts.compute_grades`) |
| 종합 평가 문단 · 조치 권고 | 모델 |
| 결함 사진 판독 소견 | 모델 (비전) |

모델은 이미 계산된 등급을 **인용만** 하도록 프롬프트로 제약하고, 출력은
`prompts.py`의 파서가 검증합니다.

## 환각 방지 장치 (`prompts.py`)

검증에 실패하면 `ValueError`를 던지고 **해당 조각만** 규칙 기반으로 강등합니다.
사진 판독이 실패해도 문단 요약은 남고, 결함 하나의 사진이 깨져도 나머지 판독은
그대로 진행됩니다.

- **거리 검증** — 문장에 나오는 `N m` 값이 실제 결함 위치에 없으면 거부. 가장 중요한 장치.
- **결함 종류 일치** — 판독 소견이 확정된 결함 종류를 언급하지 않으면 거부
- **개조식 강제** — 평서형 종결(`~다.`)이 있으면 거부
- **지시형·마크다운 금지**, **길이 제한**, **JSON 강제**

프롬프트에 들어가는 데이터도 `facts.py`에서 미리 정제합니다: enum은 허용 목록으로
강제, 수치는 범위 클램프, 결함은 30건에서 자르고 나머지는 개수만 보고.
`snapshot_path`는 `/media/` 하위만 허용하고, 파일을 여는 시점에
`document.resolve_media_file`이 media_root 밖으로 나가지 않는지 재검증합니다.

## 설정

`.env` (예시는 `.env.example`):

| 변수 | 의미 |
|---|---|
| `LLM_API_BASE_URL` | 게이트웨이 주소. `/chat/completions`를 붙여 호출한다 |
| `LLM_API_KEY` | `Authorization: Bearer`로 보낸다 |
| `LLM_MODEL` | 문단 요약 모델 |
| `LLM_VISION_MODEL` | 사진 판독 모델. 비우면 `LLM_MODEL` |
| `LLM_DESCRIBE_IMAGES` | `false`면 사진 판독을 건너뛴다 |
| `LLM_MAX_VISION_DEFECTS` | 사진 판독 상한(기본 8). 초과분은 메타데이터 소견 |
| `LLM_ENABLED` | `false`면 전부 규칙 기반 |

**키가 없으면 규칙 기반으로 내려가고 PDF는 그대로 나옵니다.** 설정 누락이 다운로드를
막지는 않습니다. 어느 경로로 나왔는지는 보고서의 `summary_model` 필드에 남습니다
(`rule-based` 또는 모델 이름).

## 다른 규격의 게이트웨이로 바꾸려면

`provider.py`의 `OpenAICompatibleProvider`는 `POST {base_url}/chat/completions`를
가정합니다. 규격이 다르면 `ChatProvider`를 만족하는 클래스를 하나 더 쓰고
`build_provider()`가 그것을 돌려주게 하면 됩니다. `service.py`는 손대지 않습니다.

```python
class ChatProvider(Protocol):
    name: str
    supports_vision: bool
    def complete(self, *, system_prompt, user_payload, image, max_tokens) -> str: ...
```

`image`는 `(media_type, bytes)`입니다. OpenAI 규격에서는 data URI로 실어 보내고,
게이트웨이가 비전을 지원하지 않으면 사진 판독만 실패하고 문단 요약은 남습니다.

## `ai/llm_report`와의 관계

같은 로직이 두 벌 있습니다. [`ai/llm_report/`](../../../../../../ai/llm_report/)는
로컬 Qwen2.5-VL로 도는 CLI(프롬프트 실험용)이고, 이 모듈은 원격 API로 도는 서비스
경로입니다. Cloud API가 GPU 머신에 인바운드로 닿을 수 없어서 갈라졌습니다.

**검증 규칙이나 등급 산정을 고치면 양쪽을 같이 고쳐야 합니다** —
`facts._grade_for`, `prompts._reject_shared_style_violations`, 두 시스템 프롬프트가
대응됩니다.

## 알려진 한계

- **첫 다운로드가 느립니다.** 문단 요약 1회 + 사진 판독 최대 8회가 동기로 돕니다.
  결함이 많으면 수십 초가 걸릴 수 있습니다. 프론트 버튼은 그동안 `PDF 생성 중…`으로
  비활성화됩니다.
- 요약 생성은 `run_in_threadpool`에서 돌기 때문에, 동시에 여러 보고서를 뽑으면
  Starlette 스레드풀(기본 40)을 그만큼 오래 점유합니다.
- 상태등급 0~5 산정 규칙(`facts._grade_for`)은 **환경부 하수관로 상태평가 기준으로
  검증되지 않은 임시 규칙**입니다. 발표 전에 기준 출처를 확인하는 편이 좋습니다.
- YOLO가 탐지하는 클래스가 `EX`/`CL`(균열) 뿐이라 실제 저장되는 결함은 대부분
  `CRACK`입니다. **운영적 상태등급이 계속 0으로 나오는 것은 버그가 아니라 모델 범위의
  한계**입니다.
