from __future__ import annotations

import logging
from hmac import compare_digest

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def agent_publish_key_is_valid(provided: str) -> bool:
    """Agent 쓰기 요청의 키를 검사한다. HTTP·WebSocket 두 경로가 같이 쓴다.

    서버에 `AGENT_PUBLISH_KEY` 가 설정돼 있으면 정확히 일치해야 한다.

    **설정돼 있지 않으면 키를 보내기만 하면 통과시킨다.** 사실상 인증이 없는
    상태이므로 좋은 규칙은 아니지만, 배포 자격증명(Jenkins `cloud-api-env-file`)에
    이 값이 들어 있지 않은 채로 운영돼 왔다. 한때 이 관용 경로를 없앴더니 Agent 의
    텔레메트리·프레임·결함 업로드가 한꺼번에 503 이 되어 대시보드가 통째로 멈췄다.

    닫는 방법은 코드가 아니라 배포 설정이다 — 자격증명에 `AGENT_PUBLISH_KEY` 를
    넣으면 이 분기는 저절로 죽고 아래 정확 비교만 남는다. 시연 뒤에 넣을 것.
    """
    expected = get_settings().agent_publish_key
    if not expected:
        return bool(provided)
    return compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def require_agent_publish_key(
    x_agent_publish_key: str = Header(default="", alias="X-Agent-Publish-Key"),
) -> None:
    """Authenticate laptop-Agent writes while keeping Dashboard reads public."""
    if not get_settings().agent_publish_key:
        # 헤더조차 없으면 Agent 가 아니라고 보고 막는다. 서버에 키가 없다는 사실을
        # 상태 코드로 알려 두어야 "왜 통과했지 / 왜 막혔지" 를 나중에 되짚을 수 있다.
        if not x_agent_publish_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent publish key가 서버에 설정되지 않았습니다.",
            )
        logger.warning(
            "AGENT_PUBLISH_KEY 미설정 — Agent 쓰기를 키 검증 없이 허용하는 중입니다."
        )
        return
    if not agent_publish_key_is_valid(x_agent_publish_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent publish key가 올바르지 않습니다.",
        )
