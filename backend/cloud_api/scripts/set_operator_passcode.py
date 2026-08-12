"""운영자 확인 코드를 심는다.

코드 자체는 **저장소에도 DB 에도 평문으로 남지 않는다.** 운영자마다 무작위 소금을
뽑아 `sha256(salt + code)` 만 저장하고, 서버는 같은 방식으로 다시 계산해 비교한다.

    python scripts/set_operator_passcode.py 김철수

코드는 인자로 받지 않는다. 명령 이력과 프로세스 목록에 남기 때문이다. 실행하면
가려진 입력으로 물어본다. 자동화가 필요하면 `OPERATOR_PASSCODE` 환경변수를 쓴다.

지우려면 `--clear` 를 준다. 코드가 없는 운영자로는 점검을 시작할 수 없다
(`guard_inspection_start` 가 막는다).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import AsyncMongoClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

# 시연장에서 급히 눌러야 하는 값이라 짧게 둔다. 이 코드는 보안 인증이 아니라
# 「옆 사람이 실수로 못 누르게」 하는 잠금장치다.
MIN_CODE_LENGTH = 4


def build_hash(code: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return salt, hashlib.sha256(f"{salt}{code}".encode("utf-8")).hexdigest()


async def main() -> int:
    parser = argparse.ArgumentParser(description="운영자 확인 코드 설정")
    parser.add_argument("name", help="운영자 이름 (operators.name)")
    parser.add_argument("--clear", action="store_true", help="코드를 지운다")
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]

    try:
        operator = await database.operators.find_one({"name": args.name})
        if operator is None:
            print(f"[!] 운영자를 찾을 수 없습니다: {args.name}")
            return 1

        if args.clear:
            await database.operators.update_one(
                {"id": operator["id"]},
                {"$unset": {"passcode_salt": "", "passcode_hash": ""}},
            )
            print(f"[OK] {args.name} 의 확인 코드를 지웠습니다. 이제 이 운영자로는 점검을 시작할 수 없습니다.")
            return 0

        code = os.environ.get("OPERATOR_PASSCODE") or getpass.getpass(f"{args.name} 의 확인 코드: ")
        if len(code) < MIN_CODE_LENGTH:
            print(f"[!] 코드는 {MIN_CODE_LENGTH}자 이상이어야 합니다.")
            return 1

        salt, digest = build_hash(code)
        await database.operators.update_one(
            {"id": operator["id"]},
            {"$set": {"passcode_salt": salt, "passcode_hash": digest}},
        )
        # 코드도 해시도 찍지 않는다. 터미널 기록에 남는다.
        print(f"[OK] {args.name} (id={operator['id']}) 확인 코드를 설정했습니다.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
