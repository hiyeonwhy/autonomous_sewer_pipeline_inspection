from __future__ import annotations

import math
from dataclasses import dataclass

Vector = list[float]

# 열의 모든 값이 같으면 변별력이 0이므로 가중치도 0이 되어야 합니다.
CONSTANT_TOLERANCE = 1e-12


@dataclass
class WeightResult:
    method: str
    weights: dict[str, float]
    detail: dict[str, dict]

    def summary(self) -> str:
        lines = [f"{self.method} 가중치"]
        for name, weight in sorted(self.weights.items(), key=lambda pair: -pair[1]):
            bar = "█" * int(weight * 60)
            lines.append(f"  {name:26} {weight:.4f}  {bar}")
        return "\n".join(lines)


def _columns(
    districts: list[dict],
    feature_names: list[str],
) -> dict[str, Vector]:
    columns: dict[str, Vector] = {}

    for name in feature_names:
        raw = [district.get(name) for district in districts]
        observed = sorted(value for value in raw if value is not None)

        if not observed:
            raise ValueError(f"지표 {name!r}에 관측값이 하나도 없습니다.")

        middle = len(observed) // 2
        median = (
            observed[middle]
            if len(observed) % 2 == 1
            else (observed[middle - 1] + observed[middle]) / 2.0
        )

        columns[name] = [
            median if value is None else float(value) for value in raw
        ]

    return columns


def _shift_nonnegative(column: Vector) -> Vector:
    smallest = min(column)
    if smallest >= 0:
        return list(column)
    return [value - smallest for value in column]


def entropy_weights(
    districts: list[dict],
    feature_names: list[str],
) -> WeightResult:
    columns = _columns(districts, feature_names)
    count = len(districts)

    if count < 2:
        raise ValueError("엔트로피 가중법은 관측이 2개 이상이어야 합니다.")

    normalizer = 1.0 / math.log(count)
    diversities: dict[str, float] = {}
    detail: dict[str, dict] = {}

    for name, column in columns.items():
        shifted = _shift_nonnegative(column)
        total = sum(shifted)

        if total <= CONSTANT_TOLERANCE:
            diversities[name] = 0.0
            detail[name] = {"entropy": 1.0, "diversity": 0.0, "note": "전부 0"}
            continue

        entropy = 0.0
        for value in shifted:
            proportion = value / total
            if proportion > 0.0:
                entropy -= proportion * math.log(proportion)
        entropy *= normalizer

        # 부동소수 오차로 1을 살짝 넘을 수 있어 자릅니다.
        entropy = min(1.0, max(0.0, entropy))
        diversity = 1.0 - entropy

        diversities[name] = diversity
        detail[name] = {"entropy": round(entropy, 6), "diversity": round(diversity, 6)}

    total_diversity = sum(diversities.values())

    if total_diversity <= CONSTANT_TOLERANCE:
        # 모든 지표가 상수면 균등 가중이 유일한 선택입니다.
        uniform = 1.0 / len(feature_names)
        weights = {name: uniform for name in feature_names}
    else:
        weights = {
            name: value / total_diversity for name, value in diversities.items()
        }

    return WeightResult("엔트로피", weights, detail)


def pearson(left: Vector, right: Vector) -> float:
    count = len(left)
    mean_left = sum(left) / count
    mean_right = sum(right) / count

    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right)
    )
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)

    denominator = math.sqrt(variance_left * variance_right)
    if denominator < CONSTANT_TOLERANCE:
        return 0.0

    return covariance / denominator


def critic_weights(
    districts: list[dict],
    feature_names: list[str],
) -> WeightResult:
    columns = _columns(districts, feature_names)
    count = len(districts)

    if count < 3:
        raise ValueError("CRITIC은 관측이 3개 이상이어야 합니다 (상관 계산 필요).")

    # 대비 강도 = 표준편차
    deviations: dict[str, float] = {}
    for name, column in columns.items():
        mean = sum(column) / count
        variance = sum((value - mean) ** 2 for value in column) / (count - 1)
        deviations[name] = math.sqrt(variance)

    # 갈등 정도 = Σ(1 - 상관)
    conflicts: dict[str, float] = {}
    correlations: dict[str, dict[str, float]] = {}

    for name in feature_names:
        conflict = 0.0
        row: dict[str, float] = {}
        for other in feature_names:
            if other == name:
                row[other] = 1.0
                continue
            correlation = pearson(columns[name], columns[other])
            row[other] = round(correlation, 6)
            conflict += 1.0 - correlation
        conflicts[name] = conflict
        correlations[name] = row

    information = {
        name: deviations[name] * conflicts[name] for name in feature_names
    }
    total = sum(information.values())

    if total <= CONSTANT_TOLERANCE:
        uniform = 1.0 / len(feature_names)
        weights = {name: uniform for name in feature_names}
    else:
        weights = {name: value / total for name, value in information.items()}

    detail = {
        name: {
            "std": round(deviations[name], 6),
            "conflict": round(conflicts[name], 6),
            "information": round(information[name], 6),
            "correlations": correlations[name],
        }
        for name in feature_names
    }

    return WeightResult("CRITIC", weights, detail)


def compare_weights(sets: dict[str, dict[str, float]]) -> str:
    if not sets:
        return "(비교할 가중치가 없습니다)"

    names = sorted({name for weights in sets.values() for name in weights})
    methods = list(sets)

    header = f"  {'지표':26}" + "".join(f"{method:>14}" for method in methods)
    lines = [header, "  " + "-" * (26 + 14 * len(methods))]

    for name in names:
        row = f"  {name:26}"
        for method in methods:
            row += f"{sets[method].get(name, 0.0):14.4f}"
        lines.append(row)

    # 방법 간 최대 격차: 클수록 방법 선택이 결과를 바꿉니다
    lines.append("  " + "-" * (26 + 14 * len(methods)))
    gaps = []
    for name in names:
        values = [sets[method].get(name, 0.0) for method in methods]
        gaps.append(max(values) - min(values))
    lines.append(f"  지표별 최대 격차 평균: {sum(gaps) / len(gaps):.4f}")

    return "\n".join(lines)


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    features = ["a", "b", "c"]

    # a: 값이 크게 퍼짐 (변별력 큼)
    # b: 거의 상수 (변별력 없음)
    # c: 중간
    districts = []
    for index in range(50):
        districts.append(
            {
                "sgg_code": f"{index}",
                "a": float(index),
                "b": 10.0 + (index % 2) * 0.001,
                "c": float(index % 5),
            }
        )

    print("엔트로피 가중법")
    entropy = entropy_weights(districts, features)
    print("    " + entropy.summary().replace("\n", "\n    "))

    check(
        "가중치 합 = 1",
        abs(sum(entropy.weights.values()) - 1.0) < 1e-9,
        str(sum(entropy.weights.values())),
    )
    check("모든 가중치 ≥ 0", all(w >= 0 for w in entropy.weights.values()), "")
    check(
        "거의 상수인 b의 가중치가 가장 작음",
        entropy.weights["b"] == min(entropy.weights.values()),
        str(entropy.weights),
    )
    check(
        "가장 퍼진 a가 b보다 큰 가중치",
        entropy.weights["a"] > entropy.weights["b"],
        "",
    )

    print()
    print("CRITIC 법")
    critic = critic_weights(districts, features)
    print("    " + critic.summary().replace("\n", "\n    "))

    check(
        "가중치 합 = 1",
        abs(sum(critic.weights.values()) - 1.0) < 1e-9,
        str(sum(critic.weights.values())),
    )
    check("모든 가중치 ≥ 0", all(w >= 0 for w in critic.weights.values()), "")

    print()
    print("CRITIC: 중복 지표 처리 (핵심 성질)")
    # d를 a의 복제로 넣으면, 중복이므로 a와 d의 가중치가 깎여야 합니다.
    duplicated = [dict(row, d=row["a"]) for row in districts]
    critic_dup = critic_weights(duplicated, features + ["d"])
    print("    " + critic_dup.summary().replace("\n", "\n    "))
    check(
        "a와 d(복제)의 가중치가 동일",
        abs(critic_dup.weights["a"] - critic_dup.weights["d"]) < 1e-9,
        f"{critic_dup.weights['a']} vs {critic_dup.weights['d']}",
    )
    check(
        "복제 추가로 a의 가중치가 감소 (중복 페널티)",
        critic_dup.weights["a"] < critic.weights["a"],
        f"{critic_dup.weights['a']:.4f} < {critic.weights['a']:.4f}",
    )
    check(
        "a·d 상관이 1.0으로 잡힘",
        abs(critic_dup.detail["a"]["correlations"]["d"] - 1.0) < 1e-6,
        str(critic_dup.detail["a"]["correlations"]["d"]),
    )

    print()
    print("엔트로피: 중복에 둔감함 (CRITIC과의 차이)")
    entropy_dup = entropy_weights(duplicated, features + ["d"])
    check(
        "엔트로피는 a와 d를 각각 온전히 계산 (중복 페널티 없음)",
        abs(entropy_dup.detail["a"]["diversity"] - entropy_dup.detail["d"]["diversity"])
        < 1e-9,
        "",
    )

    print()
    print("결측·경계 조건")
    with_none = [dict(row) for row in districts]
    with_none[0]["a"] = None
    with_none[1]["a"] = None
    filled = entropy_weights(with_none, features)
    check("결측이 있어도 계산됨", abs(sum(filled.weights.values()) - 1.0) < 1e-9, "")

    constant = [{"a": 5.0, "b": 5.0, "c": 5.0} for _ in range(10)]
    flat = entropy_weights(constant, features)
    check(
        "전부 상수면 균등 가중",
        all(abs(w - 1 / 3) < 1e-9 for w in flat.weights.values()),
        str(flat.weights),
    )

    negative = [{"a": float(index) - 25.0, "b": 1.0, "c": 2.0} for index in range(50)]
    shifted = entropy_weights(negative, features)
    check(
        "음수 값도 처리 (평행이동)",
        abs(sum(shifted.weights.values()) - 1.0) < 1e-9,
        "",
    )

    try:
        entropy_weights([{"a": 1.0}], ["a"])
        check("관측 1개면 예외", False, "")
    except ValueError:
        check("관측 1개면 예외", True)

    try:
        entropy_weights(districts, ["없는지표"])
        check("관측값 없는 지표는 예외", False, "")
    except ValueError:
        check("관측값 없는 지표는 예외", True)

    print()
    print("가중치 비교 표")
    manual = {"a": 0.5, "b": 0.2, "c": 0.3}
    table = compare_weights(
        {"수동": manual, "엔트로피": entropy.weights, "CRITIC": critic.weights}
    )
    print("    " + table.replace("\n", "\n    "))
    check("비교 표 생성", "지표별 최대 격차" in table, "")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
