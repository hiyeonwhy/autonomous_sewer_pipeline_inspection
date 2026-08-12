from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402
from scoring.glm import fit_glm  # noqa: E402
from scoring.linalg import inverse, solve  # noqa: E402

# 계수는 기계 정밀도로 맞아야 합니다. 같은 알고리즘(IRLS)에 같은 수렴 기준이면
# 부동소수 오차 수준까지 일치하는 것이 정상입니다.
COEF_TOLERANCE = 1e-6

# 표준오차는 역행렬 계산 경로가 달라 상대 오차로 봅니다.
SE_RELATIVE_TOLERANCE = 1e-3

# p-value 는 분포 꼬리에서 z 에 지수적으로 민감합니다. 표준오차 차이가 그대로
# 증폭되므로 표준오차와 같은 기준으로 봅니다.
P_RELATIVE_TOLERANCE = 1e-3

def _synthetic_logistic(n: int, true_beta: list[float], seed: int):
    random.seed(seed)
    design, targets = [], []
    for _ in range(n):
        row = [random.gauss(0, 1) for _ in range(len(true_beta) - 1)]
        eta = true_beta[0] + sum(b * v for b, v in zip(true_beta[1:], row))
        probability = 1.0 / (1.0 + math.exp(-eta))
        design.append(row)
        targets.append(1.0 if random.random() < probability else 0.0)
    return design, targets


def _synthetic_poisson(n: int, true_beta: list[float], seed: int):
    random.seed(seed)
    design, targets, offsets = [], [], []
    for _ in range(n):
        row = [random.gauss(0, 1) for _ in range(len(true_beta) - 1)]
        exposure = random.uniform(0.5, 3.0)
        mu = exposure * math.exp(
            true_beta[0] + sum(b * v for b, v in zip(true_beta[1:], row))
        )
        # 역변환 표집. numpy 없이 포아송 난수를 만듭니다.
        count, term, cumulative = 0, math.exp(-mu), math.exp(-mu)
        threshold = random.random()
        while threshold > cumulative and count < 200:
            count += 1
            term *= mu / count
            cumulative += term
        design.append(row)
        targets.append(float(count))
        offsets.append(math.log(exposure))
    return design, targets, offsets


def check_glm() -> tuple[int, int, list[str]]:
    try:
        import numpy as np
        import statsmodels.api as sm
    except ImportError:
        return 0, 0, ["statsmodels 없음: 건너뜀 (pip install statsmodels)"]

    passed = failed = 0
    notes: list[str] = []

    # ---- 로지스틱
    true_beta = [-0.5, 1.8, -1.2, 0.6]
    design, targets = _synthetic_logistic(800, true_beta, seed=7)
    ours = fit_glm([[1.0] + row for row in design], targets, "binomial")
    ref = sm.GLM(
        np.array(targets),
        sm.add_constant(np.array(design)),
        family=sm.families.Binomial(),
    ).fit()

    coef_gap = max(abs(a - b) for a, b in zip(ours.coefficients, ref.params))
    se_gap = max(
        abs(a - b) / b for a, b in zip(ours.standard_errors, ref.bse) if b > 0
    )
    dev_gap = abs(ours.deviance - ref.deviance)

    for label, gap, tolerance in (
        ("로지스틱 계수", coef_gap, COEF_TOLERANCE),
        ("로지스틱 표준오차(상대)", se_gap, SE_RELATIVE_TOLERANCE),
        ("로지스틱 이탈도", dev_gap, 1e-6),
    ):
        ok = gap < tolerance
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label} 최대차 {gap:.3e} < {tolerance:.0e}")
        passed += ok
        failed += not ok

    notes.append(f"로지스틱 계수 일치 {coef_gap:.2e}, 표준오차 {se_gap:.2e}")

    # p-value 는 꼬리에서 z 에 매우 민감하므로 상대 오차로 봅니다. 남는 차이는
    # 표준오차에서 전파된 것이고(위에서 1e-3 상대로 확인), 공식 자체가 근사인
    # 것은 아닙니다. 그 점은 아래 check_pvalue_formula() 가 따로 증명합니다.
    p_gap = max(
        abs(a - b) / b for a, b in zip(ours.p_values, ref.pvalues) if b > 0
    )
    ok = p_gap < P_RELATIVE_TOLERANCE
    print(
        f"  [{'OK  ' if ok else 'FAIL'}] Wald p-value(상대) 최대차 "
        f"{p_gap:.3e} < {P_RELATIVE_TOLERANCE:.0e}"
    )
    passed += ok
    failed += not ok
    notes.append(f"p-value 일치(상대) {p_gap:.2e}")

    # ---- 포아송 + offset
    true_poisson = [-1.0, 0.9, -0.7]
    design, targets, offsets = _synthetic_poisson(600, true_poisson, seed=11)
    ours = fit_glm([[1.0] + row for row in design], targets, "poisson", offset=offsets)
    ref = sm.GLM(
        np.array(targets),
        sm.add_constant(np.array(design)),
        family=sm.families.Poisson(),
        offset=np.array(offsets),
    ).fit()

    coef_gap = max(abs(a - b) for a, b in zip(ours.coefficients, ref.params))
    se_gap = max(
        abs(a - b) / b for a, b in zip(ours.standard_errors, ref.bse) if b > 0
    )
    for label, gap, tolerance in (
        ("포아송 계수", coef_gap, COEF_TOLERANCE),
        ("포아송 표준오차(상대)", se_gap, SE_RELATIVE_TOLERANCE),
    ):
        ok = gap < tolerance
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label} 최대차 {gap:.3e} < {tolerance:.0e}")
        passed += ok
        failed += not ok

    notes.append(f"포아송(offset) 계수 일치 {coef_gap:.2e}")
    return passed, failed, notes


def check_pvalue_formula() -> tuple[int, int, list[str]]:
    """같은 z 를 넣었을 때 math.erfc 가 scipy.stats.norm 과 같은지.

    앞의 GLM 대조는 표준오차 차이가 섞여 있어 공식의 정확성을 분리해 보여주지
    못합니다. 여기서는 z 를 고정해 넣어 **공식이 근사가 아님**을 보입니다.
    scipy 는 이 대조에만 쓰고 산정 경로에는 들어가지 않습니다.
    """
    try:
        from scipy.stats import norm
    except ImportError:
        return 0, 0, ["scipy 없음: 건너뜀"]

    zs = [0.0, 0.5, 1.0, 1.959964, 2.5, 3.0, 5.0, 8.0, 12.0, 30.0]
    gap = max(
        abs(math.erfc(abs(z) / math.sqrt(2.0)) - 2.0 * norm.sf(abs(z))) for z in zs
    )
    ok = gap < 1e-15
    print(f"  [{'OK  ' if ok else 'FAIL'}] erfc 양측 p vs scipy.norm 최대차 {gap:.3e} < 1e-15")

    # 임계값 정합성: |z| = 1.959964 에서 p = 0.05 여야 합니다.
    critical = math.erfc(1.959964 / math.sqrt(2.0))
    ok2 = abs(critical - 0.05) < 1e-6
    print(f"  [{'OK  ' if ok2 else 'FAIL'}] |z|=1.959964 → p={critical:.6f} == 0.05")

    return int(ok) + int(ok2), int(not ok) + int(not ok2), [
        f"p-value 공식 정확 (scipy 대조 {gap:.1e}, 의존성 아님)"
    ]


def check_linalg() -> tuple[int, int, list[str]]:
    try:
        import numpy as np
    except ImportError:
        return 0, 0, ["numpy 없음: 건너뜀"]

    passed = failed = 0
    random.seed(23)
    size = 8
    # 대각 우세 행렬로 만들어 조건수를 적당히 유지합니다.
    matrix = [
        [random.gauss(0, 1) + (size if i == j else 0.0) for j in range(size)]
        for i in range(size)
    ]
    vector = [random.gauss(0, 1) for _ in range(size)]

    ours = solve([row[:] for row in matrix], vector[:])
    ref = np.linalg.solve(np.array(matrix), np.array(vector))
    gap = max(abs(a - b) for a, b in zip(ours, ref))
    ok = gap < 1e-9
    print(f"  [{'OK  ' if ok else 'FAIL'}] solve() 최대차 {gap:.3e} < 1e-09")
    passed += ok
    failed += not ok

    ours_inv = inverse([row[:] for row in matrix])
    ref_inv = np.linalg.inv(np.array(matrix))
    gap = max(
        abs(a - b)
        for row_ours, row_ref in zip(ours_inv, ref_inv)
        for a, b in zip(row_ours, row_ref)
    )
    ok = gap < 1e-9
    print(f"  [{'OK  ' if ok else 'FAIL'}] inverse() 최대차 {gap:.3e} < 1e-09")
    passed += ok
    failed += not ok

    return passed, failed, [f"선형대수 일치 {gap:.2e}"]


def check_boundaries() -> tuple[int, int, list[str]]:
    try:
        from shapely.geometry import shape
        from shapely.validation import explain_validity
    except ImportError:
        return 0, 0, ["shapely 없음: 건너뜀"]

    path = paths.MUNICIPALITY_BOUNDARIES
    if not path.exists():
        return 0, 0, [f"{path.name} 없음: 건너뜀"]

    with path.open(encoding="utf-8") as handle:
        collection = json.load(handle)

    features = collection.get("features", [])
    invalid: list[str] = []
    empty: list[str] = []

    for feature in features:
        name = feature.get("properties", {}).get("full_name", "?")
        try:
            geometry = shape(feature["geometry"])
        except Exception as error:  # noqa: BLE001: 어떤 실패든 드러내야 합니다
            invalid.append(f"{name}: 해석 실패 {error}")
            continue
        if geometry.is_empty:
            empty.append(name)
        elif not geometry.is_valid:
            invalid.append(f"{name}: {explain_validity(geometry)}")

    passed = failed = 0

    ok = len(features) == 229
    print(f"  [{'OK  ' if ok else 'FAIL'}] 폴리곤 개수 {len(features)} == 229")
    passed += ok
    failed += not ok

    ok = not invalid
    print(f"  [{'OK  ' if ok else 'FAIL'}] shapely 유효성: 무효 {len(invalid)}개")
    if invalid:
        for line in invalid[:5]:
            print(f"         {line}")
    passed += ok
    failed += not ok

    ok = not empty
    print(f"  [{'OK  ' if ok else 'FAIL'}] 빈 폴리곤 {len(empty)}개")
    passed += ok
    failed += not ok

    return passed, failed, [f"229개 폴리곤 shapely 유효성 통과 (무효 {len(invalid)})"]


def main() -> int:
    print("=" * 72)
    print("표준 라이브러리 대조 검증")
    print("=" * 72)
    print("직접 구현을 교체하지 않고 대조만 합니다. 라이브러리가 없으면 건너뜁니다.")

    total_passed = total_failed = 0
    all_notes: list[str] = []

    for title, checker in (
        ("GLM (IRLS) vs statsmodels", check_glm),
        ("p-value 공식 vs scipy.stats", check_pvalue_formula),
        ("선형대수 vs numpy", check_linalg),
        ("dissolve vs shapely", check_boundaries),
    ):
        print()
        print(f"[{title}]")
        passed, failed, notes = checker()
        if passed == 0 and failed == 0:
            for note in notes:
                print(f"  ⊘ {note}")
        total_passed += passed
        total_failed += failed
        all_notes.extend(notes)

    print()
    print("=" * 72)
    print(f"통과 {total_passed}개 / 실패 {total_failed}개")
    if total_failed == 0 and total_passed > 0:
        print()
        print("  직접 구현이 표준 구현과 일치합니다. 교체할 이유가 없습니다.")
        for note in all_notes:
            print(f"    · {note}")
    print("=" * 72)
    return total_failed


if __name__ == "__main__":
    raise SystemExit(main())
