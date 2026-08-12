from __future__ import annotations

import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .facts import allowed_distances
from .models import (
    DEFECT_TYPE_LABELS,
    SEVERITY_LABELS,
    DefectFact,
    DefectObservation,
    ReportNarrative,
    SessionFacts,
    StateGrades,
)

# 개조식(명사형 종결) — 관공서 조사보고서 문체.
_STYLE_RULE = (
    "문장은 개조식으로 끝낸다. '확인됨', '산정됨', '관찰됨', '검토 필요'처럼 "
    "명사형으로 종결하고, '확인되었다', '~한다', '~습니다' 같은 평서형 종결은 쓰지 않는다. "
)

SYSTEM_PROMPT = (
    "너는 하수관로 점검 보고서의 종합 평가 문단 작성기다. "
    "입력 JSON에 있는 사실만 사용해 한국어로 작성한다. "
    "assessment는 관로 상태를 서술하는 3~5문장으로 쓰고, "
    "recommendations는 조치 권고를 1~4개 항목으로 각각 한 문장씩 쓴다. "
    + _STYLE_RULE
    + "입력에 없는 거리, 수치, 결함 종류, 관 규격, 맨홀 정보를 만들지 않는다. "
    "상태등급은 이미 계산되어 입력으로 주어지므로 다시 판정하지 않고 그대로 인용한다. "
    "보고서 문체를 유지하고 이모지나 마크다운 기호를 쓰지 않는다. "
    '반드시 {"assessment":"평가 문단","recommendations":["권고1","권고2"]} JSON만 출력한다.'
)

DEFECT_SYSTEM_PROMPT = (
    "너는 하수관로 내부 CCTV 결함 사진의 판독 소견 작성기다. "
    "첨부된 사진을 보고 관찰되는 상태를 한국어 2~4문장으로 기술한다. "
    "결함의 종류·위치·심각도는 입력 JSON에 확정된 값으로 주어지므로 그것을 전제로 기술하고, "
    "다른 종류의 결함으로 판정하지 않는다. "
    + _STYLE_RULE
    + "결함이 화면에서 차지하는 면적 비율을 반드시 정수 퍼센트로 명시하고 "
    "'화면 면적의 40%(육안 추정)'처럼 육안 추정임을 함께 표기한다. "
    "결함의 위치도 '화면 하부 좌측', '관 상부 12시 방향'처럼 구체적으로 기술한다. "
    "'상당 부분', '대부분', '다수', '약간', '조금' 같은 모호한 표현을 쓰지 않고 "
    "수치나 구체적 위치로 대신한다. "
    "사진에서 실제로 보이는 것만 기술한다. 관 규격, 관종, 맨홀 정보, 거리 수치는 "
    "입력에 있는 것 외에 만들지 않는다. "
    "입력에 폐색률이 주어졌다면 그 값은 측정값이므로 화면 면적 추정치와 구분해 인용한다. "
    "보수 공법을 단정하지 않고 관찰 소견만 남긴다. "
    "이모지나 마크다운 기호를 쓰지 않는다. "
    '반드시 {"description":"판독 소견"} JSON만 출력한다.'
)

_FORBIDDEN_PHRASES = ("하세요", "하십시오", "해주세요", "바랍니다")
_MARKDOWN_MARKERS = ("**", "##", "- ", "* ")
# "25.8m"는 잡고 "40%"는 넘긴다 — 퍼센트 뒤 m가 붙는 경우가 없으므로 안전.
_DISTANCE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*m\b")
# 평서형 종결("~다." / 문장 끝 "~다") 탐지 — 개조식 위반 신호.
_DECLARATIVE_PATTERN = re.compile(r"다\s*[.!?]|다\s*$")
# 판독 소견은 면적 비율을 수치로 남겨야 한다.
_PERCENT_PATTERN = re.compile(r"\d+\s*%")
# 정량 표현을 대체해 버리는 모호어. "약 40%"의 '약'과 충돌하지 않도록 '약간'만 잡는다.
_VAGUE_QUANTIFIERS = ("상당", "대부분", "다수", "약간", "조금", "많은 양")

MIN_ASSESSMENT_CHARS = 30
MAX_ASSESSMENT_CHARS = 1200
MAX_RECOMMENDATION_CHARS = 200
MAX_RECOMMENDATIONS = 6
MIN_DESCRIPTION_CHARS = 15
MAX_DESCRIPTION_CHARS = 400


def _reject_shared_style_violations(text: str, allowed_positions: set[str]) -> None:
    """Guards applied to every model-authored string."""
    if any(phrase in text for phrase in _FORBIDDEN_PHRASES):
        raise ValueError("Qwen text used imperative phrasing")
    if any(marker in text for marker in _MARKDOWN_MARKERS):
        raise ValueError("Qwen text contained markdown formatting")
    if _DECLARATIVE_PATTERN.search(text):
        raise ValueError("Qwen text used declarative endings instead of 개조식")

    cited = {match.group(1) for match in _DISTANCE_PATTERN.finditer(text)}
    invented = {value for value in cited if value not in allowed_positions}
    if invented:
        raise ValueError(f"Qwen text cited unknown positions: {sorted(invented)}")


def _first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def build_prompt_payload(facts: SessionFacts, grades: StateGrades) -> dict[str, Any]:
    """Assemble the only session data the model is allowed to see."""
    return {
        "districtId": facts.district_id,
        "durationSeconds": facts.duration_seconds,
        "defectTotal": facts.defect_total,
        "omittedDefectCount": facts.dropped_defect_count,
        "structuralGrade": grades.structural,
        "operationalGrade": grades.operational,
        "defects": [
            {
                "positionM": defect.position_m,
                "type": DEFECT_TYPE_LABELS[defect.defect_type],
                "severity": SEVERITY_LABELS[defect.severity],
                "confidence": defect.confidence,
                **(
                    {"blockageRatio": defect.blockage_ratio}
                    if defect.blockage_ratio is not None
                    else {}
                ),
            }
            for defect in facts.defects
        ],
    }


def build_defect_payload(defect: DefectFact) -> dict[str, Any]:
    """Assemble the confirmed facts that frame one snapshot reading."""
    payload: dict[str, Any] = {
        "positionM": defect.position_m,
        "type": DEFECT_TYPE_LABELS[defect.defect_type],
        "severity": SEVERITY_LABELS[defect.severity],
        "confidence": defect.confidence,
    }
    if defect.blockage_ratio is not None:
        payload["blockageRatio"] = defect.blockage_ratio
    return payload


def parse_narrative_text(text: str, *, facts: SessionFacts) -> ReportNarrative:
    """Extract and validate the model's session-level JSON narrative.

    Raises ValueError on anything unusable so the caller can fall back rather
    than persist a malformed or hallucinated summary.
    """
    payload = _first_json_object(text)
    if payload is None:
        raise ValueError("Qwen response did not contain a valid JSON narrative")

    assessment = str(payload.get("assessment", "")).strip()
    if len(assessment) < MIN_ASSESSMENT_CHARS:
        raise ValueError("Qwen assessment was empty or too short")
    if len(assessment) > MAX_ASSESSMENT_CHARS:
        raise ValueError("Qwen assessment exceeded the length budget")

    raw_recommendations = payload.get("recommendations", [])
    if not isinstance(raw_recommendations, list):
        raise ValueError("Qwen recommendations must be a list")
    recommendations = [str(item).strip() for item in raw_recommendations if str(item).strip()]
    if len(recommendations) > MAX_RECOMMENDATIONS:
        raise ValueError("Qwen returned too many recommendations")
    if any(len(item) > MAX_RECOMMENDATION_CHARS for item in recommendations):
        raise ValueError("Qwen recommendation exceeded the length budget")

    combined = " ".join([assessment, *recommendations])
    _reject_shared_style_violations(combined, allowed_distances(facts))

    return ReportNarrative(assessment=assessment, recommendations=recommendations)


def parse_defect_description_text(
    text: str, *, defect: DefectFact, image_url: str | None
) -> DefectObservation:
    """Extract and validate one snapshot reading.

    Beyond the shared style guards, the description must name the defect type it
    was given — a 3B vision model that drifts to a different finding is the main
    failure mode here, and naming the given type is the cheapest check for it.
    """
    payload = _first_json_object(text)
    if payload is None:
        raise ValueError("Qwen response did not contain a valid JSON description")

    description = str(payload.get("description", "")).strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise ValueError("Qwen description was empty or too short")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError("Qwen description exceeded the length budget")

    expected_label = DEFECT_TYPE_LABELS[defect.defect_type]
    if expected_label not in description:
        raise ValueError(f"Qwen description did not mention the confirmed type: {expected_label}")

    vague = [word for word in _VAGUE_QUANTIFIERS if word in description]
    if vague:
        raise ValueError(f"Qwen description used vague quantifiers: {vague}")
    if not _PERCENT_PATTERN.search(description):
        raise ValueError("Qwen description did not quantify the affected area as a percentage")

    permitted = {f"{defect.position_m:g}", f"{defect.position_m:.1f}", str(int(defect.position_m))}
    _reject_shared_style_violations(description, permitted)

    return DefectObservation(
        defect_id=defect.defect_id,
        position_m=defect.position_m,
        defect_type=defect.defect_type,
        severity=defect.severity,
        image_url=image_url,
        description=description,
        source="vision",
    )


MODELS_ROOT = Path(__file__).resolve().parents[1] / "models"

# 7B를 먼저 찾고 없으면 Agent가 쓰는 3B로 내려간다. 보고서 판독은 배치로 돌아서
# 지연에 둔감하므로 더 큰 모델이 유리하고, 3B만 받아둔 환경에서도 동작해야 한다.
MODEL_PREFERENCE = (
    "qwen2.5-vl-7b-instruct-awq",
    "qwen2.5-vl-3b-instruct-awq",
)


def has_weights(model_dir: Path) -> bool:
    """스냅샷이 쓸 만한 상태인지 확인한다.

    7B는 weight가 여러 파일로 샤딩되므로 `model.safetensors` 하나를 요구하면 안 된다.
    설정 파일 두 개와 safetensors가 하나라도 있으면 로드를 시도한다.
    """
    if not (model_dir / "config.json").is_file():
        return False
    if not (model_dir / "preprocessor_config.json").is_file():
        return False
    return any(model_dir.glob("*.safetensors"))


def resolve_model_dir(models_root: Path = MODELS_ROOT) -> Path | None:
    """선호 순서대로 내려받힌 첫 모델의 weights 디렉터리."""
    for name in MODEL_PREFERENCE:
        candidate = models_root / name / "weights"
        if has_weights(candidate):
            return candidate
    return None


class QwenReportProvider:
    """Generates report prose with a local Qwen2.5-VL-AWQ snapshot.

    Prefers the 7B weights for snapshot reading and falls back to the Agent's 3B.
    Loads its own copy, so this must not run concurrently with the Agent server on
    a single 8GB GPU — see README.md (VRAM 주의).
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        resolved = model_dir or resolve_model_dir()
        # 아무것도 없으면 선호 1순위를 가리켜 둔다. 오류 메시지가 무엇을 받아야
        # 하는지 알려주고, is_downloaded()가 False를 돌려준다.
        self.model_dir = resolved or (MODELS_ROOT / MODEL_PREFERENCE[0] / "weights")
        self.name = self.model_dir.parent.name
        self._model: Any = None
        self._processor: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    def is_downloaded(self) -> bool:
        return has_weights(self.model_dir)

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

    def _generate(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        image: Any | None = None,
        max_new_tokens: int,
    ) -> tuple[str, float]:
        import torch

        self.load()
        assert self._processor is not None
        assert self._model is not None

        user_content: list[dict[str, Any]] = []
        if image is not None:
            user_content.append({"type": "image"})
        user_content.append({"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)})

        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": user_content},
        ]
        prompt = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        processor_kwargs: dict[str, Any] = {"text": [prompt], "padding": True}
        if image is not None:
            processor_kwargs["images"] = [image]
        inputs = self._processor(**processor_kwargs, return_tensors="pt")
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}

        started = time.perf_counter()
        with torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0

        trimmed = [
            output[len(input_ids) :] for input_ids, output in zip(inputs["input_ids"], generated)
        ]
        response = self._processor.batch_decode(
            trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0].strip()
        return response, latency_ms

    def generate_narrative(
        self,
        *,
        facts: SessionFacts,
        grades: StateGrades,
        max_new_tokens: int = 512,
    ) -> tuple[ReportNarrative, float]:
        """Return the session narrative and the generation latency in milliseconds."""
        response, latency_ms = self._generate(
            system_prompt=SYSTEM_PROMPT,
            user_payload=build_prompt_payload(facts, grades),
            max_new_tokens=max_new_tokens,
        )
        return parse_narrative_text(response, facts=facts), latency_ms

    def describe_defect(
        self,
        *,
        defect: DefectFact,
        image_bytes: bytes,
        image_url: str | None = None,
        max_new_tokens: int = 192,
    ) -> tuple[DefectObservation, float]:
        """Read one defect snapshot and return the observation plus latency."""
        from PIL import Image

        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        response, latency_ms = self._generate(
            system_prompt=DEFECT_SYSTEM_PROMPT,
            user_payload=build_defect_payload(defect),
            image=image,
            max_new_tokens=max_new_tokens,
        )
        observation = parse_defect_description_text(response, defect=defect, image_url=image_url)
        return observation, latency_ms
