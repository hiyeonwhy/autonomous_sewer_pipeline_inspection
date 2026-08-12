# PipeCheckkk V2 Models

V2 runtime은 아래 두 모델만 사용합니다.

```text
models/
├── depth-anything-v2-small/
│   ├── manifest.json
│   ├── README.md
│   └── weights/          # Hugging Face snapshot, Git 제외
└── qwen2.5-vl-3b-instruct-awq/
    ├── manifest.json
    ├── README.md
    └── weights/          # Hugging Face snapshot, Git 제외
```

- Depth Anything: 좌·중·우 자유공간 판단에 사용합니다.
- Qwen: 진행 상황 안내 문장 생성에만 사용하며 navigation 또는 motor control에는 관여하지 않습니다.

애플리케이션은 Hugging Face 저장소 ID 대신 `pipecheckkk_ai.model_registry`의 안정적인 로컬 이름을 사용합니다.
