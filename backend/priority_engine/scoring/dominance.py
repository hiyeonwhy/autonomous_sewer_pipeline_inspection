from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import math
from dataclasses import dataclass, field

from scoring import risk  # noqa: E402
from scoring.weighted import percentiles  # noqa: E402

# 최종 점수는 발생확률(가중합) × 영향배율입니다. 영향배율은 영향도 백분위의
# 증가함수이므로, 두 축 전부에서 우세할 때만 "어떤 가중치에서도 우세하다"가
# 성립합니다. 발생확률 지표만 비교하면 영향도가 낮은 구역을 잘못 지배 판정합니다.
# 중점관리대상은 점수에 반영되지 않지만(배율 제거, 2026-08-03) 지배 판정에는
# 축으로 남깁니다 — 지정 구역을 미지정 구역이 지배했다고 판정하지 않는 보수적
# 선택이고, 이 축을 빼면 배제가 늘어나는 방향이라 결론이 과감해집니다.
OCCURRENCE_FEATURES = tuple(risk.OCCURRENCE_WEIGHTS)
IMPACT_FEATURES = risk.IMPACT_FEATURES
TARGET_FLAG = "is_priority_target"

# 상위 몇 %를 선별 대상으로 볼지. 기획서의 "상위 20% 선별"과 맞춥니다.
DEFAULT_TOP_FRACTION = 0.2


def feature_names() -> list[str]:
    return [*OCCURRENCE_FEATURES, *IMPACT_FEATURES, TARGET_FLAG]


def percentile_vectors(districts: list[dict]) -> dict[str, list[float]]:
    ranked = [*OCCURRENCE_FEATURES, *IMPACT_FEATURES]
    by_feature = {
        name: percentiles([row.get(name) for row in districts]) for name in ranked
    }

    vectors: dict[str, list[float]] = {}
    for index, row in enumerate(districts):
        vector = [by_feature[name][index] for name in ranked]
        # 중점관리대상은 불리언이라 백분위를 매기지 않습니다. None 을 백분위로
        # 넘기면 0.5 로 패딩되어 "미지정(False)"보다 위로 올라갑니다.
        vector.append(1.0 if row.get(TARGET_FLAG) else 0.0)
        vectors[row["sgg_code"]] = vector
    return vectors


def dominates(left: list[float], right: list[float]) -> bool:
    return all(a >= b for a, b in zip(left, right)) and any(
        a > b for a, b in zip(left, right)
    )


def relation_counts(
    vectors: dict[str, list[float]],
) -> tuple[dict[str, int], dict[str, int]]:
    codes = list(vectors)
    dominated_by = {code: 0 for code in codes}
    dominating = {code: 0 for code in codes}

    for left in codes:
        for right in codes:
            if left == right:
                continue
            if dominates(vectors[left], vectors[right]):
                dominating[left] += 1
                dominated_by[right] += 1

    return dominated_by, dominating


@dataclass
class Dominance:
    district_count: int
    features: list[str]
    top_k: int
    skyline: list[str]
    excluded: list[str]
    guaranteed: list[str]
    rank_bounds: dict[str, tuple[int, int]] = field(default_factory=dict)
    padding_dependent: int = 0

    @property
    def candidate_count(self) -> int:
        return self.district_count - len(self.excluded)

    @property
    def interval_widths(self) -> list[int]:
        return [worst - best for best, worst in self.rank_bounds.values()]

    def to_dict(self) -> dict:
        widths = self.interval_widths
        return {
            "district_count": self.district_count,
            "features": self.features,
            "top_k": self.top_k,
            "skyline": self.skyline,
            "excluded": self.excluded,
            "guaranteed": self.guaranteed,
            "candidate_count": self.candidate_count,
            "padding_dependent": self.padding_dependent,
            "interval_width_mean": round(sum(widths) / len(widths), 1) if widths else 0,
            "interval_width_min": min(widths) if widths else 0,
            "rank_bounds": {code: list(pair) for code, pair in self.rank_bounds.items()},
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        return (
            f"가중치 없이 확정: {self.district_count}개 중 {len(self.excluded)}개는 "
            f"어떤 가중치로도 상위 {self.top_k}위에 들 수 없습니다 "
            f"(후보 {self.candidate_count}개로 축소). "
            f"비지배 구역 {len(self.skyline)}개, "
            f"모든 가중치에서 상위 {self.top_k}위가 보장되는 구역 {len(self.guaranteed)}개."
        )

    @property
    def verdict(self) -> str:
        widths = self.interval_widths
        mean_width = sum(widths) / len(widths) if widths else 0
        share = len(self.excluded) / self.district_count if self.district_count else 0

        lines = []
        if share >= 0.2:
            lines.append(
                f"배제에는 강합니다. 하위 {len(self.excluded)}개 구역의 제외는 "
                "가중치를 정하지 않고도 증명됩니다."
            )
        else:
            lines.append(
                f"배제로 걸러지는 구역이 {len(self.excluded)}개뿐입니다. "
                "지표들이 서로 상충해 지배 관계가 드물다는 뜻입니다."
            )

        lines.append(
            f"선별에는 약합니다. 순위 구간 폭이 평균 {mean_width:.0f}위라 "
            "개별 순위는 가중치 없이 확정되지 않습니다."
        )
        lines.append(
            "가중치의 대체가 아니라 보완입니다. 상위권 선정에는 여전히 "
            "가중치가 필요합니다."
        )
        if self.padding_dependent:
            lines.append(
                f"단 배제 {len(self.excluded)}개 중 {self.padding_dependent}개는 "
                "일부 결측 지표를 포함해 중앙값 패딩 처리에 의존합니다. "
                f"관측값만으로 배제가 증명되는 구역은 "
                f"{len(self.excluded) - self.padding_dependent}개입니다."
            )
        return " ".join(lines)


def analyze(
    districts: list[dict], top_fraction: float = DEFAULT_TOP_FRACTION
) -> Dominance:
    if not districts:
        raise ValueError("districts가 비어 있습니다.")
    if not 0 < top_fraction <= 1:
        raise ValueError(f"top_fraction 은 0 초과 1 이하여야 합니다: {top_fraction}")

    count = len(districts)
    top_k = max(1, math.ceil(count * top_fraction))

    vectors = percentile_vectors(districts)
    dominated_by, dominating = relation_counts(vectors)
    codes = list(vectors)

    # 나를 지배하는 구역이 k개 이상이면, 어떤 가중치에서도 그 k개가 모두 나보다
    # 위에 있으므로 상위 k위가 불가능합니다. 필요조건 위반이라 확실한 배제이고,
    # 반대로 여기 없는 구역이 반드시 가능하다는 뜻은 아닙니다 (하한입니다).
    excluded = [code for code in codes if dominated_by[code] >= top_k]

    # 내가 지배하지 못하는 구역만 나를 넘어설 수 있습니다. 그 수가 k보다 작으면
    # 어떤 가중치에서도 상위 k위입니다. 충분조건이라 보수적입니다 - 0개가 나와도
    # "보장되는 구역이 없다"는 증명은 아닙니다.
    guaranteed = [
        code for code in codes if (count - 1 - dominating[code]) < top_k
    ]

    skyline = [code for code in codes if dominated_by[code] == 0]

    # 지배 관계만으로 확정되는 순위 구간. 나를 지배하는 구역은 반드시 위에,
    # 내가 지배하는 구역은 반드시 아래에 있습니다.
    rank_bounds = {
        code: (1 + dominated_by[code], count - dominating[code]) for code in codes
    }

    # 전 구역이 결측인 지표는 모두가 같은 0.5를 받아 지배 판정에 영향을 주지
    # 않습니다(현재 침하가 그렇습니다). 판정을 왜곡하는 것은 일부만 결측인
    # 지표뿐이므로 그것만 셉니다. 전부 세면 침하 하나 때문에 229개 전부가
    # "패딩 의존"으로 잡혀 지표가 무의미해집니다.
    partial = [
        name
        for name in [*OCCURRENCE_FEATURES, *IMPACT_FEATURES]
        if 0 < sum(1 for row in districts if row.get(name) is not None) < count
    ]
    excluded_set = set(excluded)
    padding_dependent = sum(
        1
        for row in districts
        if row["sgg_code"] in excluded_set
        and any(row.get(name) is None for name in partial)
    )

    order = {row["sgg_code"]: index for index, row in enumerate(districts)}
    return Dominance(
        district_count=count,
        features=feature_names(),
        top_k=top_k,
        skyline=sorted(skyline, key=lambda code: order[code]),
        excluded=sorted(excluded, key=lambda code: order[code]),
        guaranteed=sorted(guaranteed, key=lambda code: order[code]),
        rank_bounds=rank_bounds,
        padding_dependent=padding_dependent,
    )


def bounds_table(result: Dominance, districts: list[dict], limit: int = 10) -> str:
    names = {row["sgg_code"]: row.get("full_name", "") for row in districts}
    ordered = sorted(result.rank_bounds.items(), key=lambda pair: pair[1])

    lines = [f"  {'구역':22} {'가능한 최고':>10} {'가능한 최저':>10}"]
    lines.append("  " + "-" * 44)
    for code, (best, worst) in ordered[:limit]:
        lines.append(f"  {names.get(code, code):22} {best:9d}위 {worst:9d}위")
    return "\n".join(lines)


# ---------------------------------------------------------------- 자체 검증


def _synthetic(count: int, seed: int = 20260731) -> list[dict]:
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
                "aged_ratio_25y": nxt(),
                "concrete_ratio": nxt(),
                "subsidence_per_km": nxt() * 0.5,
                "combined_sewer_ratio": nxt(),
                "heavy_rain_zone_ratio": nxt() * 0.4,
                "large_diameter_ratio": nxt(),
                "population": nxt() * 500_000,
            }
        )
    return districts


def _flat(count: int, varying: str | None = None) -> list[dict]:
    districts = []
    for index in range(count):
        row = {
            "sgg_code": f"{20000 + index}",
            "full_name": f"F{index}",
            "aged_ratio_25y": 0.5,
            "concrete_ratio": 0.5,
            "subsidence_per_km": 0.2,
            "combined_sewer_ratio": 0.5,
            "heavy_rain_zone_ratio": 0.3,
            "large_diameter_ratio": 0.4,
            "population": 100_000,
        }
        if varying:
            row[varying] = index / count
        districts.append(row)
    return districts


def _random_weights(count: int, seed: int = 777) -> list[dict[str, float]]:
    combos = []
    state = seed
    names = list(risk.OCCURRENCE_WEIGHTS)

    def nxt() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    for _ in range(count):
        raw = {name: nxt() + 0.01 for name in names}
        total = sum(raw.values())
        combos.append({name: value / total for name, value in raw.items()})
    return combos


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("지배 관계 정의")
    check("전부 크면 지배", dominates([1.0, 1.0], [0.0, 0.0]))
    check("전부 같으면 지배 아님", not dominates([1.0, 1.0], [1.0, 1.0]))
    check("일부만 크면 지배 아님", not dominates([1.0, 0.0], [0.0, 1.0]))
    check("하나만 크고 나머지 같으면 지배", dominates([1.0, 0.5], [0.9, 0.5]))
    check(
        "반대칭: A가 B를 지배하면 B는 A를 지배 못함",
        dominates([1.0, 1.0], [0.5, 0.5]) and not dominates([0.5, 0.5], [1.0, 1.0]),
    )
    check(
        "이행성: A≻B, B≻C 이면 A≻C",
        dominates([3.0, 3.0], [2.0, 2.0])
        and dominates([2.0, 2.0], [1.0, 1.0])
        and dominates([3.0, 3.0], [1.0, 1.0]),
    )

    print()
    print("비교 축에 영향도·중점관리대상이 포함됨")
    # 개수를 박으면 지표를 추가할 때마다 깨집니다. 구성이 빠짐없이 들어갔는지만
    # 봅니다 (3차 개정에서 영향도가 1개 → 2개가 됐습니다).
    check(
        "비교 축 = 발생확률 + 영향도 + 중점관리 플래그",
        feature_names() == [*OCCURRENCE_FEATURES, *IMPACT_FEATURES, TARGET_FLAG],
        str(feature_names()),
    )
    check("발생확률 지표 전체 포함", set(OCCURRENCE_FEATURES) <= set(feature_names()))
    check("영향도 지표 전체 포함", set(IMPACT_FEATURES) <= set(feature_names()))

    flagged = _flat(4)
    flagged[0][TARGET_FLAG] = True
    vectors = percentile_vectors(flagged)
    check(
        "중점관리대상 지정이 벡터 마지막 축에 1.0",
        vectors[flagged[0]["sgg_code"]][-1] == 1.0,
        str(vectors[flagged[0]["sgg_code"]][-1]),
    )
    check(
        "미지정은 0.0 (None 이 0.5로 패딩되지 않음)",
        vectors[flagged[1]["sgg_code"]][-1] == 0.0,
        str(vectors[flagged[1]["sgg_code"]][-1]),
    )
    check(
        "다른 조건이 같으면 지정 구역이 미지정 구역을 지배",
        dominates(
            vectors[flagged[0]["sgg_code"]], vectors[flagged[1]["sgg_code"]]
        ),
    )

    print()
    print("정당성: 지배 관계가 실제 점수 순서를 보존하는가 (무작위 가중치 200개)")
    districts = _synthetic(40)
    vectors = percentile_vectors(districts)
    codes = list(vectors)
    pairs = [
        (a, b)
        for a in codes
        for b in codes
        if a != b and dominates(vectors[a], vectors[b])
    ]
    check("지배 쌍이 존재", len(pairs) > 0, str(len(pairs)))

    violations = 0
    for weights in _random_weights(200):
        scored = risk.score_districts(districts, weights)
        score_of = {item.sgg_code: item.score for item in scored}
        for left, right in pairs:
            if score_of[left] < score_of[right] - 1e-9:
                violations += 1
    check(
        "모든 가중치에서 지배 구역의 점수가 더 높음",
        violations == 0,
        f"위반 {violations}건",
    )

    print()
    print("정당성: 실제 순위가 가중치 무관 구간 안에 있는가")
    result = analyze(districts)
    scored = risk.score_districts(districts)
    outside = [
        item.sgg_code
        for item in scored
        if not (
            result.rank_bounds[item.sgg_code][0]
            <= item.rank
            <= result.rank_bounds[item.sgg_code][1]
        )
    ]
    check("기본 가중치 순위가 구간 안", not outside, str(outside[:5]))

    equal = {name: 1.0 / len(risk.OCCURRENCE_WEIGHTS) for name in risk.OCCURRENCE_WEIGHTS}
    outside_equal = [
        item.sgg_code
        for item in risk.score_districts(districts, equal)
        if not (
            result.rank_bounds[item.sgg_code][0]
            <= item.rank
            <= result.rank_bounds[item.sgg_code][1]
        )
    ]
    check("등가중치 순위도 구간 안", not outside_equal, str(outside_equal[:5]))

    print()
    print("집합 성질")
    check("스카이라인이 비어있지 않음", len(result.skyline) > 0)
    check(
        "스카이라인 구역은 아무에게도 지배당하지 않음",
        all(result.rank_bounds[code][0] == 1 for code in result.skyline),
    )
    check(
        "배제와 보장은 서로소",
        not (set(result.excluded) & set(result.guaranteed)),
    )
    check(
        "배제 구역은 스카이라인에 없음",
        not (set(result.excluded) & set(result.skyline)),
    )
    check(
        "후보 수 = 전체 - 배제",
        result.candidate_count == result.district_count - len(result.excluded),
    )
    check(
        "순위 구간이 1..n 안에 있음",
        all(
            1 <= best <= worst <= result.district_count
            for best, worst in result.rank_bounds.values()
        ),
    )

    print()
    print("극단 데이터: 한 지표만 다른 경우 (완전 순서)")
    ordered = analyze(_flat(50, varying="aged_ratio_25y"), top_fraction=0.2)
    check("스카이라인 1개", len(ordered.skyline) == 1, str(len(ordered.skyline)))
    check(
        f"배제 = 전체 - 상위 {ordered.top_k}",
        len(ordered.excluded) == 50 - ordered.top_k,
        f"{len(ordered.excluded)} vs {50 - ordered.top_k}",
    )
    check(
        "보장 = 상위 k개",
        len(ordered.guaranteed) == ordered.top_k,
        str(len(ordered.guaranteed)),
    )
    check(
        "순위가 완전히 확정됨 (구간 폭 0)",
        max(ordered.interval_widths) == 0,
        str(max(ordered.interval_widths)),
    )

    print()
    print("극단 데이터: 전부 동일한 경우")
    same = analyze(_flat(20), top_fraction=0.2)
    check("지배 관계 없음 → 스카이라인 = 전체", len(same.skyline) == 20)
    check("배제 0개", len(same.excluded) == 0, str(len(same.excluded)))
    check(
        "구간 폭 = 전체 (아무것도 확정 안 됨)",
        min(same.interval_widths) == 19,
        str(min(same.interval_widths)),
    )
    check("판정문이 약점을 명시", "선별에는 약합니다" in same.verdict)

    print()
    print("결측 처리")
    missing = _synthetic(30)
    for row in missing[:10]:
        row["aged_ratio_25y"] = None
    vectors = percentile_vectors(missing)
    # 위치가 아니라 이름으로 찾습니다. 가중치를 개정하면 OCCURRENCE_FEATURES 의
    # 순서가 바뀌어, 위치로 집으면 다른 지표를 검사하게 됩니다.
    aged_index = [*OCCURRENCE_FEATURES, *IMPACT_FEATURES].index("aged_ratio_25y")
    check(
        "결측은 백분위 0.5로 패딩",
        vectors[missing[0]["sgg_code"]][aged_index] == 0.5,
        str(vectors[missing[0]["sgg_code"]][aged_index]),
    )
    missing_result = analyze(missing)
    check(
        "패딩 의존 구역 수가 집계됨",
        missing_result.padding_dependent
        <= len(missing_result.excluded),
        f"{missing_result.padding_dependent} vs {len(missing_result.excluded)}",
    )

    # 전 구역 결측 지표는 모두 같은 0.5 패딩을 받아 어느 쌍의 우열도 못 가리므로
    # 패딩 의존으로 세면 안 되고, 전 구역이 같은 값으로 관측된 경우와 지배 결과가
    # 같아야 합니다 (패딩이 중립이라는 뜻). 예전 이 검사는 지표명이 3차 개정 전
    # 이름이라 존재하지 않는 키를 None 으로 바꿔 아무것도 검증하지 않았습니다.
    all_missing = _synthetic(30)
    uniform = _synthetic(30)
    for row_missing, row_uniform in zip(all_missing, uniform):
        row_missing["subsidence_per_km"] = None
        row_uniform["subsidence_per_km"] = 0.3
    whole = analyze(all_missing)
    check(
        "전 구역 결측 지표는 패딩 의존으로 세지 않음",
        whole.padding_dependent == 0,
        str(whole.padding_dependent),
    )
    check(
        "전 구역 결측은 전 구역 동일 관측과 같은 지배 결과",
        analyze(uniform).excluded == whole.excluded,
        f"{len(analyze(uniform).excluded)} vs {len(whole.excluded)}",
    )

    print()
    print("경계 조건")
    try:
        analyze([])
        check("빈 입력은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("빈 입력은 예외", True)

    try:
        analyze(_synthetic(5), top_fraction=0.0)
        check("top_fraction 0은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("top_fraction 0은 예외", True)

    try:
        analyze(_synthetic(5), top_fraction=1.5)
        check("top_fraction 1 초과는 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("top_fraction 1 초과는 예외", True)

    single = analyze(_synthetic(1))
    check("1개 입력: 스카이라인 1개", len(single.skyline) == 1)
    check("1개 입력: 배제 0개", len(single.excluded) == 0)
    check("1개 입력: top_k 1", single.top_k == 1, str(single.top_k))

    full = analyze(_synthetic(10), top_fraction=1.0)
    check("top_fraction 1.0이면 배제 0개", len(full.excluded) == 0)

    print()
    print("직렬화")
    try:
        json.dumps(result.to_dict(), ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))
    check(
        "to_dict 에 요약 포함",
        "summary" in result.to_dict() and result.to_dict()["candidate_count"] > 0,
    )

    print()
    print("무작위 40개 결과")
    print(f"    {result.summary}")
    print(f"    {result.verdict}")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
