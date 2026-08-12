from __future__ import annotations

import json
from dataclasses import dataclass

MODEL_VERSION = "weighted-v1"

# 전부 "값이 클수록 위험"인 방향으로 통일되어 있습니다.
WEIGHTS: dict[str, float] = {
    "combined_sewer_ratio": 0.35,
    "facility_avg_age_years": 0.20,
    "sewer_subsidence_per_km": 0.30,
    "unconnected_ratio": 0.15,
}

PRIORITY_TARGET_MULTIPLIER = 1.2

# 결측 시 부여할 백분위. 중앙값이므로 벌점도 가점도 아닙니다.
MISSING_PERCENTILE = 0.5


def percentiles(values: list[float | None]) -> list[float]:
    observed = sorted(value for value in values if value is not None)
    count = len(observed)

    if count == 0:
        return [MISSING_PERCENTILE] * len(values)

    # 값 → 백분위 사전 (동점 처리 포함)
    lookup: dict[float, float] = {}
    index = 0
    while index < count:
        value = observed[index]
        same = 1
        while index + same < count and observed[index + same] == value:
            same += 1
        lookup[value] = (index + 0.5 * same) / count
        index += same

    return [
        MISSING_PERCENTILE if value is None else lookup[value]
        for value in values
    ]


@dataclass
class ScoredDistrict:
    sgg_code: str
    full_name: str
    score: float
    rank: int
    model_version: str
    features: dict
    contributions: dict
    missing_features: list[str]

    def to_dict(self) -> dict:
        return {
            "sgg_code": self.sgg_code,
            "full_name": self.full_name,
            "score": round(self.score, 6),
            "rank": self.rank,
            "model_version": self.model_version,
            "features": self.features,
            "contributions": self.contributions,
            "missing_features": self.missing_features,
        }


def score_districts(
    districts: list[dict], weights: dict[str, float] | None = None
) -> list[ScoredDistrict]:
    if not districts:
        return []

    if weights is None:
        weights = WEIGHTS

    # 지표별 백분위를 한 번에 계산 (전국 대비 상대 위치)
    percentile_columns: dict[str, list[float]] = {}
    for name in weights:
        percentile_columns[name] = percentiles(
            [district.get(name) for district in districts]
        )

    scored: list[ScoredDistrict] = []

    for index, district in enumerate(districts):
        features: dict = {}
        contributions: dict = {}
        missing: list[str] = []

        for name, weight in weights.items():
            raw = district.get(name)
            percentile = percentile_columns[name][index]

            features[name] = raw
            features[f"{name}_pct"] = round(percentile, 6)
            contributions[name] = round(weight * percentile, 6)

            if raw is None:
                missing.append(name)

        # `round(base, 6)`을 붙이지 마세요: risk.py와 같은 이유입니다.
        base = sum(contributions.values())

        is_target = bool(district.get("is_priority_target"))
        multiplier = PRIORITY_TARGET_MULTIPLIER if is_target else 1.0

        features["is_priority_target"] = is_target
        features["base_risk"] = base
        features["priority_target_multiplier"] = multiplier

        # 강수량 동적 트리거 자리: 범위 제외이므로 비워둡니다.
        features["rainfall_30d_mm"] = None
        features["rainfall_pct"] = None
        features["rainfall_is_approximate"] = None
        features["trigger_multiplier"] = 1.0

        scored.append(
            ScoredDistrict(
                sgg_code=district["sgg_code"],
                full_name=district.get("full_name", district["sgg_code"]),
                score=base * multiplier,
                rank=0,
                model_version=MODEL_VERSION,
                features=features,
                contributions=contributions,
                missing_features=missing,
            )
        )

    assign_ranks(scored)
    return scored


def assign_ranks(scored: list[ScoredDistrict]) -> None:
    scored.sort(key=lambda item: (-item.score, item.sgg_code))

    previous_score: float | None = None
    previous_rank = 0

    for position, item in enumerate(scored, start=1):
        if previous_score is not None and abs(item.score - previous_score) < 1e-12:
            item.rank = previous_rank
        else:
            item.rank = position
            previous_rank = position
            previous_score = item.score


def contribution_shares(item: ScoredDistrict) -> dict[str, float]:
    total = sum(item.contributions.values())
    if total <= 0:
        return {name: 0.0 for name in item.contributions}
    return {
        name: round(value / total, 4)
        for name, value in item.contributions.items()
    }


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

    # --- 백분위 기본 동작
    print("백분위")
    result = percentiles([1.0, 2.0, 3.0, 4.0])
    check("단조 증가", result == sorted(result) and result[0] < result[-1], str(result))
    check("범위 (0,1)", all(0 < value < 1 for value in result), str(result))

    tied = percentiles([5.0, 5.0, 1.0, 9.0])
    check("동점은 같은 백분위", tied[0] == tied[1], str(tied))

    with_missing = percentiles([1.0, None, 3.0])
    check(
        "결측은 중앙값 0.5",
        with_missing[1] == MISSING_PERCENTILE,
        str(with_missing),
    )
    check(
        "결측이 다른 값의 백분위를 왜곡하지 않음",
        with_missing[0] == 0.25 and with_missing[2] == 0.75,
        str(with_missing),
    )

    all_missing = percentiles([None, None])
    check("전부 결측이면 전부 0.5", all_missing == [0.5, 0.5], str(all_missing))

    # --- 점수 산정
    print()
    print("점수 산정")
    sample = [
        {
            # 모든 지표 최악 → 1위여야 함
            "sgg_code": "29140",
            "full_name": "광주광역시 서구",
            "combined_sewer_ratio": 0.90,
            "facility_avg_age_years": 35.0,
            "sewer_subsidence_per_km": 0.50,
            "unconnected_ratio": 0.30,
        },
        {
            "sgg_code": "29110",
            "full_name": "광주광역시 동구",
            "combined_sewer_ratio": 0.50,
            "facility_avg_age_years": 20.0,
            "sewer_subsidence_per_km": 0.20,
            "unconnected_ratio": 0.15,
        },
        {
            # 모든 지표 최선 → 최하위여야 함
            "sgg_code": "11110",
            "full_name": "서울특별시 종로구",
            "combined_sewer_ratio": 0.10,
            "facility_avg_age_years": 5.0,
            "sewer_subsidence_per_km": 0.01,
            "unconnected_ratio": 0.02,
        },
    ]

    scored = score_districts(sample)
    by_code = {item.sgg_code: item for item in scored}

    check("전체 산정됨", len(scored) == 3, str(len(scored)))
    check("최악 지표가 1위", by_code["29140"].rank == 1, f"rank={by_code['29140'].rank}")
    check("최선 지표가 3위", by_code["11110"].rank == 3, f"rank={by_code['11110'].rank}")
    check(
        "점수 내림차순 정렬",
        [item.rank for item in scored] == [1, 2, 3],
        str([item.rank for item in scored]),
    )

    worst = by_code["29140"]
    check(
        "기여도 합 = base_risk",
        abs(sum(worst.contributions.values()) - worst.features["base_risk"]) < 1e-9,
        f"{sum(worst.contributions.values())} vs {worst.features['base_risk']}",
    )
    check(
        "가중치 합이 1이므로 base_risk ≤ 1",
        0 < worst.features["base_risk"] <= 1.0,
        str(worst.features["base_risk"]),
    )
    check(
        "기여도 비율 합 = 1",
        abs(sum(contribution_shares(worst).values()) - 1.0) < 1e-6,
        str(contribution_shares(worst)),
    )
    check(
        "합류식(0.35)이 최악 구역에서 최대 기여",
        max(worst.contributions, key=worst.contributions.get) == "combined_sewer_ratio",
        str(worst.contributions),
    )

    # --- 중점관리대상 가중
    print()
    print("중점관리대상 가중")
    pair = [
        dict(sample[1], sgg_code="A", full_name="A", is_priority_target=False),
        dict(sample[1], sgg_code="B", full_name="B", is_priority_target=True),
    ]
    pair_scored = {item.sgg_code: item for item in score_districts(pair)}
    check(
        "동일 지표라도 중점관리대상이 상위",
        pair_scored["B"].score > pair_scored["A"].score,
        f"A={pair_scored['A'].score} B={pair_scored['B'].score}",
    )
    check(
        "배수가 정확히 1.2",
        abs(pair_scored["B"].score / pair_scored["A"].score - 1.2) < 1e-9,
        str(pair_scored["B"].score / pair_scored["A"].score),
    )

    # --- 결측 처리
    print()
    print("결측 처리")
    with_none = [
        dict(sample[0], sgg_code="X", full_name="X", combined_sewer_ratio=None),
        dict(sample[2], sgg_code="Y", full_name="Y"),
    ]
    none_scored = {item.sgg_code: item for item in score_districts(with_none)}
    check(
        "결측 항목이 missing_features에 기록",
        none_scored["X"].missing_features == ["combined_sewer_ratio"],
        str(none_scored["X"].missing_features),
    )
    check(
        "결측이어도 점수 산출됨",
        none_scored["X"].score > 0,
        str(none_scored["X"].score),
    )
    check(
        "다른 지표가 최악이면 결측에도 여전히 상위",
        none_scored["X"].rank == 1,
        f"rank={none_scored['X'].rank}",
    )

    # --- 동점
    print()
    print("동점 처리")
    identical = [
        dict(sample[1], sgg_code="P", full_name="P"),
        dict(sample[1], sgg_code="Q", full_name="Q"),
    ]
    tie_scored = score_districts(identical)
    check(
        "완전 동일하면 같은 순위",
        tie_scored[0].rank == tie_scored[1].rank == 1,
        str([item.rank for item in tie_scored]),
    )

    # --- 경계 조건
    print()
    print("경계 조건")
    check("빈 입력", score_districts([]) == [], "")
    single = score_districts([dict(sample[0], sgg_code="Z", full_name="Z")])
    check("1개 입력은 1위", single[0].rank == 1, str(single[0].rank))
    check(
        "1개 입력의 백분위는 0.5",
        single[0].features["combined_sewer_ratio_pct"] == 0.5,
        str(single[0].features["combined_sewer_ratio_pct"]),
    )

    # --- 직렬화
    print()
    print("직렬화")
    try:
        json.dumps([item.to_dict() for item in scored], ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    check(
        "강수량 자리가 비어 있음 (범위 제외)",
        worst.features["rainfall_30d_mm"] is None
        and worst.features["trigger_multiplier"] == 1.0,
        str(worst.features["rainfall_30d_mm"]),
    )

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
