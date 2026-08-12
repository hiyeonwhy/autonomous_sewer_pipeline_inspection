from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math
from dataclasses import dataclass, field

from scoring.glm import GlmResult, auc, fit_glm, predict  # noqa: E402
from scoring.linalg import standardize  # noqa: E402
from scoring.objective_weights import pearson  # noqa: E402

Vector = list[float]

# 상관이 이보다 높으면 계수를 개별적으로 식별할 수 없습니다. 모형이 조용히
# 효과를 두 지표에 임의 배분하므로 반드시 경고해야 합니다.
COLLINEARITY_CRITICAL = 0.95

# 이 정도만 되어도 계수 표준오차가 부풀어 해석이 불안정해집니다.
COLLINEARITY_WARNING = 0.80

# 우리 지표는 전부 "값이 크면 위험"입니다. 계수가 음수로 나오면 가정과 반대이므로
# 조용히 넘기지 않고 경고로 드러냅니다.
EXPECTED_SIGN = 1.0

# 5% 유의수준. p 로 판정합니다 (|z| > 1.96 과 동등하지만 경고문에 p 를 실을 수 있음).
SIGNIFICANCE_THRESHOLD = 1.96
SIGNIFICANCE_ALPHA = 0.05


@dataclass
class PriorityModel:
    glm: GlmResult
    feature_names: list[str]
    means: list[float]
    deviations: list[float]
    exposure_name: str | None
    derived_weights: dict[str, float]
    signed_effects: dict[str, float]
    # 결측 대체에 쓴 학습 시점 중앙값. 채점 때 재사용해야 학습/채점이 같은
    # 전처리를 탑니다 — 채점 대상에서 다시 계산하면 train/serve 가 어긋납니다.
    fill_values: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [self.glm.summary(), ""]

        lines.append("  데이터가 도출한 가중치 (|계수| 비율)")
        for name, weight in sorted(
            self.derived_weights.items(), key=lambda pair: -pair[1]
        ):
            effect = self.signed_effects[name]
            arrow = "↑위험" if effect > 0 else "↓위험(가정과 반대)"
            bar = "█" * int(weight * 50)
            lines.append(f"    {name:26} {weight:.4f} {arrow:18} {bar}")

        if self.warnings:
            lines.append("")
            lines.append("  [!] 경고")
            for warning in self.warnings:
                lines.append(f"    - {warning}")

        return "\n".join(lines)


def _feature_matrix(
    districts: list[dict],
    feature_names: list[str],
    fill_values: list[float] | None = None,
) -> tuple[list[Vector], list[str], list[float]]:
    """지표 행렬과 결측 대체 기록, 사용한 채움값을 돌려줍니다.

    fill_values 를 주면 (채점 경로) 그 값으로 채우고, 없으면 (학습 경로)
    이 데이터의 중앙값을 계산해 씁니다.
    """
    columns: list[Vector] = []
    notes: list[str] = []
    fills: list[float] = []

    for index, name in enumerate(feature_names):
        raw = [district.get(name) for district in districts]

        if fill_values is not None:
            median = fill_values[index]
        else:
            observed = sorted(value for value in raw if value is not None)
            if not observed:
                raise ValueError(f"지표 {name!r}에 관측값이 없습니다.")

            middle = len(observed) // 2
            median = (
                observed[middle]
                if len(observed) % 2 == 1
                else (observed[middle - 1] + observed[middle]) / 2.0
            )

        missing = sum(1 for value in raw if value is None)
        if missing:
            notes.append(f"{name}: 결측 {missing}개를 중앙값 {median:.4g}로 대체")

        fills.append(median)
        columns.append([median if value is None else float(value) for value in raw])

    return columns, notes, fills


def fit_priority_model(
    districts: list[dict],
    feature_names: list[str],
    outcome_key: str,
    family: str = "binomial",
    exposure_key: str | None = None,
) -> PriorityModel:
    if not districts:
        raise ValueError("districts가 비어 있습니다.")

    warnings: list[str] = []

    columns, notes, fills = _feature_matrix(districts, feature_names)
    warnings.extend(notes)

    # 다중공선성 점검. 상관이 높으면 계수가 개별 식별되지 않고 모형이 효과를
    # 임의로 쪼개므로 가중치 해석 자체가 무의미해집니다.
    for left in range(len(feature_names)):
        for right in range(left + 1, len(feature_names)):
            correlation = pearson(columns[left], columns[right])
            pair = f"{feature_names[left]} ↔ {feature_names[right]}"

            if abs(correlation) >= COLLINEARITY_CRITICAL:
                warnings.append(
                    f"{pair}: 상관 {correlation:+.3f}: 거의 같은 지표입니다. "
                    "계수를 개별 식별할 수 없어 가중치 해석이 무의미합니다. "
                    "하나를 제거하세요."
                )
            elif abs(correlation) >= COLLINEARITY_WARNING:
                warnings.append(
                    f"{pair}: 상관 {correlation:+.3f}: 중복이 큽니다. "
                    "계수 표준오차가 부풀어 가중치가 불안정할 수 있습니다."
                )

    # 표준화해야 계수 크기가 상대적 영향력이 되어 가중치로 환산됩니다.
    scaled, means, deviations = standardize(columns)

    rows = len(districts)
    design = [
        [1.0] + [scaled[column][row] for column in range(len(feature_names))]
        for row in range(rows)
    ]

    targets: Vector = []
    for district in districts:
        value = district.get(outcome_key)
        if value is None:
            raise ValueError(f"정답 {outcome_key!r}에 결측이 있습니다: {district.get('sgg_code')}")
        targets.append(float(value))

    offset: Vector | None = None
    if exposure_key is not None:
        offset = []
        for district in districts:
            exposure = district.get(exposure_key)
            if exposure is None or exposure <= 0:
                # 노출량이 없으면 중립값(1)으로 둡니다: log(1)=0
                offset.append(0.0)
                warnings.append(
                    f"{district.get('sgg_code')}: 노출량 {exposure_key!r}가 없어 offset 0으로 처리"
                )
            else:
                offset.append(math.log(float(exposure)))

    if family == "binomial":
        positives = sum(1 for target in targets if target > 0.5)
        if positives == 0 or positives == rows:
            raise ValueError(
                f"정답이 한 쪽으로만 몰려 있습니다 (양성 {positives}/{rows}). "
                "로지스틱 회귀를 적합할 수 없습니다."
            )
        # 사건이 너무 적으면 계수가 불안정합니다 (EPV 규칙: 변수당 10건)
        events_per_variable = min(positives, rows - positives) / len(feature_names)
        if events_per_variable < 10:
            warnings.append(
                f"변수당 사건 수 {events_per_variable:.1f}건 (권장 10건 이상). "
                "계수 신뢰구간이 넓을 수 있습니다."
            )

    result = fit_glm(
        design,
        targets,
        family=family,
        offset=offset,
        feature_names=["절편"] + list(feature_names),
    )

    if not result.converged:
        warnings.append("IRLS가 수렴하지 않았습니다. 계수를 신뢰하지 마세요.")

    # 절편을 제외한 계수가 가중치의 원천입니다.
    slopes = result.coefficients[1:]
    errors = result.standard_errors[1:]

    signed_effects = dict(zip(feature_names, slopes))

    total = sum(abs(value) for value in slopes)
    if total <= 0:
        derived = {name: 0.0 for name in feature_names}
        warnings.append("모든 계수가 0입니다. 설명력이 없습니다.")
    else:
        derived = {
            name: abs(value) / total
            for name, value in zip(feature_names, slopes)
        }

    # 부호·유의성 점검
    for name, slope, error in zip(feature_names, slopes, errors):
        z = slope / error if error > 0 else 0.0
        p_value = math.erfc(abs(z) / math.sqrt(2.0))

        if slope * EXPECTED_SIGN < 0 and p_value < SIGNIFICANCE_ALPHA:
            warnings.append(
                f"{name}: 계수가 음수({slope:.3f}, z={z:.2f}, p={p_value:.4f})입니다. "
                "'값이 크면 위험'이라는 가정과 반대입니다: 지표를 재검토하세요."
            )
        elif p_value >= SIGNIFICANCE_ALPHA:
            warnings.append(
                f"{name}: 통계적으로 유의하지 않습니다 (z={z:.2f}, p={p_value:.4f}). "
                "이 지표는 침하를 설명하지 못합니다."
            )

    if result.mcfadden_r2 < 0.05:
        warnings.append(
            f"McFadden R²가 {result.mcfadden_r2:.4f}로 매우 낮습니다. "
            "피처가 침하를 거의 설명하지 못합니다."
        )

    return PriorityModel(
        glm=result,
        feature_names=list(feature_names),
        means=means,
        deviations=deviations,
        exposure_name=exposure_key,
        derived_weights=derived,
        signed_effects=signed_effects,
        fill_values=fills,
        warnings=warnings,
    )


@dataclass
class ModelScore:
    sgg_code: str
    full_name: str
    score: float
    rank: int
    contributions: dict[str, float]
    contribution_shares: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "sgg_code": self.sgg_code,
            "full_name": self.full_name,
            "score": round(self.score, 6),
            "rank": self.rank,
            "contributions": {
                name: round(value, 6) for name, value in self.contributions.items()
            },
            "contribution_shares": {
                name: round(value, 4)
                for name, value in self.contribution_shares.items()
            },
        }


def score_with_model(
    model: PriorityModel,
    districts: list[dict],
) -> list[ModelScore]:
    columns, _, _ = _feature_matrix(
        districts, model.feature_names, model.fill_values or None
    )

    rows = len(districts)
    scaled = [
        [
            (columns[index][row] - model.means[index]) / model.deviations[index]
            for index in range(len(model.feature_names))
        ]
        for row in range(rows)
    ]

    design = [[1.0] + row for row in scaled]

    offset: Vector | None = None
    if model.exposure_name is not None:
        offset = []
        for district in districts:
            exposure = district.get(model.exposure_name)
            offset.append(
                math.log(float(exposure)) if exposure and exposure > 0 else 0.0
            )

    predictions = predict(model.glm, design, offset)
    slopes = model.glm.coefficients[1:]

    scored: list[ModelScore] = []
    for row, district in enumerate(districts):
        contributions = {
            name: slopes[index] * scaled[row][index]
            for index, name in enumerate(model.feature_names)
        }

        magnitude = sum(abs(value) for value in contributions.values())
        shares = (
            {name: abs(value) / magnitude for name, value in contributions.items()}
            if magnitude > 0
            else {name: 0.0 for name in contributions}
        )

        scored.append(
            ModelScore(
                sgg_code=district["sgg_code"],
                full_name=district.get("full_name", district["sgg_code"]),
                score=predictions[row],
                rank=0,
                contributions=contributions,
                contribution_shares=shares,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.sgg_code))

    previous: float | None = None
    previous_rank = 0
    for position, item in enumerate(scored, start=1):
        if previous is not None and abs(item.score - previous) < 1e-12:
            item.rank = previous_rank
        else:
            item.rank = position
            previous_rank = position
            previous = item.score

    return scored


@dataclass
class Validation:
    observations: int
    positives: int
    auc: float
    capture_rate: float
    capture_top_n: int
    baseline_rate: float
    lift: float

    def summary(self) -> str:
        return (
            f"표본 외 검증: 관측 {self.observations}개(양성 {self.positives}), "
            f"AUC {self.auc:.3f}, "
            f"상위 {self.capture_top_n}개가 실제 발생의 {self.capture_rate:.1%} 포착 "
            f"(무작위 기대 {self.baseline_rate:.1%}, 향상도 {self.lift:.2f}배)"
        )


def validate(
    scored: list[ModelScore],
    actual: dict[str, float],
    top_fraction: float = 0.2,
) -> Validation:
    usable = [item for item in scored if item.sgg_code in actual]
    if not usable:
        raise ValueError("예측과 실제의 시군구 코드가 하나도 겹치지 않습니다.")

    targets = [1.0 if actual[item.sgg_code] > 0 else 0.0 for item in usable]
    scores = [item.score for item in usable]

    total_events = sum(actual[item.sgg_code] for item in usable)
    positives = int(sum(targets))

    top_n = max(1, int(len(usable) * top_fraction))
    ordered = sorted(usable, key=lambda item: -item.score)
    captured = sum(actual[item.sgg_code] for item in ordered[:top_n])

    capture_rate = captured / total_events if total_events > 0 else 0.0
    baseline = top_n / len(usable)

    return Validation(
        observations=len(usable),
        positives=positives,
        auc=auc(targets, scores),
        capture_rate=capture_rate,
        capture_top_n=top_n,
        baseline_rate=baseline,
        lift=capture_rate / baseline if baseline > 0 else 0.0,
    )


# ---------------------------------------------------------------- 자체 검증


class _Rng:
    def __init__(self, seed: int = 20260730) -> None:
        self.state = seed

    def uniform(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % (2**31)
        return self.state / (2**31)


def _make_districts(count: int, seed: int, true_weights: dict[str, float]) -> list[dict]:
    rng = _Rng(seed)
    districts = []

    for index in range(count):
        combined = rng.uniform()
        age = rng.uniform() * 40.0
        unconnected = rng.uniform() * 0.3
        length = 20.0 + rng.uniform() * 300.0

        # 표준화 후 스케일을 맞추기 위해 대략 정규화한 값으로 선형결합
        logit = (
            -0.3
            + true_weights["combined_sewer_ratio"] * (combined - 0.5) * 4.0
            + true_weights["facility_avg_age_years"] * (age - 20.0) / 10.0
            + true_weights["unconnected_ratio"] * (unconnected - 0.15) * 12.0
        )
        probability = 1.0 / (1.0 + math.exp(-logit))

        districts.append(
            {
                "sgg_code": f"{10000 + index}",
                "full_name": f"테스트 {index}구",
                "combined_sewer_ratio": combined,
                "facility_avg_age_years": age,
                "unconnected_ratio": unconnected,
                "pipe_total_length": length,
                "future_subsidence": 1.0 if rng.uniform() < probability else 0.0,
                "_true_probability": probability,
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

    features = [
        "combined_sewer_ratio",
        "facility_avg_age_years",
        "unconnected_ratio",
    ]

    # 참 영향력: 합류식이 가장 크고, 미연결이 중간, 시설연령이 작음
    true_weights = {
        "combined_sewer_ratio": 1.0,
        "facility_avg_age_years": 0.25,
        "unconnected_ratio": 0.6,
    }

    print("모형 적합: 참 영향력 순서를 되찾는가 (표본 3000)")
    train = _make_districts(3000, 20260730, true_weights)
    model = fit_priority_model(train, features, "future_subsidence", "binomial")
    print("    " + model.summary().replace("\n", "\n    "))

    check("수렴함", model.glm.converged, "")
    check(
        "가중치 합 = 1",
        abs(sum(model.derived_weights.values()) - 1.0) < 1e-9,
        "",
    )
    check(
        "모든 계수가 양수 (가정과 일치)",
        all(value > 0 for value in model.signed_effects.values()),
        str(model.signed_effects),
    )

    ordered = sorted(model.derived_weights.items(), key=lambda pair: -pair[1])
    check(
        "영향력 순서 복원: 합류식 > 미연결 > 시설연령",
        [name for name, _ in ordered] == [
            "combined_sewer_ratio",
            "unconnected_ratio",
            "facility_avg_age_years",
        ],
        str(ordered),
    )

    print()
    print("점수 산출")
    scored = score_with_model(model, train)
    check("전체 산정", len(scored) == len(train), "")
    check("점수가 확률 범위", all(0 <= item.score <= 1 for item in scored), "")
    check("1위가 최고점", scored[0].rank == 1, "")
    check(
        "기여도 비율 합 = 1",
        abs(sum(scored[0].contribution_shares.values()) - 1.0) < 1e-6,
        "",
    )

    # 예측 확률이 참 확률과 상관이 높아야 함
    pairs = {item.sgg_code: item.score for item in scored}
    truths = {d["sgg_code"]: d["_true_probability"] for d in train}
    codes = sorted(pairs)
    mean_p = sum(pairs[c] for c in codes) / len(codes)
    mean_t = sum(truths[c] for c in codes) / len(codes)
    cov = sum((pairs[c] - mean_p) * (truths[c] - mean_t) for c in codes)
    var_p = sum((pairs[c] - mean_p) ** 2 for c in codes)
    var_t = sum((truths[c] - mean_t) ** 2 for c in codes)
    correlation = cov / math.sqrt(var_p * var_t)
    check(f"예측이 참 확률과 상관 {correlation:.3f} > 0.95", correlation > 0.95, "")

    print()
    print("표본 외 검증: 학습에 쓰지 않은 시군구")
    holdout = _make_districts(600, 999, true_weights)
    holdout_scored = score_with_model(model, holdout)
    actual = {d["sgg_code"]: d["future_subsidence"] for d in holdout}
    result = validate(holdout_scored, actual, top_fraction=0.2)
    print(f"    {result.summary()}")

    check(f"AUC {result.auc:.3f} > 0.7 (표본 외)", result.auc > 0.7, "")
    check(
        f"포착률 {result.capture_rate:.1%} > 무작위 {result.baseline_rate:.1%}",
        result.capture_rate > result.baseline_rate,
        "",
    )
    check(f"향상도 {result.lift:.2f} > 1.5", result.lift > 1.5, "")

    # 결측 대체가 학습 시점 중앙값을 쓰는지. 예전에는 채점 대상에서 중앙값을
    # 다시 계산해, 같은 구역이라도 함께 채점되는 집단에 따라 점수가 달라졌습니다.
    lone = dict(holdout[0])
    lone[features[0]] = None
    padded = dict(lone, **{features[0]: model.fill_values[0]})

    def score_of(rows: list[dict], code: str) -> float:
        return next(
            item.score for item in score_with_model(model, rows) if item.sgg_code == code
        )

    lone_score = score_of([lone, holdout[1]], lone["sgg_code"])
    padded_score = score_of([padded, holdout[1]], padded["sgg_code"])
    check(
        "결측 채움이 학습 중앙값 (채점 집단과 무관)",
        abs(lone_score - padded_score) < 1e-12,
        f"{lone_score} vs {padded_score}",
    )

    print()
    print("경고 탐지: 무관한 지표를 넣으면 잡아내는가")
    noisy = [dict(d) for d in train]
    rng = _Rng(555)
    for district in noisy:
        district["random_noise"] = rng.uniform()
    noisy_model = fit_priority_model(
        noisy, features + ["random_noise"], "future_subsidence", "binomial"
    )
    check(
        "무관 지표가 '유의하지 않음' 경고에 잡힘",
        any("random_noise" in w and "유의하지 않" in w for w in noisy_model.warnings),
        str(noisy_model.warnings),
    )
    check(
        "무관 지표의 가중치가 가장 작음",
        noisy_model.derived_weights["random_noise"]
        == min(noisy_model.derived_weights.values()),
        str(noisy_model.derived_weights),
    )

    print()
    print("경고 탐지: 다중공선성")
    # 완전 상관인 지표를 넣으면 계수 식별이 불가능하므로 경고해야 합니다.
    collinear = [dict(d) for d in train]
    for district in collinear:
        district["mirror"] = 1.0 - district["combined_sewer_ratio"]
    collinear_model = fit_priority_model(
        collinear, features + ["mirror"], "future_subsidence", "binomial"
    )
    check(
        "완전 상관이 '거의 같은 지표' 경고에 잡힘",
        any("거의 같은 지표" in w for w in collinear_model.warnings),
        str([w for w in collinear_model.warnings if "mirror" in w]),
    )

    print()
    print("경고 탐지: 부호가 반대인 지표 (독립적으로 위험을 낮추는 경우)")
    # 위험을 실제로 낮추는 지표를 독립적으로 생성합니다.
    # (다중공선이 아니어야 계수가 식별되고 음수로 나옵니다)
    rng3 = _Rng(4242)
    protective_rows = []
    for index in range(3000):
        combined = rng3.uniform()
        maintenance = rng3.uniform()          # 정비 투자 수준: 위험을 낮춤
        logit = -0.3 + 2.0 * (combined - 0.5) - 2.0 * (maintenance - 0.5)
        probability = 1.0 / (1.0 + math.exp(-logit))
        protective_rows.append(
            {
                "sgg_code": f"{30000 + index}",
                "full_name": f"P{index}",
                "combined_sewer_ratio": combined,
                "maintenance_level": maintenance,
                "future_subsidence": 1.0 if rng3.uniform() < probability else 0.0,
            }
        )

    protective_model = fit_priority_model(
        protective_rows,
        ["combined_sewer_ratio", "maintenance_level"],
        "future_subsidence",
        "binomial",
    )
    check(
        "정비 지표의 계수가 음수로 추정됨",
        protective_model.signed_effects["maintenance_level"] < 0,
        str(protective_model.signed_effects),
    )
    check(
        "음수 계수가 경고에 잡힘",
        any(
            "maintenance_level" in w and "음수" in w
            for w in protective_model.warnings
        ),
        str([w for w in protective_model.warnings if "maintenance" in w]),
    )
    check(
        "합류식 계수는 양수 유지",
        protective_model.signed_effects["combined_sewer_ratio"] > 0,
        "",
    )

    print()
    print("포아송 + 노출량(offset)")
    counted = [dict(d) for d in train]
    rng2 = _Rng(321)
    for district in counted:
        # 연장에 비례해 건수가 늘도록 생성
        rate = district["_true_probability"] * district["pipe_total_length"] / 100.0
        limit = math.exp(-rate)
        product = rng2.uniform()
        draw = 0
        while product > limit and draw < 200:
            draw += 1
            product *= rng2.uniform()
        district["subsidence_count"] = float(draw)

    poisson_model = fit_priority_model(
        counted, features, "subsidence_count", "poisson",
        exposure_key="pipe_total_length",
    )
    check("포아송 수렴", poisson_model.glm.converged, "")
    check(
        "포아송도 합류식이 최대 가중치",
        max(poisson_model.derived_weights, key=poisson_model.derived_weights.get)
        == "combined_sewer_ratio",
        str(poisson_model.derived_weights),
    )

    print()
    print("경계 조건")
    try:
        fit_priority_model([], features, "future_subsidence")
        check("빈 입력 예외", False, "")
    except ValueError:
        check("빈 입력 예외", True)

    one_sided = [dict(d, future_subsidence=1.0) for d in train[:100]]
    try:
        fit_priority_model(one_sided, features, "future_subsidence", "binomial")
        check("정답이 한쪽으로 몰리면 예외", False, "")
    except ValueError:
        check("정답이 한쪽으로 몰리면 예외", True)

    small = _make_districts(60, 42, true_weights)
    small_model = fit_priority_model(small, features, "future_subsidence", "binomial")
    check(
        "표본이 작으면 EPV 경고",
        any("변수당 사건" in w for w in small_model.warnings),
        str(small_model.warnings),
    )

    try:
        validate(scored, {"없는코드": 1.0})
        check("코드가 안 겹치면 예외", False, "")
    except ValueError:
        check("코드가 안 겹치면 예외", True)

    print()
    print("직렬화")
    import json

    try:
        json.dumps([item.to_dict() for item in scored[:5]], ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
