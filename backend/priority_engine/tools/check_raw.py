from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402

CHECKSUMS = paths.RAW_CHECKSUMS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="raw/ 원본이 우리가 검증한 판본과 같은지 확인합니다."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="현재 raw/ 내용으로 기준 체크섬을 다시 씁니다 (판본을 의도적으로 갱신할 때만)",
    )
    args = parser.parse_args()

    if args.update:
        recorded = {}
        for path in sorted(paths.RAW.iterdir()):
            if path.is_dir() or path.name == "MANIFEST.md":
                continue
            recorded[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        CHECKSUMS.write_text(
            json.dumps(recorded, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"→ {CHECKSUMS} ({len(recorded)}개)")
        return

    if not CHECKSUMS.exists():
        raise SystemExit(f"{CHECKSUMS} 가 없습니다. --update 로 먼저 생성하세요.")

    expected = json.loads(CHECKSUMS.read_text(encoding="utf-8"))

    problems = 0
    for name, want in expected.items():
        path = paths.RAW / name
        if not path.exists():
            print(f"  [없음] {name}")
            problems += 1
            continue

        size = path.stat().st_size
        if size != want["bytes"]:
            print(f"  [크기] {name}  {size:,} != {want['bytes']:,}")
            problems += 1
            continue

        got = sha256(path)
        if got != want["sha256"]:
            print(f"  [다름] {name}")
            print(f"         기대 {want['sha256'][:16]}...")
            print(f"         실제 {got[:16]}...")
            problems += 1
        else:
            print(f"  [OK  ] {name}  {size:,} bytes")

    print()
    if problems:
        # 통계 파일이 조용히 개정판으로 바뀌면 수치가 달라지는데 원인 추적이 어렵습니다.
        print(f"  {problems}개 불일치. 받는 방법은 팀 문서 07-코드가이드.md 참조.")
        raise SystemExit(1)
    print(f"  원본 {len(expected)}개가 검증된 판본과 일치합니다.")


if __name__ == "__main__":
    main()
