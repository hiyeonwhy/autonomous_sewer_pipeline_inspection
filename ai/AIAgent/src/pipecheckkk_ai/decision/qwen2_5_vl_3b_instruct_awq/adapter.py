from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from pipecheckkk_ai.models import (
    DecisionProposal,
    MissionState,
    ProgressAnnouncement,
    ProgressEventType,
    utc_now,
)


def parse_proposal_text(text: str, *, latency_ms: float) -> DecisionProposal:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        return DecisionProposal.model_validate(
            {
                **payload,
                "modelName": Qwen25Vl3BAwqProvider.name,
                "latencyMs": latency_ms,
            }
        )
    raise ValueError("Qwen response did not contain a valid JSON proposal")


def parse_progress_announcement_text(
    text: str,
    *,
    requested_event: ProgressEventType,
    mission_id: str | None,
    mission_state: MissionState,
    latency_ms: float,
) -> ProgressAnnouncement:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event_type = ProgressEventType(payload.get("eventType"))
        if event_type is not requested_event:
            raise ValueError("Qwen response event type did not match the request")
        message = str(payload.get("message", "")).strip()
        if not message:
            raise ValueError("Qwen response message was empty")
        required_terms = {
            ProgressEventType.ROBOT_ACTION: (),
            ProgressEventType.DEFECT_DETECTED: ("결함", "발견"),
            ProgressEventType.OBSTACLE_DETECTED: ("장애물", "인식"),
            ProgressEventType.OBSTACLE_AVOIDANCE_COMPLETED: ("장애물", "회피 완료"),
            ProgressEventType.EXPLORATION_UNAVAILABLE: ("탐사", "불가"),
        }[requested_event]
        if any(term not in message for term in required_terms) or any(
            forbidden in message
            for forbidden in ("하세요", "하십시오", "피하세요", "탐사를 불가능")
        ):
            raise ValueError("Qwen response did not contain required status terms")
        if "\n" in message or len(message) > 20:
            raise ValueError("Qwen status message must be one line and at most 20 characters")
        return ProgressAnnouncement(
            mission_id=mission_id,
            mission_state=mission_state,
            event_type=event_type,
            message=message,
            generated_at=utc_now(),
            latency_ms=latency_ms,
            status="ready",
        )
    raise ValueError("Qwen response did not contain a valid progress announcement")


def _bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    if not math.isfinite(number):
        return minimum
    return min(maximum, max(minimum, number))


def sanitize_progress_facts(
    event_type: ProgressEventType,
    facts: dict[str, object],
) -> dict[str, object]:
    """Return only application-owned facts that are safe to place in a model prompt."""
    if event_type is ProgressEventType.ROBOT_ACTION:
        return {
            "situation": str(facts.get("situation", ""))[:120],
            "decidedAction": str(facts.get("decidedAction", ""))[:60],
            "request": "상황과 확정 동작을 20자 이내 한 줄로 요약해줘",
        }
    if event_type is ProgressEventType.OBSTACLE_DETECTED:
        direction = str(facts.get("avoidanceDirection", "UNKNOWN")).upper()
        if direction not in {"LEFT", "RIGHT", "UNKNOWN"}:
            direction = "UNKNOWN"
        return {
            "obstacleDetected": True,
            "pathBlockedProbability": round(
                _bounded_number(
                    facts.get("pathBlockedProbability"),
                    minimum=0.0,
                    maximum=1.0,
                ),
                3,
            ),
            "avoidanceDirection": direction,
        }
    if event_type is ProgressEventType.OBSTACLE_AVOIDANCE_COMPLETED:
        return {"obstacleAvoidanceCompleted": True}
    if event_type is ProgressEventType.EXPLORATION_UNAVAILABLE:
        return {"explorationUnavailable": True}

    defect_type = str(facts.get("defectType", "OTHER")).upper()
    allowed_defect_types = {
        "CRACK",
        "DEBRIS",
        "ROOT",
        "BLOCKAGE",
        "SURFACE_DAMAGE",
        "OTHER",
    }
    if defect_type not in allowed_defect_types:
        defect_type = "OTHER"
    return {
        "defectType": defect_type,
        "confidence": round(
            _bounded_number(facts.get("confidence"), minimum=0.0, maximum=1.0),
            3,
        ),
        "locationM": round(
            _bounded_number(facts.get("locationM"), minimum=0.0, maximum=10000.0),
            3,
        ),
    }


class Qwen25Vl3BAwqProvider:
    name = "qwen2.5-vl-3b-instruct-awq"

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = model_dir or (
            Path(__file__).parents[4]
            / "models"
            / "qwen2.5-vl-3b-instruct-awq"
            / "weights"
        )
        self._model: Any = None
        self._processor: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    def is_downloaded(self) -> bool:
        required = ("config.json", "model.safetensors", "preprocessor_config.json")
        return all((self.model_dir / filename).is_file() for filename in required)

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.is_downloaded():
            raise FileNotFoundError(f"Qwen model snapshot is incomplete: {self.model_dir}")
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        self._processor = AutoProcessor.from_pretrained(
            self.model_dir,
            local_files_only=True,
            min_pixels=256 * 28 * 28,
            max_pixels=768 * 28 * 28,
        )
        self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_dir,
            device_map="auto",
            torch_dtype=torch.float16,
            local_files_only=True,
        )
        if self._model.config.tie_word_embeddings:
            embedding = self._model.model.embed_tokens
            lm_head = torch.nn.Linear(
                embedding.embedding_dim,
                embedding.num_embeddings,
                bias=False,
                device="meta",
                dtype=embedding.weight.dtype,
            )
            lm_head.weight = embedding.weight
            self._model.lm_head = lm_head

    def generate_progress_announcement(
        self,
        *,
        event_type: ProgressEventType,
        mission_id: str | None,
        mission_state: MissionState,
        facts: dict[str, object],
    ) -> ProgressAnnouncement:
        import torch

        self.load()
        assert self._processor is not None
        assert self._model is not None
        system_prompt = (
            "관제 상태를 입력 사실만으로 보고한다. 새 동작 지시 금지. "
            "message 값은 한국어 한 줄, 공백 포함 최대 20자다. "
            "출력 전 글자 수를 확인하고 21자 이상은 절대 출력하지 않는다. 필수어: "
            "OBSTACLE_DETECTED=장애물·인식, "
            "OBSTACLE_AVOIDANCE_COMPLETED=장애물·회피 완료, "
            "EXPLORATION_UNAVAILABLE=탐사·불가, DEFECT_DETECTED=결함·발견. "
            "없는 정보와 지시형 표현을 만들지 않는다. "
            '반드시 {"eventType":"이벤트","message":"보고 문장"} JSON만 출력한다.'
        )
        request_payload = {
            "eventType": event_type.value,
            "facts": sanitize_progress_facts(event_type, facts),
            "outputConstraints": {
                "language": "ko",
                "singleLine": True,
                "maxCharacters": 20,
            },
        }
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(request_payload, ensure_ascii=False),
                    }
                ],
            },
        ]
        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._processor(text=[prompt], padding=True, return_tensors="pt")
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=32,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0
        trimmed = [
            output[len(input_ids) :]
            for input_ids, output in zip(inputs["input_ids"], generated)
        ]
        response = self._processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        return parse_progress_announcement_text(
            response,
            requested_event=event_type,
            mission_id=mission_id,
            mission_state=mission_state,
            latency_ms=latency_ms,
        )
