from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math
from dataclasses import dataclass, field

from scoring.linalg import (
    Matrix,
    SingularMatrixError,
    Vector,
    add_ridge,
    inverse,
    matvec,
    solve,
    xtwx,
    xtwy,
)

# exp 오버플로 방지. exp(30)≈1e13 이면 실무적으로 충분히 큽니다.
ETA_LIMIT = 30.0

# 로지스틱에서 μ가 0이나 1에 붙으면 가중치가 0이 되어 해가 발산합니다.
MU_EPSILON = 1e-10

MAX_ITERATIONS = 100
CONVERGENCE_TOLERANCE = 1e-9


def clamp(value: float, limit: float = ETA_LIMIT) -> float:
    return max(-limit, min(limit, value))


class Family:
    name = "base"

    def linkinv(self, eta: float) -> float:
        raise NotImplementedError

    def weight(self, mu: float) -> float:
        raise NotImplementedError

    def working_residual(self, y: float, mu: float) -> float:
        raise NotImplementedError

    def deviance(self, y: float, mu: float) -> float:
        raise NotImplementedError

    def initial_mu(self, targets: Vector) -> float:
        raise NotImplementedError


class Binomial(Family):
    name = "binomial"

    def linkinv(self, eta: float) -> float:
        mu = 1.0 / (1.0 + math.exp(-clamp(eta)))
        return min(1.0 - MU_EPSILON, max(MU_EPSILON, mu))

    def weight(self, mu: float) -> float:
        # (dμ/dη)² / V(μ) = [μ(1-μ)]² / [μ(1-μ)] = μ(1-μ)
        return max(mu * (1.0 - mu), MU_EPSILON)

    def working_residual(self, y: float, mu: float) -> float:
        return (y - mu) / max(mu * (1.0 - mu), MU_EPSILON)

    def deviance(self, y: float, mu: float) -> float:
        if y == 1.0:
            return -2.0 * math.log(mu)
        if y == 0.0:
            return -2.0 * math.log(1.0 - mu)
        return -2.0 * (y * math.log(mu) + (1.0 - y) * math.log(1.0 - mu))

    def initial_mu(self, targets: Vector) -> float:
        mean = sum(targets) / len(targets)
        return min(0.99, max(0.01, mean))


class Poisson(Family):
    name = "poisson"

    def linkinv(self, eta: float) -> float:
        return max(math.exp(clamp(eta)), MU_EPSILON)

    def weight(self, mu: float) -> float:
        # (dμ/dη)² / V(μ) = μ² / μ = μ
        return max(mu, MU_EPSILON)

    def working_residual(self, y: float, mu: float) -> float:
        return (y - mu) / max(mu, MU_EPSILON)

    def deviance(self, y: float, mu: float) -> float:
        # y=0 일 때 y·log(y/μ) → 0
        term = 0.0 if y <= 0.0 else y * math.log(y / mu)
        return 2.0 * (term - (y - mu))

    def initial_mu(self, targets: Vector) -> float:
        mean = sum(targets) / len(targets)
        return max(mean, 0.1)


FAMILIES = {"binomial": Binomial, "poisson": Poisson}


@dataclass
class GlmResult:
    family: str
    coefficients: Vector
    standard_errors: Vector
    deviance: float
    null_deviance: float
    iterations: int
    converged: bool
    observations: int
    parameters: int
    feature_names: list[str] = field(default_factory=list)

    @property
    def z_values(self) -> Vector:
        return [
            coefficient / error if error > 0 else 0.0
            for coefficient, error in zip(self.coefficients, self.standard_errors)
        ]

    @property
    def p_values(self) -> Vector:
        # Wald 양측 p = 2(1 - Φ(|z|)) = erfc(|z|/√2).
        # math.erfc 가 표준 라이브러리라 근사 없이 정확히 계산됩니다
        # (scipy.stats.norm 과 1e-16 일치 확인: reference_check.py).
        return [math.erfc(abs(z) / math.sqrt(2.0)) for z in self.z_values]

    @property
    def mcfadden_r2(self) -> float:
        if self.null_deviance <= 0:
            return 0.0
        return 1.0 - self.deviance / self.null_deviance

    def summary(self) -> str:
        lines = [
            f"{self.family} GLM: 관측 {self.observations}, 모수 {self.parameters}",
            f"  수렴: {'예' if self.converged else '아니오'} ({self.iterations}회)",
            f"  이탈도 {self.deviance:.3f} (영모형 {self.null_deviance:.3f})",
            f"  McFadden R² {self.mcfadden_r2:.4f}",
            "",
            f"  {'변수':24} {'계수':>10} {'표준오차':>10} {'z':>8} {'p':>9}  유의",
        ]
        names = self.feature_names or [f"x{i}" for i in range(self.parameters)]
        for name, coefficient, error, z, p in zip(
            names, self.coefficients, self.standard_errors, self.z_values, self.p_values
        ):
            mark = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else ""))
            shown = f"{p:9.2e}" if p < 1e-4 else f"{p:9.4f}"
            lines.append(
                f"  {name:24} {coefficient:10.4f} {error:10.4f} {z:8.2f} {shown}  {mark}"
            )
        return "\n".join(lines)


def fit_glm(
    design: Matrix,
    targets: Vector,
    family: str = "binomial",
    offset: Vector | None = None,
    ridge: float = 1e-8,
    feature_names: list[str] | None = None,
) -> GlmResult:
    if family not in FAMILIES:
        raise ValueError(f"지원하지 않는 분포족: {family}. {list(FAMILIES)} 중 하나여야 합니다.")

    dist = FAMILIES[family]()
    rows = len(design)
    cols = len(design[0])

    if rows != len(targets):
        raise ValueError(f"행 수 불일치: 설계행렬 {rows}, 응답 {len(targets)}")
    if rows <= cols:
        raise ValueError(
            f"관측 {rows}개로 모수 {cols}개를 추정할 수 없습니다. "
            "설명변수를 줄이거나 표본을 늘리세요."
        )

    offset = offset or [0.0] * rows

    # 초기값: 평균으로 시작하는 게 0벡터보다 안정적입니다.
    initial = dist.initial_mu(targets)
    coefficients = [0.0] * cols
    mus = [initial] * rows
    etas = [0.0] * rows

    previous_deviance = float("inf")
    iterations = 0
    converged = False

    for iterations in range(1, MAX_ITERATIONS + 1):
        weights: Vector = []
        working: Vector = []

        for index in range(rows):
            mu = mus[index]
            eta = etas[index]
            weights.append(dist.weight(mu))
            # 작업 응답에서 offset을 빼야 계수가 offset을 흡수하지 않습니다.
            working.append(
                eta - offset[index] + dist.working_residual(targets[index], mu)
            )

        try:
            normal_matrix = add_ridge(xtwx(design, weights), ridge)
            candidate = solve(normal_matrix, xtwy(design, weights, working))
        except SingularMatrixError:
            # 페널티를 키워 한 번 더 시도합니다.
            normal_matrix = add_ridge(xtwx(design, weights), max(ridge * 1e4, 1e-4))
            candidate = solve(normal_matrix, xtwy(design, weights, working))

        # 이탈도가 늘면 스텝을 반으로 줄여 되돌립니다 (발산 방지).
        step = 1.0
        for _ in range(12):
            trial = [
                old + step * (new - old)
                for old, new in zip(coefficients, candidate)
            ]
            trial_etas = [
                value + offset[index]
                for index, value in enumerate(matvec(design, trial))
            ]
            trial_mus = [dist.linkinv(eta) for eta in trial_etas]
            trial_deviance = sum(
                dist.deviance(targets[index], trial_mus[index])
                for index in range(rows)
            )

            if trial_deviance <= previous_deviance + 1e-10 or step < 1e-4:
                break
            step /= 2.0

        coefficients = trial
        etas = trial_etas
        mus = trial_mus

        if abs(previous_deviance - trial_deviance) < CONVERGENCE_TOLERANCE * (
            abs(trial_deviance) + 0.1
        ):
            previous_deviance = trial_deviance
            converged = True
            break

        previous_deviance = trial_deviance

    # 표준오차 = sqrt(diag((XᵀWX)⁻¹))
    final_weights = [dist.weight(mu) for mu in mus]
    try:
        covariance = inverse(add_ridge(xtwx(design, final_weights), ridge))
        errors = [
            math.sqrt(covariance[index][index]) if covariance[index][index] > 0 else 0.0
            for index in range(cols)
        ]
    except SingularMatrixError:
        errors = [0.0] * cols

    # 영모형 이탈도: 절편만 있는 모형
    null_mu = dist.initial_mu(targets)
    null_deviance = sum(dist.deviance(target, null_mu) for target in targets)

    return GlmResult(
        family=family,
        coefficients=coefficients,
        standard_errors=errors,
        deviance=previous_deviance,
        null_deviance=null_deviance,
        iterations=iterations,
        converged=converged,
        observations=rows,
        parameters=cols,
        feature_names=feature_names or [],
    )


def predict(
    result: GlmResult,
    design: Matrix,
    offset: Vector | None = None,
) -> Vector:
    dist = FAMILIES[result.family]()
    offset = offset or [0.0] * len(design)
    etas = matvec(design, result.coefficients)
    return [dist.linkinv(eta + offset[index]) for index, eta in enumerate(etas)]


def auc(targets: Vector, scores: Vector) -> float:
    positives = [score for target, score in zip(targets, scores) if target > 0.5]
    negatives = [score for target, score in zip(targets, scores) if target <= 0.5]

    if not positives or not negatives:
        return 0.5

    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5

    return wins / (len(positives) * len(negatives))


# ---------------------------------------------------------------- 자체 검증


class _Rng:
    def __init__(self, seed: int = 20260730) -> None:
        self.state = seed

    def uniform(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % (2**31)
        return self.state / (2**31)

    def normal(self) -> float:
        # Box-Muller
        u1 = max(self.uniform(), 1e-12)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    # ---- 선형대수
    print("선형대수")
    from scoring.linalg import matmul, transpose

    a = [[4.0, 3.0], [6.0, 3.0]]
    x = solve(a, [10.0, 12.0])
    check("solve 정확", all(abs(v - e) < 1e-9 for v, e in zip(x, [1.0, 2.0])), str(x))

    inv = inverse(a)
    product = matmul(a, inv)
    check(
        "inverse × 원행렬 = 단위행렬",
        abs(product[0][0] - 1) < 1e-9 and abs(product[0][1]) < 1e-9,
        str(product),
    )

    try:
        solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])
        check("특이행렬 예외", False, "예외가 안 났습니다")
    except SingularMatrixError:
        check("특이행렬 예외", True)

    check(
        "transpose",
        transpose([[1.0, 2.0, 3.0]]) == [[1.0], [2.0], [3.0]],
        "",
    )

    # ---- 로지스틱 계수 복원
    print()
    print("로지스틱 회귀: 계수 복원 (참값을 아는 데이터)")
    rng = _Rng()
    true_beta = [-0.5, 1.8, -1.2, 0.6]   # 절편, x1, x2, x3
    count = 4000

    design = []
    targets = []
    for _ in range(count):
        x1, x2, x3 = rng.normal(), rng.normal(), rng.normal()
        row = [1.0, x1, x2, x3]
        eta = sum(b * v for b, v in zip(true_beta, row))
        probability = 1.0 / (1.0 + math.exp(-eta))
        design.append(row)
        targets.append(1.0 if rng.uniform() < probability else 0.0)

    result = fit_glm(
        design, targets, "binomial",
        feature_names=["절편", "x1", "x2", "x3"],
    )
    print("    " + result.summary().replace("\n", "\n    "))

    check("수렴함", result.converged, f"{result.iterations}회")
    errors = [abs(a - b) for a, b in zip(result.coefficients, true_beta)]
    check(
        f"계수 복원 (최대 오차 {max(errors):.3f})",
        max(errors) < 0.15,
        str([round(c, 3) for c in result.coefficients]),
    )
    check(
        "부호 전부 일치",
        all((a > 0) == (b > 0) for a, b in zip(result.coefficients, true_beta)),
        "",
    )
    check(
        "영향 큰 변수(x1)의 |z|가 가장 큼",
        abs(result.z_values[1]) > abs(result.z_values[3]),
        str([round(z, 2) for z in result.z_values]),
    )
    check("McFadden R² > 0", result.mcfadden_r2 > 0.1, str(result.mcfadden_r2))

    scores = predict(result, design)
    area = auc(targets, scores)
    check(f"AUC {area:.3f} > 0.7", area > 0.7, str(area))

    # ---- 포아송 계수 복원 (offset 포함)
    print()
    print("포아송 회귀: 계수 복원 + offset(노출량) 처리")
    rng2 = _Rng(777)
    true_poisson = [-1.0, 0.9, -0.7]
    design_p = []
    targets_p = []
    offsets_p = []

    for _ in range(3000):
        x1, x2 = rng2.normal(), rng2.normal()
        row = [1.0, x1, x2]
        # 노출량(관로 연장)을 다르게 줍니다: offset이 제대로 처리되는지 확인
        exposure = 0.5 + 3.0 * rng2.uniform()
        eta = sum(b * v for b, v in zip(true_poisson, row)) + math.log(exposure)
        mean = math.exp(eta)

        # 포아송 난수 (Knuth)
        limit = math.exp(-mean) if mean < 700 else 0.0
        product = rng2.uniform()
        draw = 0
        while product > limit and draw < 500:
            draw += 1
            product *= rng2.uniform()

        design_p.append(row)
        targets_p.append(float(draw))
        offsets_p.append(math.log(exposure))

    result_p = fit_glm(
        design_p, targets_p, "poisson",
        offset=offsets_p,
        feature_names=["절편", "x1", "x2"],
    )
    print("    " + result_p.summary().replace("\n", "\n    "))

    check("수렴함", result_p.converged, f"{result_p.iterations}회")
    errors_p = [abs(a - b) for a, b in zip(result_p.coefficients, true_poisson)]
    check(
        f"계수 복원 (최대 오차 {max(errors_p):.3f})",
        max(errors_p) < 0.15,
        str([round(c, 3) for c in result_p.coefficients]),
    )
    check(
        "부호 전부 일치",
        all((a > 0) == (b > 0) for a, b in zip(result_p.coefficients, true_poisson)),
        "",
    )

    # offset을 빼면 계수가 오염되는지 (offset 처리가 실제로 동작하는지 반증)
    without_offset = fit_glm(design_p, targets_p, "poisson")
    check(
        "offset 없이 적합하면 절편이 달라짐 (offset이 실제로 반영됨)",
        abs(without_offset.coefficients[0] - result_p.coefficients[0]) > 0.2,
        f"{without_offset.coefficients[0]:.3f} vs {result_p.coefficients[0]:.3f}",
    )

    # ---- 경계 조건
    print()
    print("경계 조건")
    try:
        fit_glm([[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]], [0.0, 1.0, 1.0], "binomial")
        check("관측 > 모수면 통과", True)
    except ValueError as error:
        check("관측 > 모수면 통과", False, str(error))

    try:
        fit_glm([[1.0, 0.0], [1.0, 1.0]], [0.0, 1.0], "binomial")
        check("관측 ≤ 모수면 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("관측 ≤ 모수면 예외", True)

    try:
        fit_glm(design[:10], targets[:10], "없는분포")
        check("미지원 분포족 예외", False, "")
    except ValueError:
        check("미지원 분포족 예외", True)

    # 완전 분리: 능형 페널티로 발산을 막아야 함
    separated_design = [[1.0, float(index)] for index in range(40)]
    separated_targets = [0.0 if index < 20 else 1.0 for index in range(40)]
    separated = fit_glm(separated_design, separated_targets, "binomial", ridge=1e-4)
    check(
        "완전분리에서도 계수가 유한",
        all(abs(c) < 1e6 for c in separated.coefficients),
        str(separated.coefficients),
    )

    print()
    print("AUC 성질")
    check("완벽 분리 = 1.0", auc([0.0, 0.0, 1.0, 1.0], [0.1, 0.2, 0.8, 0.9]) == 1.0, "")
    check("역방향 = 0.0", auc([0.0, 0.0, 1.0, 1.0], [0.9, 0.8, 0.2, 0.1]) == 0.0, "")
    check("전부 동점 = 0.5", auc([0.0, 1.0], [0.5, 0.5]) == 0.5, "")
    check("한 클래스만 = 0.5", auc([1.0, 1.0], [0.2, 0.8]) == 0.5, "")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
