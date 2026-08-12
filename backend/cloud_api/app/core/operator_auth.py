"""승인 운영자 확인 코드 검증.

코드는 DB 에 평문으로 두지 않는다. 운영자마다 무작위 소금을 뽑아
`sha256(salt + code)` 만 저장하고, 여기서 같은 방식으로 다시 계산해 비교한다.
코드를 심는 것은 `scripts/set_operator_passcode.py` 가 한다.

**이것은 시연 중 남이 로봇을 건드리지 못하게 하는 잠금장치다.** 세션 로그인도,
권한 체계도 아니다. 진짜 인증이 필요해지면 토큰을 내주는 방식으로 바꿔야 한다.
그래도 검증을 서버에 두는 이유는, 화면에서만 비교하면 개발자 도구로 API 를 직접
불러 그냥 지나갈 수 있기 때문이다.
"""
from __future__ import annotations

import hashlib
import hmac


class OperatorCodeError(Exception):
    """확인 코드로 통과하지 못했다. 호출부가 상황에 맞는 HTTP 응답으로 옮긴다."""


async def verify_operator_passcode(
    database,
    *,
    operator_id: int | None,
    code: str | None,
) -> None:
    if operator_id is None:
        raise OperatorCodeError("승인 운영자를 지정해야 합니다.")

    operator = await database.operators.find_one({"id": operator_id})
    salt = (operator or {}).get("passcode_salt")
    expected = (operator or {}).get("passcode_hash")
    if not salt or not expected:
        # 코드가 없는 운영자를 통과시키면 그 계정이 그대로 우회로가 된다.
        raise OperatorCodeError("승인 운영자에 확인 코드가 설정되어 있지 않습니다.")

    digest = hashlib.sha256(f"{salt}{code or ''}".encode("utf-8")).hexdigest()
    # 자릿수마다 빠져나오지 않는 비교. 코드 길이를 재는 실마리를 주지 않는다.
    if not hmac.compare_digest(digest, str(expected)):
        raise OperatorCodeError("확인 코드가 올바르지 않습니다.")
