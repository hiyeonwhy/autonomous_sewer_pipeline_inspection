from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import itertools
import json
from dataclasses import dataclass

from scoring import risk  # noqa: E402

# 기본은 현재 쓰는 모델(risk-v2)입니다. v1을 분석하려면
# `analyze(..., weights=weighted.WEIGHTS, scorer=weighted.score_districts)`.
DEFAULT_WEIGHTS = risk.OCCURRENCE_WEIGHTS
DEFAULT_SCORER = risk.score_districts

# 각 가중치를 이만큼씩 흔듭니다. 흔든 뒤 합이 1이 되도록 재정규화합니다.
DEFAULT_DELTAS = (-0.10, 0.0, 0.10)

# 상위 몇 개 구역의 유지율을 볼지
DEFAULT_TOP_N = 20


def perturb(base: dict[str, float], deltas: tuple[float, ...]) -> list[dict[str, float]]:
    names = list(base)
    combinations: list[dict[str, float]] = []
    seen: set[tuple] = set()

    for offsets in itertools.product(deltas, repeat=len(names)):
        candidate = {
            name: base[name] + offset
            for name, offset in zip(names, offsets)
        }

        # 음수나 0 가중치는 의미가 없으므로 버립니다.
        if any(value <= 0 for value in candidate.values()):
            continue

        total = sum(candidate.values())
        normalized = {
            name: round(value / total, 6)
            for name, value in candidate.items()
        }

        key = tuple(sorted(normalized.items()))
        if key in seen:
            continue
        seen.add(key)
        combinations.append(normalized)

    return combinations


def rank_map(
    districts: list[dict], weights: dict[str, float], scorer=None
) -> dict[str, int]:
    scored = (scorer or DEFAULT_SCORER)(districts, weights)
    return {item.sgg_code: item.rank for item in scored}


def spearman(left: dict[str, int], right: dict[str, int]) -> float:
    codes = sorted(set(left) & set(right))
    count = len(codes)
    if count < 2:
        return 1.0

    squared = sum((left[code] - right[code]) ** 2 for code in codes)
    return 1.0 - (6.0 * squared) / (count * (count * count - 1))


@dataclass
class Sensitivity:
    combination_count: int
    top_n: int
    baseline_top: list[str]
    retention_min: float
    retention_mean: float
    spearman_min: float
    spearman_mean: float
    max_rank_shift: dict[str, int]
    always_top: list[str]

    def to_dict(self) -> dict:
        return {
            "combination_count": self.combination_count,
            "top_n": self.top_n,
            "baseline_top": self.baseline_top,
            "retention_min": round(self.retention_min, 4),
            "retention_mean": round(self.retention_mean, 4),
            "spearman_min": round(self.spearman_min, 4),
            "spearman_mean": round(self.spearman_mean, 4),
            "max_rank_shift": self.max_rank_shift,
            "always_top": self.always_top,
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        return (
            f"가중치 {self.combination_count}개 조합 검증: "
            f"상위 {self.top_n}위 유지율 최소 {self.retention_min:.0%}·평균 {self.retention_mean:.0%}, "
            f"순위상관 최소 {self.spearman_min:.3f}. "
            f"모든 조합에서 상위 {self.top_n}위를 유지한 구역 {len(self.always_top)}개."
        )

    @property
    def verdict(self) -> str:
        if self.retention_min >= 0.8 and self.spearman_min >= 0.95:
            return (
                "가중치 선택에 강건합니다. 어떤 조합에서도 상위권은 거의 같은 구역이 "
                "나오므로, 산정 결과가 임의 가중치에 의존하지 않습니다."
            )
        if self.retention_min >= 0.6:
            return (
                "가중치에 대체로 안정적이나 경계 구역은 순위가 바뀝니다. "
                "상위권 판정에는 쓸 수 있고, 개별 순위는 근사값으로 제시하세요."
            )
        return (
            "가중치에 민감합니다. 순위를 단정하지 말고 등급(상·중·하) 단위로 "
            "제시하거나, 가중치 근거를 별도로 확보하세요."
        )


def analyze(
    districts: list[dict],
    top_n: int = DEFAULT_TOP_N,
    deltas: tuple[float, ...] = DEFAULT_DELTAS,
    weights: dict[str, float] | None = None,
    scorer=None,
) -> Sensitivity:
    if not districts:
        raise ValueError("districts가 비어 있습니다.")

    top_n = min(top_n, len(districts))
    base_weights = dict(weights or DEFAULT_WEIGHTS)

    baseline = rank_map(districts, base_weights, scorer)
    baseline_top = sorted(
        (code for code, rank in baseline.items() if rank <= top_n),
        key=lambda code: baseline[code],
    )
    baseline_top_set = set(baseline_top)

    combinations = perturb(base_weights, deltas)

    retentions: list[float] = []
    correlations: list[float] = []
    shifts: dict[str, int] = {code: 0 for code in baseline}
    always_top = set(baseline_top_set)

    for combo in combinations:
        ranks = rank_map(districts, combo, scorer)

        current_top = {code for code, rank in ranks.items() if rank <= top_n}
        retentions.append(len(baseline_top_set & current_top) / top_n)
        always_top &= current_top

        correlations.append(spearman(baseline, ranks))

        for code, rank in ranks.items():
            shifts[code] = max(shifts[code], abs(rank - baseline[code]))

    return Sensitivity(
        combination_count=len(combinations),
        top_n=top_n,
        baseline_top=baseline_top,
        retention_min=min(retentions),
        retention_mean=sum(retentions) / len(retentions),
        spearman_min=min(correlations),
        spearman_mean=sum(correlations) / len(correlations),
        # 순위가 가장 많이 흔들린 구역 상위 10개만 남깁니다.
        max_rank_shift=dict(
            sorted(shifts.items(), key=lambda pair: -pair[1])[:10]
        ),
        always_top=sorted(always_top, key=lambda code: baseline[code]),
    )


# ---------------------------------------------------------------- 자체 검증


def _synthetic(count: int, seed: int = 12345) -> list[dict]:
    districts = []
    state = seed

    def nxt() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    for index in range(count):
        districts.append(
            {
                "sgg_code": f"{10000 + index}",
                "full_name": f"테스트시 {index}구",
                # v2(risk) 지표
                "aged_ratio_25y": nxt(),
                "concrete_ratio": nxt(),
                "large_diameter_ratio": nxt(),
                # v1·v2 공통
                "combined_sewer_ratio": nxt(),
                "subsidence_per_km": nxt() * 0.5,
                "unconnected_ratio": nxt() * 0.3,
                # v1 전용 (두 모델 모두 검증할 수 있게 남겨 둡니다)
                "facility_avg_age_years": nxt() * 40,
            }
        )
    return districts


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("가중치 조합 생성")
    combos = perturb(dict(DEFAULT_WEIGHTS), DEFAULT_DELTAS)
    check("조합이 생성됨", len(combos) > 1, str(len(combos)))
    check(
        "모든 조합의 합이 1",
        all(abs(sum(combo.values()) - 1.0) < 1e-5 for combo in combos),
        "",
    )
    check(
        "음수·0 가중치 없음",
        all(all(value > 0 for value in combo.values()) for combo in combos),
        "",
    )
    check(
        "기준 가중치가 조합에 포함",
        any(
            all(abs(combo[name] - DEFAULT_WEIGHTS[name]) < 1e-6 for name in DEFAULT_WEIGHTS)
            for combo in combos
        ),
        "",
    )

    print()
    print("가중치 주입 (전역을 건드리지 않음)")
    before = dict(DEFAULT_WEIGHTS)
    equal = {name: 1.0 / len(DEFAULT_WEIGHTS) for name in DEFAULT_WEIGHTS}
    injected = rank_map(_synthetic(5), equal)
    check("호출 후 기본 가중치가 그대로", DEFAULT_WEIGHTS == before, str(DEFAULT_WEIGHTS))
    check("주입한 가중치로 순위가 나옴", len(injected) == 5, str(len(injected)))

    # 다른 모델도 같은 인터페이스로 분석되는지 확인합니다. 모델을 바꿀 때
    # 민감도 보고가 조용히 옛 모델을 가리키는 것이 원래 문제였습니다.
    from scoring import weighted as _v1

    v1_ranks = rank_map(
        _synthetic(5), dict(_v1.WEIGHTS), scorer=_v1.score_districts
    )
    check("v1 모델도 같은 인터페이스로 분석 가능", len(v1_ranks) == 5, str(len(v1_ranks)))

    print()
    print("순위 상관")
    check("동일 순위는 1.0", spearman({"a": 1, "b": 2}, {"a": 1, "b": 2}) == 1.0, "")
    check(
        "완전 역순은 -1.0",
        abs(spearman({"a": 1, "b": 2}, {"a": 2, "b": 1}) - (-1.0)) < 1e-9,
        str(spearman({"a": 1, "b": 2}, {"a": 2, "b": 1})),
    )

    print()
    print("민감도 분석: 무작위 데이터 (229개, 실제 규모)")
    result = analyze(_synthetic(229), top_n=20)
    print(f"    {result.summary}")
    check("조합 수 기록됨", result.combination_count == len(combos), "")
    check("유지율 0~1 범위", 0 <= result.retention_min <= 1, str(result.retention_min))
    check(
        "평균 ≥ 최소",
        result.retention_mean >= result.retention_min - 1e-9,
        "",
    )
    check("상위 목록 길이 = top_n", len(result.baseline_top) == 20, str(len(result.baseline_top)))
    check(
        "always_top ⊆ baseline_top",
        set(result.always_top) <= set(result.baseline_top),
        "",
    )
    check(
        "순위상관 ≤ 1",
        result.spearman_min <= 1.0 + 1e-9 and result.spearman_mean <= 1.0 + 1e-9,
        f"min={result.spearman_min} mean={result.spearman_mean}",
    )

    print()
    print("민감도 분석: 한 지표가 지배적인 데이터")
    # 침하만 크게 다르고 나머지는 동일하면, 가중치를 흔들어도 순위가 안 바뀌어야 함
    dominant = []
    for index in range(50):
        dominant.append(
            {
                "sgg_code": f"{20000 + index}",
                "full_name": f"D{index}",
                "combined_sewer_ratio": 0.5,
                "facility_avg_age_years": 20.0,
                "subsidence_per_km": index / 50.0,
                "unconnected_ratio": 0.1,
            }
        )
    dominant_result = analyze(dominant, top_n=10)
    print(f"    {dominant_result.summary}")
    check(
        "지배적 지표가 있으면 유지율 100%",
        dominant_result.retention_min == 1.0,
        str(dominant_result.retention_min),
    )
    check(
        "지배적 지표가 있으면 순위상관 1.0",
        abs(dominant_result.spearman_min - 1.0) < 1e-9,
        str(dominant_result.spearman_min),
    )
    check(
        "판정문이 강건으로 나옴",
        "강건" in dominant_result.verdict,
        dominant_result.verdict,
    )

    print()
    print("경계 조건")
    try:
        analyze([])
        check("빈 입력은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("빈 입력은 예외", True)

    small = analyze(_synthetic(3), top_n=20)
    check("top_n이 데이터 수로 축소됨", small.top_n == 3, str(small.top_n))

    print()
    print("직렬화")
    try:
        json.dumps(result.to_dict(), ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    print()
    print("판정")
    print(f"    {result.verdict}")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
