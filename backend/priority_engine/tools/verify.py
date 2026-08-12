from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (표시명, 모듈, 추가 인자). 자체 검증은 각 모듈의 __main__ 에 있습니다.
SUITES: list[tuple[str, str, list[str]]] = [
    ("scoring.grading", "scoring.grading", []),
    ("collectors.subsidence", "collectors.subsidence", []),
    ("collectors.geocode", "collectors.geocode", []),
    ("collectors.rainfall", "collectors.rainfall", []),
    ("collectors.population", "collectors.population", []),
    ("collectors.manhole", "collectors.manhole", []),
    ("collectors.sewer", "collectors.sewer", []),
    ("collectors.pipe_age", "collectors.pipe_age", []),
    ("collectors.facilities", "collectors.facilities", []),
    ("collectors.odcloud", "collectors.odcloud", []),
    ("boundary.build_boundaries", "boundary.build_boundaries", []),
    ("dataset", "dataset", []),
    ("scoring.risk", "scoring.risk", []),
    ("scoring.weighted", "scoring.weighted", []),
    ("scoring.supervised", "scoring.supervised", []),
    ("scoring.glm", "scoring.glm", []),
    ("scoring.sensitivity", "scoring.sensitivity", []),
    ("mapping.sgg_normalize", "mapping.sgg_normalize", ["data/districts_master.json"]),
    ("scoring.objective_weights", "scoring.objective_weights", []),
    ("scoring.dominance", "scoring.dominance", []),
    ("analysis.profile", "analysis.profile", []),
    ("analysis.pca", "analysis.pca", []),
    ("analysis.cluster", "analysis.cluster", []),
    ("analysis.charts", "analysis.charts", []),
    ("analysis.presentation", "analysis.presentation", []),
]

# 표준 라이브러리 대조. 라이브러리가 없으면 스스로 건너뜁니다.
REFERENCE = ("scoring.reference_check", "scoring.reference_check", [])

OK = re.compile(r"\[OK")
FAIL = re.compile(r"\[FAIL")


def exists(module: str) -> bool:
    # 부분 커밋 상태에서도 돌아야 합니다. 아직 올리지 않은 모듈을 실패로 세면
    # "검증 실패"로 보여서, 없는 것과 깨진 것을 구분합니다.
    return (ROOT / Path(*module.split("."))).with_suffix(".py").exists()


def run(module: str, extra: list[str]) -> tuple[int, int, str]:
    result = subprocess.run(
        [sys.executable, "-m", module, *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = result.stdout + result.stderr
    failed = len(FAIL.findall(out))
    # 출력의 OK/FAIL 개수만 세면 **중간에 예외로 죽은 모듈**이 통과로 집계됩니다
    # (앞부분 OK 몇 개 + FAIL 0개). 실제로 그렇게 가려진 적이 있어 종료 코드를
    # 함께 봅니다 — 죽었으면 FAIL 이 없어도 실패입니다.
    if result.returncode != 0 and failed == 0:
        failed = 1
        out += f"\n[verify] 종료 코드 {result.returncode} — 검증이 끝까지 돌지 못했습니다."
    return len(OK.findall(out)), failed, out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="우선순위 엔진 전체 검증을 한 번에 실행합니다."
    )
    parser.add_argument(
        "--skip-reference",
        action="store_true",
        help="표준 라이브러리 대조를 건너뜁니다 (numpy·statsmodels·shapely 불필요)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="실패한 모듈의 전체 출력을 보여줍니다"
    )
    args = parser.parse_args()

    suites = list(SUITES) if args.skip_reference else [*SUITES, REFERENCE]

    print("=" * 60)
    print("우선순위 엔진 검증")
    print("=" * 60)

    total_ok = 0
    total_fail = 0
    broken: list[tuple[str, str]] = []

    skipped: list[str] = []

    for label, module, extra in suites:
        if not exists(module):
            skipped.append(label)
            print(f"  [없음] {label:28}   이 커밋에 아직 없습니다")
            continue

        passed, failed, out = run(module, extra)
        total_ok += passed
        total_fail += failed

        if failed or passed == 0:
            mark = "FAIL" if failed else "  ??"
            broken.append((label, out))
        else:
            mark = "  OK"
        print(f"  [{mark}] {label:28} {passed:3d}개" + (f"  실패 {failed}" if failed else ""))

    print("-" * 60)
    print(f"  통과 {total_ok}개 / 실패 {total_fail}개"
          + (f" / 없음 {len(skipped)}개" if skipped else ""))

    if broken:
        print()
        for label, out in broken:
            print(f"--- {label} " + "-" * (56 - len(label)))
            print(out.strip()[-2000:] if args.verbose else out.strip()[-600:])
        print()
        print("  실패가 있습니다. -v 로 전체 출력을 보세요.")
        raise SystemExit(1)

    print()
    print("  전부 통과했습니다.")


if __name__ == "__main__":
    main()
