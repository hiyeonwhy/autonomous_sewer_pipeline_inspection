"""원격 LLM 호출부.

Cloud API는 GPU 없이 EC2 컨테이너에서 돌기 때문에, 모델은 HTTP 너머에 있다.
GMS처럼 여러 모델을 한 키로 중계하는 게이트웨이는 대부분 OpenAI 호환
(`POST /v1/chat/completions`)이라 그것을 기본 구현으로 둔다.

규격이 다른 게이트웨이로 바꿔야 하면 `ChatProvider`를 만족하는 클래스를 하나 더
쓰고 `build_provider`가 그것을 돌려주게 하면 된다. 서비스 로직은 손대지 않는다.

의존성을 늘리지 않으려고 `urllib`을 쓴다. 호출은 블로킹이므로 라우터가
`run_in_threadpool`로 감싼다.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from http.client import IncompleteRead
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class TransientLLMError(RuntimeError):
    """다시 걸면 될 가능성이 높은 실패. 연결·타임아웃·게이트웨이 과부하."""


# 다시 걸어 볼 HTTP 상태. 429는 속도 제한, 5xx는 게이트웨이 쪽 문제다.
# 400/401/403 은 요청이나 키가 잘못된 것이므로 재시도해도 같은 답이 온다.
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# 일시 오류일 때 호출을 몇 번까지 하는지(최초 호출 포함).
#
# 결함 하나가 실패하면 그 결함은 판독 없이 남고 원인은 로그에만 적힌다. 실제로
# 「INFERENCE_FAILED」가 한 번 떴다가 사람이 재생성을 누르니 성공한 일이 있었다 —
# 사람이 눌러서 되는 일이면 코드가 눌러야 한다.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 1.5

# SSAFY GMS 프록시는 비전 요청 응답에서 본문 머리 28바이트(`{\n  "id": "chatcmpl-` +
# id 앞 8자)를 떨어뜨리고, Content-Length는 원래 길이 그대로 보낸다. 텍스트 요청은
# chunked로 멀쩡히 오고 비전 요청만 그렇다. 재현율 100%.
#
# 받은 만큼으로 파싱이 안 되면 이 머리를 붙여 다시 시도한다. id 값이 잘리지만
# 우리가 쓰지 않는 필드다. 정상 응답에는 손대지 않으므로 GMS가 고쳐도 안전하다.
_TRUNCATED_HEAD = b'{\n  "id": "'


class ChatProvider(Protocol):
    """텍스트 한 번 주고 텍스트 한 번 받는, 이 모듈이 필요로 하는 최소 인터페이스."""

    name: str
    supports_vision: bool

    def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        image: tuple[str, bytes] | None = ...,
        max_tokens: int = ...,
    ) -> str: ...


class OpenAICompatibleProvider:
    """`POST {base_url}/chat/completions` 규격 클라이언트.

    이미지는 OpenAI 비전 규격대로 `image_url` 콘텐츠 블록에 data URI로 실어 보낸다.
    게이트웨이가 그 규격을 지원하지 않으면 사진 판독만 실패하고, 서비스가 해당
    결함을 메타데이터 소견으로 강등한다 — 문단 요약은 그대로 남는다.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        vision_model: str | None = None,
        timeout_s: float = 60.0,
        opener: Any = urlopen,
    ) -> None:
        resolved = base_url.rstrip("/")
        if not resolved.startswith(("http://", "https://")):
            raise ValueError("LLM base URL은 http 또는 https여야 합니다.")
        self.base_url = resolved
        self.api_key = api_key
        self.model = model
        self.vision_model = vision_model or model
        self.timeout_s = timeout_s
        self.opener = opener
        # 보고서의 `summary_model`에 남는 값. 문단과 사진에 다른 모델을 쓰면 둘 다
        # 적는다 — 어느 모델이 무엇을 썼는지 나중에 되짚을 수 있어야 한다.
        self.name = model if model == self.vision_model else f"{model}+{self.vision_model}"
        self.supports_vision = bool(self.vision_model)
        # 추론 모델(gpt-5.x)은 `max_tokens`와 `temperature`를 거부한다. 어느 쪽을
        # 받는지는 모델마다 다르므로 첫 호출에서 알아내고 그 뒤로는 재사용한다.
        self._token_field = "max_tokens"
        self._send_temperature = True

    def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        image: tuple[str, bytes] | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """일시 오류면 다시 걸고, 그 외에는 바로 올린다.

        재시도를 파라미터 협상 루프(`_complete_once`) **밖에** 둔다. 안에 섞으면
        「temperature 미지원」 같은 협상용 재시도와 「게이트웨이 과부하」 재시도가
        같은 횟수를 나눠 쓰게 되어, 협상에 두 번 쓰면 일시 오류에는 한 번도 못 쓴다.
        """
        last: TransientLLMError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._complete_once(
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    image=image,
                    max_tokens=max_tokens,
                )
            except TransientLLMError as error:
                last = error
                if attempt == MAX_ATTEMPTS:
                    break
                wait = RETRY_BACKOFF_S * attempt
                logger.warning(
                    "LLM 일시 오류 — %.1f초 후 재시도 (%d/%d): %s",
                    wait,
                    attempt,
                    MAX_ATTEMPTS,
                    error,
                )
                time.sleep(wait)
        assert last is not None
        raise last

    def _complete_once(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        image: tuple[str, bytes] | None,
        max_tokens: int,
    ) -> str:
        text = json.dumps(user_payload, ensure_ascii=False)

        # 사진이 없으면 content를 문자열로 보낸다. 콘텐츠 블록 배열을 받아주지 않는
        # 게이트웨이가 있어서, 가장 중요한 문단 요약 호출은 제일 보수적인 형태로 낸다.
        content: str | list[dict[str, Any]] = text
        if image is not None:
            media_type, payload = image
            encoded = base64.b64encode(payload).decode()
            # 이미지를 먼저 두어야 모델이 텍스트를 "이 사진에 대한 전제"로 읽는다.
            content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                },
                {"type": "text", "text": text},
            ]

        model = self.vision_model if image is not None else self.model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        # 거부당한 파라미터를 하나씩 빼면서 최대 세 번까지 시도한다.
        for _ in range(3):
            body: dict[str, Any] = {
                "model": model,
                self._token_field: max_tokens,
                "messages": messages,
            }
            if self._send_temperature:
                # 보고서는 같은 입력에 같은 문장이 나오는 편이 검토에 낫다.
                body["temperature"] = 0

            try:
                return self._extract_text(self._post("/chat/completions", body))
            except TransientLLMError:
                # 파라미터 협상과 무관한 일시적 실패다. TransientLLMError 가
                # RuntimeError 를 물려받으므로 아래 문자열 검사에 걸리지 않게
                # 여기서 먼저 걷어낸다 — 잘린 응답 본문에 "temperature" 같은
                # 낱말이 섞이면 엉뚱하게 협상 분기를 타 버린다.
                raise
            except RuntimeError as error:
                message = str(error)
                if (
                    "max_completion_tokens" in message
                    and self._token_field == "max_tokens"
                ):
                    logger.info("%s: max_completion_tokens로 전환", model)
                    self._token_field = "max_completion_tokens"
                    continue
                if "temperature" in message and self._send_temperature:
                    logger.info("%s: temperature 미지원 — 빼고 재시도", model)
                    self._send_temperature = False
                    continue
                raise

        raise RuntimeError(f"{model}: 요청 파라미터를 맞추지 못함")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout_s) as response:
                try:
                    body = response.read()
                except IncompleteRead as partial:
                    # 선언된 길이보다 짧게 왔다. 받은 만큼으로 진행한다.
                    body = partial.partial
        except HTTPError as error:
            # 본문에 원인이 들어 있는 경우가 많아 읽어서 로그에 남긴다. 키는 절대 찍지 않는다.
            try:
                detail = error.read().decode()[:500]
            except Exception:
                detail = ""
            if error.code == 400 and "Model not found in request" in detail:
                # GMS가 요청 본문을 파싱하지 못했을 때 내는 메시지다. model은 보냈으므로
                # 실제 원인은 대개 본문 크기 초과(약 100KB)다.
                raise RuntimeError(
                    "LLM 요청 거부됨: 게이트웨이가 본문을 파싱하지 못함 "
                    f"(본문 {len(request.data or b'') / 1024:.0f}KB — 크기 제한 의심). {detail}"
                ) from error
            if error.code in TRANSIENT_STATUS_CODES:
                raise TransientLLMError(
                    f"LLM 일시 오류: HTTP {error.code} {detail}"
                ) from error
            raise RuntimeError(f"LLM 요청 거부됨: HTTP {error.code} {detail}") from error
        except (URLError, TimeoutError, OSError) as error:
            # 연결·타임아웃은 거의 항상 일시적이다. 다시 걸면 대개 붙는다.
            raise TransientLLMError(f"LLM 연결 실패: {error}") from error

        payload = self._loads(body)
        if not isinstance(payload, dict):
            # 응답이 망가진 것이므로 다시 걸면 멀쩡히 올 수 있다. 요청은 정상이었다.
            raise TransientLLMError("LLM 응답이 JSON 객체가 아님")
        return payload

    @staticmethod
    def _loads(body: bytes) -> Any:
        """정상 파싱을 먼저 시도하고, 실패하면 잘린 머리를 붙여 한 번 더 시도한다.

        머리 복구가 안 되면 `{"__salvaged__": ...}`로 감싼 본문만 돌려준다.
        봉투를 못 살려도 우리가 필요한 건 message.content 하나뿐이다.

        **비전 응답은 100% 잘려서 온다.** 실측 10회 모두 「머리를 잘라 보냄」 경고가
        찍혔고 복구로 넘어갔다. 잘린 양이 평소와 다르면 복구가 빗나가는데, 그때
        판독이 실패한다. 그래서 여기서 나는 실패는 **다시 걸 값어치가 있다** —
        요청은 멀쩡했고 응답만 망가진 것이다.
        """
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            pass

        try:
            payload = json.loads(_TRUNCATED_HEAD + body)
        except json.JSONDecodeError:
            pass
        else:
            logger.warning("게이트웨이가 응답 머리를 잘라 보냄 — 복구해서 진행함")
            return payload

        salvaged = OpenAICompatibleProvider._salvage_content(body)
        if salvaged is None:
            raise TransientLLMError(f"LLM 응답을 해석하지 못함 (앞 80B: {body[:80]!r})")
        logger.warning("게이트웨이가 응답을 크게 잘라 보냄 — 본문만 건져서 진행함")
        return {"__salvaged__": salvaged}

    @staticmethod
    def _salvage_content(body: bytes) -> str | None:
        """봉투가 어디서 잘렸든 `content` 값만 직접 꺼낸다.

        잘려 나가는 길이가 모델마다 다르다(gpt-4o는 28바이트, gpt-5 계열은 더 많이).
        고정 접두사 복구로는 못 잡는 경우가 있어 키를 직접 찾는다.
        """
        text = body.decode("utf-8", errors="replace")
        decoder = json.JSONDecoder()
        marker = '"content"'
        index = text.find(marker)
        while index != -1:
            quote = text.find('"', index + len(marker))
            if quote != -1:
                try:
                    value, _ = decoder.raw_decode(text[quote:])
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, str) and value.strip():
                    return value
            index = text.find(marker, index + 1)
        return None

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        salvaged = payload.get("__salvaged__")
        if isinstance(salvaged, str):
            return salvaged.strip()

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("LLM 응답에 choices가 없음")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("LLM 응답에 message가 없음")

        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        # 일부 게이트웨이는 content를 블록 배열로 돌려준다.
        if isinstance(content, list):
            parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
        raise RuntimeError("LLM 응답 본문이 비어 있음")


def build_provider() -> ChatProvider | None:
    """설정이 갖춰졌을 때만 provider를 만든다.

    키가 없으면 `None`을 돌려주고, 서비스는 규칙 기반 요약으로 보고서를 채운다.
    설정 실수로 PDF 다운로드가 통째로 막히는 편보다 낫다.
    """
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    if not settings.llm_api_key or not settings.llm_api_base_url:
        logger.info("LLM 요약 비활성: LLM_API_KEY 또는 LLM_API_BASE_URL 미설정")
        return None

    try:
        return OpenAICompatibleProvider(
            base_url=settings.llm_api_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            vision_model=settings.llm_vision_model or settings.llm_model,
            timeout_s=settings.llm_timeout_s,
        )
    except ValueError as error:
        logger.warning("LLM provider 구성 실패: %s", error)
        return None
