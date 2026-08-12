from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataset import DISPLAY_NAMES  # noqa: F401  (산출물 meta 로 재수출)
from scoring.weighted import (
    MISSING_PERCENTILE,
    ScoredDistrict,
    assign_ranks,
    contribution_shares,  # noqa: F401  (v1과 같은 구현을 재수출)
    percentiles,
)

MODEL_VERSION = "risk-v2"

# ---- 발생확률 축: "결함이 있을 확률". 합이 1.0이어야 합니다.
#
# 2차 개정(2026-08-03). 포아송 회귀 판정에 따라 재배분했습니다 — 근거와 규칙
# 발동 내역은 가중치 사전 등록 문서 '2차 개정'에 있습니다. 여기서 값을 고치면
# 그 문서의 변경 이력에도 사유를 적어야 합니다.
OCCURRENCE_WEIGHTS: dict[str, float] = {
    "combined_sewer_ratio": 0.35,     # 회귀에서 가장 강함 (z=10.10). 우천시 유량 급변
    "aged_ratio_25y": 0.25,           # 실측 노후율 (유일하게 추정 아님). z=4.30
    "subsidence_per_km": 0.20,        # 실측 사고 이력(원인 무관). 3차 개정에서 정의 교체
    "concrete_ratio": 0.15,           # 부호가 반대로 나왔으나 탐지 편향으로 판단해 유지
    "heavy_rain_zone_ratio": 0.05,    # 지자체 지정 집중강우 중점관리구역 비율
    # unconnected_ratio 는 규칙 2에 따라 제거 (z=-1.58, p=0.1135)
}

# ---- 영향도 축: "터졌을 때 피해가 얼마나 큰가".
# 3차 개정에서 인구를 추가했습니다 — 관 굵기만으로는 피해를 입는 사람 수가
# 빠집니다. 어느 쪽이 더 중요한지 근거가 없어 단순 평균합니다.
IMPACT_FEATURES: list[str] = ["large_diameter_ratio", "population"]

# 영향배율 = IMPACT_FLOOR + IMPACT_SPAN × mean(pct)  →  0.5 ~ 1.5
IMPACT_FLOOR = 0.5
IMPACT_SPAN = 1.0

# ---- 강수 트리거 (3차 개정에서 규칙 등록)
# triggered_score = score × (1.0 + TRIGGER_SPAN × rainfall_pct)
# **등급·순위는 강수 미반영 score 기준을 유지합니다.** 최근 강수는 시점 의존이라
# 기본 점수에 곱하면 점검 계획이 매달 재편되고 batch_id 가 수집마다 바뀝니다.
TRIGGER_SPAN = 0.2

# 중점관리대상(국토부 지정) 배율은 **제거했습니다** (2026-08-03).
#
# 실키로 조회해 보니 전국 등록이 5건뿐이고 `facilNm` 이 지역명이 아니라
# 시설물명('오수관', '가스관(평택LT~부곡)')이어서 시군구에 붙일 근거가 없습니다.
# 배율이 항상 1.0 인 곱셈 항이 남아 있어 걷어냈습니다. 중점관리 정보는
# 하수도통계의 집중강우 중점관리구역 비율(`heavy_rain_zone_ratio`)로 대체합니다.
# 되살릴 근거가 생기면 사전 등록 이력에 사유를 먼저 적으세요.

# 지표 한글명은 dataset.py 가 단일 출처입니다. 여기서 재수출해 산출물 meta 로
# 나갑니다 (프론트가 한글명을 하드코딩하지 않게).

# v1에서 쓰다가 제거한 지표. 실수로 되살리는 것을 막기 위해 남겨 둡니다.
REMOVED_FEATURES = {
    "pipe_mean_age_years": "실측 노후율과 r=0.941: 거의 같은 것을 잼",
    "facility_avg_age_years": "대리의 대리. 실측 노후율이 있으면 불필요",
}


def impact_multiplier(percentile: float) -> float:
    return IMPACT_FLOOR + IMPACT_SPAN * percentile


def trigger_multiplier(rainfall_percentile: float | None) -> float:
    """최근 강수 백분위를 트리거 배율로 옮깁니다 (1.0 ~ 1.2).

    강수 관측이 없으면 1.0 입니다 — 결측을 중간값으로 패딩하면 관측이 없는
    구역이 이유 없이 올라갑니다.
    """
    if rainfall_percentile is None:
        return 1.0
    return 1.0 + TRIGGER_SPAN * rainfall_percentile


def score_districts(
    districts: list[dict], weights: dict[str, float] | None = None
) -> list[ScoredDistrict]:
    if not districts:
        return []

    if weights is None:
        weights = OCCURRENCE_WEIGHTS
    features = list(weights)

    # 지표별로 전국 백분위를 한 번에 계산합니다. 백분위는 전체 분포가 필요하므로
    # 구역별 루프 안에서는 계산할 수 없습니다.
    percentile_by_feature = {
        name: percentiles([row.get(name) for row in districts]) for name in features
    }
    # 영향도는 지표가 둘 이상이라 각각 백분위를 낸 뒤 평균합니다.
    impact_percentile_by_feature = {
        name: percentiles([row.get(name) for row in districts])
        for name in IMPACT_FEATURES
    }
    rainfall_percentiles = percentiles(
        [row.get("rainfall_30d_mm") for row in districts]
    )
    # 전국 평균을 함께 실어야 "381mm"가 많은 건지 적은 건지 알 수 있습니다.
    # 관측된 구역만으로 평균을 냅니다 (결측을 0으로 세면 평균이 내려갑니다).
    observed_rainfall = [
        float(row["rainfall_30d_mm"])
        for row in districts
        if row.get("rainfall_30d_mm") is not None
    ]
    rainfall_mean = (
        round(sum(observed_rainfall) / len(observed_rainfall), 1)
        if observed_rainfall
        else None
    )

    scored: list[ScoredDistrict] = []
    for index, row in enumerate(districts):
        impact_pcts = {
            name: impact_percentile_by_feature[name][index] for name in IMPACT_FEATURES
        }
        impact_pct = sum(impact_pcts.values()) / len(impact_pcts)
        multiplier = impact_multiplier(impact_pct)

        contributions: dict[str, float] = {}
        occurrence = 0.0
        missing: list[str] = []
        detail: dict = {}

        for name in features:
            percentile = percentile_by_feature[name][index]
            raw = row.get(name)
            if raw is None:
                missing.append(name)

            weight = weights[name]
            occurrence += weight * percentile
            contributions[name] = round(weight * percentile * multiplier, 6)

            detail[name] = raw
            detail[f"{name}_pct"] = round(percentile, 6)

        # `round(base, 6)`을 붙이지 마세요. 프론트 기여도 막대의 합이 base_risk와
        #    정확히 같아야 하므로 구성으로 보장합니다 (이 버그가 2회 났습니다 -
        #    지표 4개에서는 우연히 맞고 5개가 되면 어긋납니다).
        base = sum(contributions.values())

        for name in IMPACT_FEATURES:
            if row.get(name) is None:
                missing.append(name)

        rainfall_pct = (
            rainfall_percentiles[index]
            if row.get("rainfall_30d_mm") is not None
            else None
        )
        trigger = trigger_multiplier(rainfall_pct)
        base_score = base

        for name in IMPACT_FEATURES:
            detail[name] = row.get(name)
            detail[f"{name}_pct"] = round(impact_pcts[name], 6)

        detail.update(
            {
                # 영향도 지표가 여러 개라 평균 백분위도 함께 싣습니다.
                "impact_pct": round(impact_pct, 6),
                "occurrence_probability": round(occurrence, 6),
                "impact_multiplier": round(multiplier, 6),
                "base_risk": base,
                # 강수는 **별도 트리거**입니다. 등급·순위는 score 기준이고
                # triggered_score 는 참고용입니다 (3차 개정 참조).
                "rainfall_30d_mm": row.get("rainfall_30d_mm"),
                "rainfall_pct": (
                    round(rainfall_pct, 6) if rainfall_pct is not None else None
                ),
                "rainfall_national_mean_mm": rainfall_mean,
                "trigger_multiplier": round(trigger, 6),
                "triggered_score": round(base_score * trigger, 6),
            }
        )

        scored.append(
            ScoredDistrict(
                sgg_code=row["sgg_code"],
                full_name=row.get("full_name", ""),
                score=round(base_score, 6),
                rank=0,
                model_version=MODEL_VERSION,
                features=detail,
                contributions=contributions,
                missing_features=missing,
            )
        )

    assign_ranks(scored)
    return scored


def _self_test() -> int:
    passed = 0

    def check(label: str, condition: bool) -> None:
        nonlocal passed
        mark = "OK  " if condition else "FAIL"
        print(f"  [{mark}] {label}")
        if condition:
            passed += 1
        else:
            raise AssertionError(label)

    print("구조")
    check(
        "발생확률 가중치 합이 1.0",
        abs(sum(OCCURRENCE_WEIGHTS.values()) - 1.0) < 1e-9,
    )
    check("영향배율 최소가 0.5", abs(impact_multiplier(0.0) - 0.5) < 1e-9)
    check("영향배율 최대가 1.5", abs(impact_multiplier(1.0) - 1.5) < 1e-9)
    check(
        "영향도 결측이면 배율 1.0 (중립)",
        abs(impact_multiplier(MISSING_PERCENTILE) - 1.0) < 1e-9,
    )
    check(
        "제거한 지표가 가중치에 없음",
        not (set(REMOVED_FEATURES) & set(OCCURRENCE_WEIGHTS)),
    )
    check(
        "영향도 지표가 발생확률에 없음",
        not (set(IMPACT_FEATURES) & set(OCCURRENCE_WEIGHTS)),
    )

    print()
    print("산정")
    sample = [
        {
            "sgg_code": "A",
            "full_name": "낡고 대구경",
            "aged_ratio_25y": 0.80,
            "concrete_ratio": 0.90,
            "combined_sewer_ratio": 0.95,
            "unconnected_ratio": 0.10,
            "large_diameter_ratio": 0.50,
        },
        {
            "sgg_code": "B",
            "full_name": "낡고 소구경",
            "aged_ratio_25y": 0.80,
            "concrete_ratio": 0.90,
            "combined_sewer_ratio": 0.95,
            "unconnected_ratio": 0.10,
            "large_diameter_ratio": 0.05,
        },
        {
            "sgg_code": "C",
            "full_name": "새 관로",
            "aged_ratio_25y": 0.10,
            "concrete_ratio": 0.20,
            "combined_sewer_ratio": 0.15,
            "unconnected_ratio": 0.00,
            "large_diameter_ratio": 0.30,
        },
    ]
    scored = score_districts(sample)
    by_code = {item.sgg_code: item for item in scored}

    check("전부 산정됨", len(scored) == 3)
    check("순위가 1부터", scored[0].rank == 1)
    check("낡은 쪽이 새 관로보다 상위", by_code["A"].score > by_code["C"].score)
    check(
        "노후도가 같으면 대구경이 상위 (영향도가 작동)",
        by_code["A"].score > by_code["B"].score,
    )
    check(
        "영향도 최하위여도 위험도가 0이 아님",
        by_code["B"].score > 0,
    )
    check(
        "영향배율이 0.5~1.5 범위",
        all(
            0.5 - 1e-9 <= item.features["impact_multiplier"] <= 1.5 + 1e-9
            for item in scored
        ),
    )
    check(
        "발생확률이 0~1 범위",
        all(
            0.0 <= item.features["occurrence_probability"] <= 1.0 for item in scored
        ),
    )

    print()
    print("기여도")
    for item in scored:
        total = sum(item.contributions.values())
        check(
            f"{item.full_name}: 기여도 합 == base_risk (정확히)",
            total == item.features["base_risk"],
        )
    check(
        "기여도 비율 합이 1.0",
        all(
            abs(sum(contribution_shares(item).values()) - 1.0) < 1e-3
            for item in scored
        ),
    )
    check(
        "기여도 항목이 발생확률 지표와 일치",
        all(set(item.contributions) == set(OCCURRENCE_WEIGHTS) for item in scored),
    )

    print()
    print("결측")
    partial = [
        {"sgg_code": "P", "full_name": "노후율만", "aged_ratio_25y": 0.9},
        {"sgg_code": "Q", "full_name": "전부 결측"},
    ]
    scored_partial = score_districts(partial)
    p, q = scored_partial[0], scored_partial[1]
    check(
        "결측 지표가 missing_features에 기록",
        "concrete_ratio" in p.missing_features,
    )
    check(
        "영향도 결측도 기록",
        IMPACT_FEATURES[0] in p.missing_features,
    )
    check(
        "전부 결측이면 배율 1.0",
        abs(q.features["impact_multiplier"] - 1.0) < 1e-9,
    )
    check("결측이어도 점수가 계산됨", q.score > 0)
    check(
        "관측값이 있는 쪽이 상위",
        p.score >= q.score,
    )

    print()
    print("기여도 합")
    target = [
        {"sgg_code": "T", "full_name": "가", "aged_ratio_25y": 0.5},
        {"sgg_code": "U", "full_name": "나", "aged_ratio_25y": 0.5},
    ]
    st = {item.sgg_code: item for item in score_districts(target)}
    check(
        "기여도 합은 배율 적용 전(base_risk)과 일치",
        sum(st["T"].contributions.values()) == st["T"].features["base_risk"],
    )

    print()
    print("영향도 축이 여러 지표 (3차 개정)")
    check("영향도 지표가 2개 이상", len(IMPACT_FEATURES) >= 2)
    check("영향도 지표가 발생확률에 없음",
          not (set(IMPACT_FEATURES) & set(OCCURRENCE_WEIGHTS)))
    impact_rows = [
        # 대구경은 최상, 인구는 최하 → 평균 백분위는 중간
        {"sgg_code": "A", "full_name": "굵고 한적", "aged_ratio_25y": 0.5,
         "large_diameter_ratio": 1.0, "population": 10},
        {"sgg_code": "B", "full_name": "얇고 붐빔", "aged_ratio_25y": 0.5,
         "large_diameter_ratio": 0.0, "population": 1_000_000},
        {"sgg_code": "C", "full_name": "둘 다 최상", "aged_ratio_25y": 0.5,
         "large_diameter_ratio": 1.0, "population": 1_000_000},
    ]
    si = {item.sgg_code: item for item in score_districts(impact_rows)}
    check(
        "두 지표 모두 높은 구역이 가장 높은 배율",
        si["C"].features["impact_multiplier"]
        > max(si["A"].features["impact_multiplier"],
              si["B"].features["impact_multiplier"]),
    )
    check(
        "한쪽만 높으면 평균이라 중간",
        abs(si["A"].features["impact_pct"] - si["B"].features["impact_pct"]) < 1e-9,
    )
    check("지표별 백분위도 함께 실림",
          "population_pct" in si["A"].features
          and "large_diameter_ratio_pct" in si["A"].features)
    check("인구 결측이면 missing 에 기록",
          "population" in score_districts(
              [{"sgg_code": "Z", "aged_ratio_25y": 0.5,
                "large_diameter_ratio": 0.5}]
          )[0].missing_features)

    print()
    print("강수 트리거 (3차 개정) — 등급·순위에는 반영 안 됨")
    check("관측 없으면 1.0", trigger_multiplier(None) == 1.0)
    check("최저 백분위면 1.0", abs(trigger_multiplier(0.0) - 1.0) < 1e-12)
    check("최고 백분위면 1.2", abs(trigger_multiplier(1.0) - 1.2) < 1e-12)
    rain_rows = [
        {"sgg_code": "R1", "full_name": "많이", "aged_ratio_25y": 0.5,
         "rainfall_30d_mm": 500.0},
        {"sgg_code": "R2", "full_name": "적게", "aged_ratio_25y": 0.5,
         "rainfall_30d_mm": 10.0},
    ]
    sr = {item.sgg_code: item for item in score_districts(rain_rows)}
    check(
        "강수가 많으면 트리거 배율이 큼",
        sr["R1"].features["trigger_multiplier"]
        > sr["R2"].features["trigger_multiplier"],
    )
    check(
        "score 는 강수를 반영하지 않음 (두 구역 동일)",
        abs(sr["R1"].score - sr["R2"].score) < 1e-12,
        )
    check(
        "triggered_score = score × 배율",
        abs(
            sr["R1"].features["triggered_score"]
            - sr["R1"].score * sr["R1"].features["trigger_multiplier"]
        )
        < 1e-6,
    )
    no_rain = score_districts(
        [{"sgg_code": "N", "aged_ratio_25y": 0.5}]
    )[0]
    check("강수 결측이면 배율 1.0 · triggered = score",
          no_rain.features["trigger_multiplier"] == 1.0
          and no_rain.features["triggered_score"] == no_rain.score)

    print()
    print("경계")
    check("빈 입력이면 빈 결과", score_districts([]) == [])
    single = score_districts([{"sgg_code": "S", "full_name": "혼자",
                              "aged_ratio_25y": 0.9}])
    check("구역 1개도 동작", len(single) == 1 and single[0].rank == 1)
    tie = score_districts(
        [
            {"sgg_code": "X", "full_name": "동점1", "aged_ratio_25y": 0.5,
             "large_diameter_ratio": 0.3},
            {"sgg_code": "Y", "full_name": "동점2", "aged_ratio_25y": 0.5,
             "large_diameter_ratio": 0.3},
        ]
    )
    check("동점은 같은 순위", tie[0].rank == tie[1].rank == 1)
    check("모델 버전 기록", all(i.model_version == MODEL_VERSION for i in scored))
    check(
        "직렬화 가능",
        isinstance(scored[0].to_dict()["features"]["base_risk"], float),
    )

    print()
    print(f"결과: {passed}개 통과")
    return passed


if __name__ == "__main__":
    _self_test()
