# qwen2.5-vl-7b-instruct-awq

- 역할: 보고서 결함 사진 판독 소견 및 종합 평가 생성 (`ai/llm_report`)
- 원본: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`
- 양자화: AWQ 4-bit
- 받는 법: `python scripts/download_models.py --model qwen-report`
- `--model all`에는 포함되지 않습니다. 용량이 커서 필요할 때만 받습니다.

## 3B와의 관계

Agent의 실시간 진행 안내는 계속 `qwen2.5-vl-3b-instruct-awq`를 씁니다. 보고서 판독은
배치로 돌아 지연에 둔감하므로 더 큰 모델을 씁니다.

`llm_report`는 7B를 먼저 찾고 없으면 3B로 내려갑니다. 어느 모델이 소견을 썼는지는
보고서의 `summary_model` 필드에 기록됩니다.

## VRAM 주의

8GB GPU에서 Agent 서버와 동시에 올리면 OOM입니다. 보고서 요약을 돌릴 때는 Agent를
먼저 종료하십시오. `--no-model` 또는 `--no-images`는 GPU를 쓰지 않습니다.
