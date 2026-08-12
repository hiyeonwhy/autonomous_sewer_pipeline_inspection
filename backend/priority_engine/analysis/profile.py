from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import math
from dataclasses import dataclass, field

import dataset  # noqa: E402

# 이 값을 넘는 상관은 "같은 것을 재고 있을 가능성"으로 보고 보고서에 올립니다.
# 0.8 은 지표 중복 판정의 관례값이고, 실제로 이 기준으로 지표 2개를 제거했습니다.
HIGH_CORRELATION = 0.8

# 다중공선성 위험. glm.py 가 같은 기준으로 경고합니다.
CRITICAL_CORRELATION = 0.95

# MNAR 진단에서 "결측 집단과 관측 집단이 다르다"고 볼 표준화 차이(Cohen's d).
# 0.5 는 중간 효과크기의 관례값입니다.
MNAR_EFFECT_THRESHOLD = 0.5


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("빈 목록의 중앙값은 정의되지 않습니다.")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def quantile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("빈 목록의 분위수는 정의되지 않습니다.")
    if not 0 <= fraction <= 1:
        raise ValueError(f"fraction 은 0~1 이어야 합니다: {fraction}")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    # 선형 보간(numpy 기본과 같은 방식). 표본이 작아 방식 차이가 눈에 보입니다.
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stdev(values: list[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def skewness(values: list[float]) -> float:
    count = len(values)
    if count < 3:
        return 0.0
    mean = sum(values) / count
    m2 = sum((value - mean) ** 2 for value in values) / count
    m3 = sum((value - mean) ** 3 for value in values) / count
    if m2 < 1e-18:
        return 0.0
    return m3 / (m2**1.5)


def pearson(left: list[float], right: list[float]) -> float:
    count = len(left)
    if count != len(right):
        raise ValueError(f"길이가 다릅니다: {count} vs {len(right)}")
    if count < 2:
        return 0.0

    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right)
    )
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)

    # 상수 열은 상관이 정의되지 않습니다. 0으로 두어 "관계 없음"으로 보고합니다.
    if variance_left < 1e-18 or variance_right < 1e-18:
        return 0.0
    return covariance / math.sqrt(variance_left * variance_right)


@dataclass
class Stat:
    name: str
    label: str
    total: int
    count: int
    mean: float | None
    median: float | None
    stdev: float | None
    minimum: float | None
    q1: float | None
    q3: float | None
    maximum: float | None
    zero_share: float | None
    skew: float | None

    @property
    def missing(self) -> int:
        return self.total - self.count

    @property
    def coverage(self) -> float:
        return self.count / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        def rounded(value):
            return None if value is None else round(value, 6)

        return {
            "name": self.name,
            "label": self.label,
            "total": self.total,
            "count": self.count,
            "missing": self.missing,
            "coverage": round(self.coverage, 4),
            "mean": rounded(self.mean),
            "median": rounded(self.median),
            "stdev": rounded(self.stdev),
            "min": rounded(self.minimum),
            "q1": rounded(self.q1),
            "q3": rounded(self.q3),
            "max": rounded(self.maximum),
            "zero_share": rounded(self.zero_share),
            "skew": rounded(self.skew),
        }


def describe(name: str, rows: list[dict]) -> Stat:
    values = [row[name] for row in rows if row.get(name) is not None]
    if not values:
        return Stat(
            name=name,
            label=dataset.label(name),
            total=len(rows),
            count=0,
            mean=None,
            median=None,
            stdev=None,
            minimum=None,
            q1=None,
            q3=None,
            maximum=None,
            zero_share=None,
            skew=None,
        )

    return Stat(
        name=name,
        label=dataset.label(name),
        total=len(rows),
        count=len(values),
        mean=sum(values) / len(values),
        median=median(values),
        stdev=stdev(values),
        minimum=min(values),
        q1=quantile(values, 0.25),
        q3=quantile(values, 0.75),
        maximum=max(values),
        zero_share=sum(1 for value in values if value == 0) / len(values),
        skew=skewness(values),
    )


def profile(rows: list[dict], names: list[str] | None = None) -> list[Stat]:
    return [describe(name, rows) for name in (names or dataset.INDICATORS)]


def stats_table(stats: list[Stat]) -> str:
    header = (
        f"  {'지표':26} {'관측':>9} {'평균':>8} {'중앙':>8} {'표준편차':>8} "
        f"{'최소':>8} {'최대':>8} {'0비율':>7} {'왜도':>7}"
    )
    lines = [header, "  " + "-" * 100]

    for stat in stats:
        if stat.count == 0:
            lines.append(
                f"  {stat.name:26} {stat.count:4d}/{stat.total:<4d}"
                "        전부 결측"
            )
            continue
        lines.append(
            f"  {stat.name:26} {stat.count:4d}/{stat.total:<4d} "
            f"{stat.mean:8.3f} {stat.median:8.3f} {stat.stdev:8.3f} "
            f"{stat.minimum:8.3f} {stat.maximum:8.3f} "
            f"{stat.zero_share:6.1%} {stat.skew:7.2f}"
        )
    return "\n".join(lines)


@dataclass
class MnarCheck:
    target: str
    other: str
    missing_group_mean: float
    observed_group_mean: float
    effect_size: float
    missing_count: int
    observed_count: int

    @property
    def concerning(self) -> bool:
        return abs(self.effect_size) >= MNAR_EFFECT_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "other": self.other,
            "missing_group_mean": round(self.missing_group_mean, 6),
            "observed_group_mean": round(self.observed_group_mean, 6),
            "effect_size": round(self.effect_size, 4),
            "missing_count": self.missing_count,
            "observed_count": self.observed_count,
            "concerning": self.concerning,
        }


def mnar_checks(
    rows: list[dict], target: str, others: list[str]
) -> list[MnarCheck]:
    # 결측이 무작위인지(MAR) 위험한 곳에 몰렸는지(MNAR) 봅니다. target 이 결측인
    # 집단과 관측된 집단에서 *다른* 지표의 평균을 비교합니다. 결측 자체는 볼 수
    # 없으므로, 볼 수 있는 다른 지표로 두 집단이 같은 모집단인지 판정합니다.
    missing_rows = [row for row in rows if row.get(target) is None]
    observed_rows = [row for row in rows if row.get(target) is not None]
    if not missing_rows or not observed_rows:
        return []

    checks: list[MnarCheck] = []
    for other in others:
        if other == target:
            continue
        left = [row[other] for row in missing_rows if row.get(other) is not None]
        right = [row[other] for row in observed_rows if row.get(other) is not None]
        if len(left) < 2 or len(right) < 2:
            continue

        mean_left = sum(left) / len(left)
        mean_right = sum(right) / len(right)

        # 합동 표준편차 기준 표준화 차이(Cohen's d). 단위가 다른 지표를 같은
        # 척도로 비교하기 위한 것입니다.
        var_left = stdev(left) ** 2
        var_right = stdev(right) ** 2
        pooled = math.sqrt(
            ((len(left) - 1) * var_left + (len(right) - 1) * var_right)
            / max(len(left) + len(right) - 2, 1)
        )
        effect = 0.0 if pooled < 1e-12 else (mean_left - mean_right) / pooled

        checks.append(
            MnarCheck(
                target=target,
                other=other,
                missing_group_mean=mean_left,
                observed_group_mean=mean_right,
                effect_size=effect,
                missing_count=len(left),
                observed_count=len(right),
            )
        )

    return sorted(checks, key=lambda check: -abs(check.effect_size))


@dataclass
class MissingReport:
    total: int
    per_indicator: dict[str, int]
    fully_missing: list[str]
    partially_missing: list[str]
    complete_rows: int
    co_missing: dict[str, dict[str, int]] = field(default_factory=dict)
    mnar: dict[str, list[MnarCheck]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "per_indicator": self.per_indicator,
            "fully_missing": self.fully_missing,
            "partially_missing": self.partially_missing,
            "complete_rows": self.complete_rows,
            "co_missing": self.co_missing,
            "mnar": {
                target: [check.to_dict() for check in checks]
                for target, checks in self.mnar.items()
            },
        }

    @property
    def concerning(self) -> list[MnarCheck]:
        return [
            check
            for checks in self.mnar.values()
            for check in checks
            if check.concerning
        ]


def missing_report(rows: list[dict], names: list[str] | None = None) -> MissingReport:
    names = names or dataset.INDICATORS
    total = len(rows)

    per_indicator = {
        name: sum(1 for row in rows if row.get(name) is None) for name in names
    }
    fully = [name for name in names if per_indicator[name] == total]
    partial = [name for name in names if 0 < per_indicator[name] < total]

    # 함께 빠지는 지표 쌍. 같은 원본에서 오면 함께 빠지므로 결측 원인을 좁힙니다.
    co_missing: dict[str, dict[str, int]] = {}
    for name in partial:
        row_map: dict[str, int] = {}
        for other in partial:
            if other == name:
                continue
            row_map[other] = sum(
                1
                for row in rows
                if row.get(name) is None and row.get(other) is None
            )
        co_missing[name] = row_map

    # MNAR 진단은 부분 결측 지표에만 의미가 있습니다. 전부 결측이면 비교할
    # 두 집단이 만들어지지 않습니다.
    observable = [name for name in names if per_indicator[name] < total]
    mnar = {name: mnar_checks(rows, name, observable) for name in partial}

    return MissingReport(
        total=total,
        per_indicator=per_indicator,
        fully_missing=fully,
        partially_missing=partial,
        complete_rows=len(dataset.complete_rows(rows, names)),
        co_missing=co_missing,
        mnar=mnar,
    )


def missing_table(report: MissingReport) -> str:
    lines = [f"  {'지표':26} {'결측':>6} {'비율':>7}", "  " + "-" * 44]
    for name, count in report.per_indicator.items():
        share = count / report.total if report.total else 0.0
        note = ""
        if count == report.total:
            note = "  ← 전부 결측"
        elif count == 0:
            note = "  ← 완전"
        lines.append(f"  {name:26} {count:6d} {share:7.1%}{note}")

    lines.append("")
    lines.append(
        f"  {len(report.per_indicator)}개 지표 전부 관측된 시군구:"
        f" {report.complete_rows}/{report.total}"
    )

    if report.fully_missing:
        lines.append(f"  전부 결측 지표: {report.fully_missing}")
    return "\n".join(lines)


def mnar_table(report: MissingReport) -> str:
    if not report.partially_missing:
        return "  부분 결측 지표가 없어 MNAR 진단을 건너뜁니다."

    lines = [
        "  결측이 무작위인지 봅니다. 결측 집단과 관측 집단에서 *다른* 지표의",
        "  평균을 비교합니다. |d| >= 0.5 면 두 집단이 다른 모집단입니다.",
        "",
        f"  {'결측 지표':24} {'비교 지표':24} {'결측군':>9} {'관측군':>9} {'d':>7}",
        "  " + "-" * 78,
    ]

    for target in report.partially_missing:
        checks = report.mnar.get(target, [])
        if not checks:
            continue
        for check in checks:
            mark = "  ←" if check.concerning else ""
            lines.append(
                f"  {check.target:24} {check.other:24} "
                f"{check.missing_group_mean:9.3f} {check.observed_group_mean:9.3f} "
                f"{check.effect_size:7.2f}{mark}"
            )
        lines.append("")

    concerning = report.concerning
    if concerning:
        lines.append(f"  [!] 효과크기 0.5 이상 {len(concerning)}건. 결측이 무작위가 아닙니다.")
        lines.append("      중앙값 패딩이 이 집단을 체계적으로 잘못 평가합니다.")
    else:
        lines.append("  효과크기 0.5 이상이 없습니다. 결측이 대체로 무작위입니다.")
    return "\n".join(lines)


def correlation_matrix(
    rows: list[dict], names: list[str]
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, int]]]:
    # 쌍별 완전관측(pairwise complete)으로 계산합니다. 완전관측 행만 쓰면
    # 표본이 크게 줄고, 결측 패턴이 다른 지표 쌍의 상관을 볼 수 없습니다.
    # 대신 쌍마다 n 이 다르므로 함께 보고합니다.
    matrix: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}

    for left in names:
        matrix[left] = {}
        counts[left] = {}
        for right in names:
            pairs = [
                (row[left], row[right])
                for row in rows
                if row.get(left) is not None and row.get(right) is not None
            ]
            counts[left][right] = len(pairs)
            if len(pairs) < 2:
                matrix[left][right] = 0.0
                continue
            matrix[left][right] = pearson(
                [pair[0] for pair in pairs], [pair[1] for pair in pairs]
            )

    return matrix, counts


def high_pairs(
    matrix: dict[str, dict[str, float]],
    counts: dict[str, dict[str, int]],
    threshold: float = HIGH_CORRELATION,
) -> list[tuple[str, str, float, int]]:
    names = list(matrix)
    found = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            value = matrix[left][right]
            if abs(value) >= threshold:
                found.append((left, right, value, counts[left][right]))
    return sorted(found, key=lambda item: -abs(item[2]))


def correlation_table(
    matrix: dict[str, dict[str, float]], counts: dict[str, dict[str, int]]
) -> str:
    names = list(matrix)
    short = {name: name[:10] for name in names}

    lines = ["  " + " " * 26 + "".join(f"{short[name]:>11}" for name in names)]
    for left in names:
        cells = []
        for right in names:
            value = matrix[left][right]
            cells.append("      1.000" if left == right else f"{value:11.3f}")
        lines.append(f"  {left:26}" + "".join(cells))

    lines.append("")
    lines.append("  쌍별 완전관측 수 (n)")
    for left in names:
        pairs = [f"{right[:8]}={counts[left][right]}" for right in names if right != left]
        lines.append(f"    {left:26} " + "  ".join(pairs))

    return "\n".join(lines)


def sido_table(rows: list[dict], names: list[str], limit: int = 17) -> str:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("sido_name") or "?", []).append(row)

    lines = [
        f"  {'시도':14} {'구역':>4} " + "".join(f"{name[:9]:>11}" for name in names),
        "  " + "-" * (20 + 11 * len(names)),
    ]

    def sort_key(item):
        values = [
            value
            for value in (
                stat.mean for stat in profile(item[1], names) if stat.mean is not None
            )
        ]
        return -(sum(values) / len(values)) if values else 0.0

    for sido, members in sorted(groups.items(), key=sort_key)[:limit]:
        cells = []
        for name in names:
            stat = describe(name, members)
            cells.append("        N/A" if stat.mean is None else f"{stat.mean:11.3f}")
        lines.append(f"  {sido:14} {len(members):4d} " + "".join(cells))

    return "\n".join(lines)


def build_report(rows: list[dict], names: list[str] | None = None) -> dict:
    names = names or dataset.INDICATORS
    available = dataset.available_indicators(rows, names)
    matrix, counts = correlation_matrix(rows, available)
    missing = missing_report(rows, names)

    return {
        "district_count": len(rows),
        "indicators": names,
        "available_indicators": available,
        "stats": [stat.to_dict() for stat in profile(rows, names)],
        "missing": missing.to_dict(),
        "correlation": matrix,
        "correlation_n": counts,
        "high_pairs": [
            {"left": left, "right": right, "r": round(value, 6), "n": count}
            for left, right, value, count in high_pairs(matrix, counts)
        ],
    }


# ---------------------------------------------------------------- 자체 검증


def _rows(values: dict[str, list]) -> list[dict]:
    count = len(next(iter(values.values())))
    return [
        {
            "sgg_code": f"{10000 + index}",
            "full_name": f"T{index}",
            "sido_name": "테스트도",
            **{name: column[index] for name, column in values.items()},
        }
        for index in range(count)
    ]


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("기술통계 함수")
    check("중앙값 홀수", median([3.0, 1.0, 2.0]) == 2.0)
    check("중앙값 짝수", median([1.0, 2.0, 3.0, 4.0]) == 2.5)
    check("사분위 0.25", abs(quantile([1, 2, 3, 4, 5], 0.25) - 2.0) < 1e-12,
          str(quantile([1, 2, 3, 4, 5], 0.25)))
    check("사분위 보간", abs(quantile([0.0, 1.0], 0.5) - 0.5) < 1e-12)
    check("최소·최대 분위", quantile([1, 9], 0.0) == 1 and quantile([1, 9], 1.0) == 9)
    check("표준편차 (표본)", abs(stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) - 2.13809) < 1e-4,
          str(stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])))
    check("1개는 표준편차 0", stdev([5.0]) == 0.0)
    check("대칭 분포 왜도 0", abs(skewness([1.0, 2.0, 3.0, 4.0, 5.0])) < 1e-12,
          str(skewness([1.0, 2.0, 3.0, 4.0, 5.0])))
    check("우측 꼬리는 양의 왜도", skewness([1, 1, 1, 1, 10]) > 0)
    check("상수 열 왜도 0", skewness([2.0] * 5) == 0.0)

    print()
    print("상관")
    check("완전 양의 상관", abs(pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-12)
    check("완전 음의 상관", abs(pearson([1, 2, 3], [-1, -2, -3]) + 1.0) < 1e-12)
    check("상수 열은 0", pearson([1, 1, 1], [1, 2, 3]) == 0.0)
    try:
        pearson([1, 2], [1, 2, 3])
        check("길이 불일치는 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("길이 불일치는 예외", True)

    print()
    print("기술통계 - 결측 처리")
    rows = _rows({"a": [1.0, 2.0, None, 4.0], "b": [0.0, 0.0, 0.0, 1.0]})
    stat = describe("a", rows)
    check("관측 수가 결측을 뺀 값", stat.count == 3, str(stat.count))
    check("전체 수는 유지", stat.total == 4, str(stat.total))
    check("결측 수", stat.missing == 1, str(stat.missing))
    check("커버리지", abs(stat.coverage - 0.75) < 1e-12, str(stat.coverage))
    check("평균이 관측값만으로 계산", abs(stat.mean - 7 / 3) < 1e-12, str(stat.mean))

    zero_stat = describe("b", rows)
    check("0 비율", abs(zero_stat.zero_share - 0.75) < 1e-12, str(zero_stat.zero_share))

    empty = describe("a", _rows({"a": [None, None]}))
    check("전부 결측이면 통계가 None", empty.mean is None and empty.count == 0)
    check("전부 결측도 라벨은 있음", bool(empty.label))

    print()
    print("결측 보고")
    rows = _rows(
        {
            "aged_ratio_25y": [1.0, None, None, 4.0],
            "concrete_ratio": [1.0, 2.0, 3.0, 4.0],
            "subsidence_per_km": [None, None, None, None],
            "population": [100, 200, 300, 400],
            "combined_sewer_ratio": [1.0, None, 3.0, 4.0],
            "unconnected_ratio": [0.0, 0.0, 0.0, 0.0],
            "heavy_rain_zone_ratio": [0.1, 0.2, 0.3, 0.4],
            "large_diameter_ratio": [1.0, 2.0, 3.0, 4.0],
        }
    )
    report = missing_report(rows)
    check("전부 결측 지표 검출", report.fully_missing == ["subsidence_per_km"],
          str(report.fully_missing))
    check(
        "부분 결측 지표 검출",
        set(report.partially_missing) == {"aged_ratio_25y", "combined_sewer_ratio"},
        str(report.partially_missing),
    )
    check("완전 관측 행 0개 (전부 결측 지표 때문)", report.complete_rows == 0,
          str(report.complete_rows))
    check(
        "함께 빠지는 쌍 집계",
        report.co_missing["aged_ratio_25y"]["combined_sewer_ratio"] == 1,
        str(report.co_missing),
    )
    check(
        "전부 결측 지표는 MNAR 진단 대상 아님",
        "subsidence_per_km" not in report.mnar,
        str(list(report.mnar)),
    )

    print()
    print("MNAR 진단")
    # 결측 집단(a=None)이 b 에서 체계적으로 높은 데이터. 검출돼야 합니다.
    biased = _rows(
        {
            "a": [1.0, 2.0, 3.0, 4.0, None, None, None, None],
            "b": [0.1, 0.2, 0.1, 0.2, 0.9, 1.0, 0.9, 1.0],
        }
    )
    checks = mnar_checks(biased, "a", ["a", "b"])
    check("비교 결과 1건", len(checks) == 1, str(len(checks)))
    check("결측군 평균이 더 높음", checks[0].missing_group_mean > checks[0].observed_group_mean)
    check("큰 효과크기 검출", checks[0].concerning, str(checks[0].effect_size))
    check("자기 자신은 비교 대상 아님", all(c.other != "a" for c in checks))

    # 결측이 무작위인 데이터는 검출되지 않아야 합니다.
    random_missing = _rows(
        {
            "a": [1.0, None, 3.0, None, 5.0, None, 7.0, None],
            "b": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        }
    )
    check(
        "상수 지표에서는 효과크기 0",
        mnar_checks(random_missing, "a", ["a", "b"])[0].effect_size == 0.0,
        str(mnar_checks(random_missing, "a", ["a", "b"])[0].effect_size),
    )
    check("결측이 없으면 진단 없음", mnar_checks(_rows({"a": [1.0, 2.0]}), "a", ["a"]) == [])

    print()
    print("상관 행렬 - 쌍별 완전관측")
    rows = _rows(
        {
            "a": [1.0, 2.0, 3.0, None],
            "b": [2.0, 4.0, 6.0, 8.0],
            "c": [3.0, 1.0, 2.0, 4.0],
        }
    )
    matrix, counts = correlation_matrix(rows, ["a", "b", "c"])
    check("대각은 1", all(abs(matrix[n][n] - 1.0) < 1e-12 for n in "abc"))
    check("대칭", abs(matrix["a"]["b"] - matrix["b"]["a"]) < 1e-12)
    check("a-b 완전 상관", abs(matrix["a"]["b"] - 1.0) < 1e-12, str(matrix["a"]["b"]))
    check("a-b 쌍 n=3 (결측 제외)", counts["a"]["b"] == 3, str(counts["a"]["b"]))
    check("b-c 쌍 n=4", counts["b"]["c"] == 4, str(counts["b"]["c"]))

    pairs = high_pairs(matrix, counts, threshold=0.8)
    check("고상관 쌍 검출", any(set(p[:2]) == {"a", "b"} for p in pairs), str(pairs))
    check("쌍 목록에 자기 자신 없음", all(p[0] != p[1] for p in pairs))
    check("n 이 함께 보고됨", all(isinstance(p[3], int) for p in pairs))

    print()
    print("표 렌더링")
    stats = profile(rows, ["a", "b", "c"])
    for renderer, label_text in (
        (lambda: stats_table(stats), "stats_table"),
        (lambda: missing_table(missing_report(rows, ["a", "b", "c"])), "missing_table"),
        (lambda: mnar_table(missing_report(rows, ["a", "b", "c"])), "mnar_table"),
        (lambda: correlation_table(matrix, counts), "correlation_table"),
        (lambda: sido_table(rows, ["a", "b"]), "sido_table"),
    ):
        try:
            check(f"{label_text} 렌더링", bool(renderer()))
        except Exception as error:  # noqa: BLE001
            check(f"{label_text} 렌더링", False, f"{type(error).__name__}: {error}")

    # 전부 결측 지표가 섞여도 표가 깨지지 않아야 합니다.
    all_missing_rows = _rows({"a": [None, None, None], "b": [1.0, 2.0, 3.0]})
    try:
        check(
            "전부 결측 지표가 있어도 표 렌더링",
            bool(stats_table(profile(all_missing_rows, ["a", "b"]))),
        )
    except Exception as error:  # noqa: BLE001
        check("전부 결측 지표가 있어도 표 렌더링", False, str(error))

    print()
    print("보고서 직렬화")
    report_dict = build_report(rows, ["a", "b", "c"])
    try:
        json.dumps(report_dict, ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))
    check(
        "보고서에 필수 키",
        {"stats", "missing", "correlation", "high_pairs"} <= set(report_dict),
        str(sorted(report_dict)),
    )

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
