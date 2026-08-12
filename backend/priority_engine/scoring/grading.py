from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from dataclasses import dataclass

# 점검 자원 배분에 쓰이는 값이므로 상위 등급을 좁게 둡니다.
# 5등급 10%는 229개 중 약 23개로, 한 해에 실제 투입 가능한 규모입니다.
DEFAULT_BANDS: tuple[tuple[int, float], ...] = (
    (5, 0.10),   # 최우선   상위 10%
    (4, 0.20),   # 우선     다음 20%
    (3, 0.40),   # 보통     다음 40%
    (2, 0.20),   # 관찰     다음 20%
    (1, 0.10),   # 양호     하위 10%
)

GRADE_LABELS = {
    5: "최우선",
    4: "우선",
    3: "보통",
    2: "관찰",
    1: "양호",
}

GRADE_DESCRIPTIONS = {
    5: "즉시 점검 대상. 로봇 투입 최우선 배정",
    4: "우선 점검 대상. 5등급 처리 후 순차 투입",
    3: "일반 점검 주기 적용",
    2: "경과 관찰. 지표 악화 시 재평가",
    1: "양호. 법정 주기 점검으로 충분",
}


@dataclass
class GradedDistrict:
    sgg_code: str
    full_name: str
    score: float
    rank: int
    grade: int
    grade_label: str
    percentile: float

    # 시도 내 순위만 채웁니다 (`add_regional_ranks()`).
    # 지역 내에서 등급을 다시 매기지 마세요: 전국 등급과 혼동됩니다.
    sido_code: str | None = None
    sido_name: str | None = None
    sido_rank: int | None = None
    sido_count: int | None = None
    sido_percentile: float | None = None

    def to_dict(self) -> dict:
        payload = {
            "sgg_code": self.sgg_code,
            "full_name": self.full_name,
            "score": round(self.score, 6),
            "rank": self.rank,
            "grade": self.grade,
            "grade_label": self.grade_label,
            "percentile": round(self.percentile, 4),
        }
        if self.sido_rank is not None:
            payload.update(
                {
                    "sido_code": self.sido_code,
                    "sido_name": self.sido_name,
                    "sido_rank": self.sido_rank,
                    "sido_count": self.sido_count,
                    "sido_percentile": round(self.sido_percentile, 4),
                }
            )
        return payload

    def describe(self) -> str:
        parts = []
        if self.sido_rank is not None:
            label = self.sido_name or self.sido_code
            parts.append(f"{label} 내 {self.sido_rank}위/{self.sido_count}")
        parts.append(f"전국 {self.rank}위/{{total}}")
        parts.append(f"{self.grade}등급({self.grade_label})")
        return " · ".join(parts)


def _validate_bands(bands: tuple[tuple[int, float], ...]) -> None:
    total = sum(fraction for _, fraction in bands)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"등급 비율의 합이 1이 아닙니다: {total}")
    if any(fraction <= 0 for _, fraction in bands):
        raise ValueError("등급 비율은 모두 0보다 커야 합니다.")


def assign_grades(
    scored: list,
    bands: tuple[tuple[int, float], ...] = DEFAULT_BANDS,
    score_attr: str = "score",
) -> list[GradedDistrict]:
    _validate_bands(bands)

    if not scored:
        return []

    ordered = sorted(
        scored,
        key=lambda item: (-getattr(item, score_attr), item.sgg_code),
    )
    count = len(ordered)

    # 최대잉여법(largest remainder). 정수 몫을 먼저 배분하고 남은 자리를 소수부가
    # 큰 등급에 줍니다. 합이 항상 정확히 count가 되고, 동률이면 위험한 등급을
    # 우선합니다. `round()`로 바꾸지 마세요 (docstring 참조).
    targets = [(grade, fraction * count) for grade, fraction in bands]
    allocation = {grade: int(target) for grade, target in targets}

    remainder = count - sum(allocation.values())
    if remainder > 0:
        leftovers = sorted(
            targets,
            key=lambda pair: (-(pair[1] - int(pair[1])), -pair[0]),
        )
        for index in range(remainder):
            grade = leftovers[index % len(leftovers)][0]
            allocation[grade] += 1

    graded: list[GradedDistrict] = []
    position_to_grade: dict[int, int] = {}

    position = 1
    for grade, _ in bands:
        for _ in range(allocation[grade]):
            position_to_grade[position] = grade
            position += 1

    # 경계에 걸친 동점 처리: 동점 판정은 순위와 같은 기준(1e-12)을 씁니다.
    # 예전에는 등급만 float 정확 일치로 묶어서, 1e-13 차이 두 구역이 같은
    # 순위인데 등급이 갈렸습니다. 내림차순 정렬이라 동점은 항상 인접하므로
    # 그룹의 첫 위치 하나로 순위와 등급을 함께 정합니다 — 그룹 첫 위치의
    # 등급이 그룹 내 최고(위험한) 등급이라 보수적 통일도 그대로입니다.
    previous_score: float | None = None
    group_start = 1

    for position, item in enumerate(ordered, start=1):
        value = getattr(item, score_attr)

        if previous_score is None or abs(value - previous_score) >= 1e-12:
            group_start = position
            previous_score = value

        rank = group_start
        grade = position_to_grade.get(group_start, bands[-1][0])

        graded.append(
            GradedDistrict(
                sgg_code=item.sgg_code,
                full_name=getattr(item, "full_name", item.sgg_code),
                score=value,
                rank=rank,
                grade=grade,
                grade_label=GRADE_LABELS[grade],
                percentile=1.0 - (position - 0.5) / count,
            )
        )

    return graded


def add_regional_ranks(
    graded: list[GradedDistrict],
    master: list[dict] | None = None,
) -> list[GradedDistrict]:
    names: dict[str, str] = {}
    if master:
        for row in master:
            code = row.get("sido_code")
            name = row.get("sido_name")
            if code and name:
                names[code] = name

    groups: dict[str, list[GradedDistrict]] = {}
    for item in graded:
        # 법정동 계열 5자리의 앞 2자리가 시도 코드입니다 (11110 → 11).
        sido = item.sgg_code[:2]
        item.sido_code = sido
        item.sido_name = names.get(sido)
        groups.setdefault(sido, []).append(item)

    for sido, members in groups.items():
        members.sort(key=lambda item: (-item.score, item.sgg_code))
        count = len(members)

        previous_score: float | None = None
        previous_rank = 0

        for position, item in enumerate(members, start=1):
            if previous_score is not None and abs(item.score - previous_score) < 1e-12:
                rank = previous_rank
            else:
                rank = position
                previous_rank = position
                previous_score = item.score

            item.sido_rank = rank
            item.sido_count = count
            item.sido_percentile = 1.0 - (position - 0.5) / count

    return graded


@dataclass
class SidoSummary:
    sido_code: str
    sido_name: str | None
    district_count: int
    weighted_score: float
    rank: int
    grade_counts: dict[int, int]
    top_district: str

    def to_dict(self) -> dict:
        return {
            "sido_code": self.sido_code,
            "sido_name": self.sido_name,
            "district_count": self.district_count,
            "weighted_score": round(self.weighted_score, 6),
            "rank": self.rank,
            "grade_counts": self.grade_counts,
            "top_district": self.top_district,
        }


def summarize_by_sido(
    graded: list[GradedDistrict],
    exposures: dict[str, float] | None = None,
    master: list[dict] | None = None,
) -> list[SidoSummary]:
    names: dict[str, str] = {}
    if master:
        for row in master:
            code = row.get("sido_code")
            name = row.get("sido_name")
            if code and name:
                names[code] = name

    groups: dict[str, list[GradedDistrict]] = {}
    for item in graded:
        groups.setdefault(item.sgg_code[:2], []).append(item)

    summaries: list[SidoSummary] = []

    for sido, members in groups.items():
        if exposures:
            weights = [max(exposures.get(item.sgg_code, 0.0), 0.0) for item in members]
            total_weight = sum(weights)
        else:
            weights = [1.0] * len(members)
            total_weight = float(len(members))

        if total_weight > 0:
            weighted = sum(
                item.score * weight for item, weight in zip(members, weights)
            ) / total_weight
        else:
            weighted = sum(item.score for item in members) / len(members)

        counts: dict[int, int] = {}
        for item in members:
            counts[item.grade] = counts.get(item.grade, 0) + 1

        best = max(members, key=lambda item: item.score)

        summaries.append(
            SidoSummary(
                sido_code=sido,
                sido_name=names.get(sido) or members[0].sido_name,
                district_count=len(members),
                weighted_score=weighted,
                rank=0,
                grade_counts=dict(sorted(counts.items(), reverse=True)),
                top_district=best.full_name,
            )
        )

    summaries.sort(key=lambda item: (-item.weighted_score, item.sido_code))

    previous: float | None = None
    previous_rank = 0
    for position, item in enumerate(summaries, start=1):
        if previous is not None and abs(item.weighted_score - previous) < 1e-12:
            item.rank = previous_rank
        else:
            item.rank = position
            previous_rank = position
            previous = item.weighted_score

    return summaries


def sido_summary_table(summaries: list[SidoSummary]) -> str:
    lines = [
        f"  {'순위':>4} {'시도':16} {'시군구':>5} {'가중점수':>9}  5등급  최상위 구역",
        "  " + "-" * 68,
    ]
    for item in summaries:
        top_grade = item.grade_counts.get(5, 0)
        name = item.sido_name or item.sido_code
        lines.append(
            f"  {item.rank:4d} {name:16} {item.district_count:5d} "
            f"{item.weighted_score:9.4f}  {top_grade:4d}개  {item.top_district}"
        )
    return "\n".join(lines)


def regional_table(graded: list[GradedDistrict], sido_code: str) -> str:
    members = [item for item in graded if item.sgg_code[:2] == sido_code]
    if not members:
        return f"({sido_code} 시도에 해당하는 구역이 없습니다)"

    members.sort(key=lambda item: (item.sido_rank or 0, item.sgg_code))
    name = members[0].sido_name or sido_code
    total = len(graded)

    lines = [
        f"  {name} 내 우선순위 ({len(members)}개 구역)",
        f"  {'지역':>4} {'구역':16} {'점수':>8} {'전국':>6} {'등급':>10}",
        "  " + "-" * 52,
    ]
    for item in members:
        lines.append(
            f"  {item.sido_rank:3d}위 {item.full_name.split()[-1]:16} "
            f"{item.score:8.4f} {item.rank:4d}위 "
            f"{item.grade}등급({item.grade_label})"
        )
    lines.append("  " + "-" * 52)
    lines.append(f"  전국 {total}개 기준 등급 · 지역 순위는 {name} {len(members)}개 내 비교")
    return "\n".join(lines)


def distribution(graded: list[GradedDistrict]) -> dict[int, int]:
    counts: dict[int, int] = {grade: 0 for grade, _ in DEFAULT_BANDS}
    for item in graded:
        counts[item.grade] = counts.get(item.grade, 0) + 1
    return counts


def summary(graded: list[GradedDistrict]) -> str:
    if not graded:
        return "(등급이 부여된 구역이 없습니다)"

    counts = distribution(graded)
    total = len(graded)

    lines = [f"우선순위 등급 분포: 전체 {total}개"]
    for grade in sorted(counts, reverse=True):
        count = counts[grade]
        share = count / total if total else 0.0
        bar = "█" * int(share * 50)
        lines.append(
            f"  {grade}등급 {GRADE_LABELS[grade]:6} {count:4d}개 ({share:5.1%})  {bar}"
        )
        lines.append(f"        └ {GRADE_DESCRIPTIONS[grade]}")

    top = [item for item in graded if item.grade == 5]
    if top:
        lines.append("")
        lines.append(f"  5등급(최우선) {len(top)}개:")
        for item in top[:15]:
            lines.append(f"    {item.sgg_code} {item.full_name}")
        if len(top) > 15:
            lines.append(f"    ... 외 {len(top) - 15}개")

    return "\n".join(lines)


def grade_stability(
    baseline: list[GradedDistrict],
    variants: list[list[GradedDistrict]],
) -> dict:
    if not baseline or not variants:
        raise ValueError("baseline과 variants가 모두 필요합니다.")

    base_map = {item.sgg_code: item.grade for item in baseline}
    retentions: list[float] = []
    shifts: dict[str, int] = {code: 0 for code in base_map}

    for variant in variants:
        variant_map = {item.sgg_code: item.grade for item in variant}
        shared = set(base_map) & set(variant_map)
        if not shared:
            continue

        same = sum(1 for code in shared if base_map[code] == variant_map[code])
        retentions.append(same / len(shared))

        for code in shared:
            shifts[code] = max(shifts[code], abs(base_map[code] - variant_map[code]))

    if not retentions:
        raise ValueError("baseline과 variants의 시군구 코드가 겹치지 않습니다.")

    moved_two_plus = sum(1 for value in shifts.values() if value >= 2)

    return {
        "variant_count": len(retentions),
        "grade_retention_min": round(min(retentions), 4),
        "grade_retention_mean": round(sum(retentions) / len(retentions), 4),
        "districts_moved_2plus_grades": moved_two_plus,
        "max_grade_shift": max(shifts.values()) if shifts else 0,
    }


# ---------------------------------------------------------------- 자체 검증


@dataclass
class _Item:
    sgg_code: str
    full_name: str
    score: float


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("등급 배분: 229개 (실제 규모)")
    items = [
        _Item(f"{10000 + index}", f"구역{index}", (229 - index) / 229.0)
        for index in range(229)
    ]
    graded = assign_grades(items)
    print("    " + summary(graded).replace("\n", "\n    "))

    counts = distribution(graded)
    check("전체 등급 부여", len(graded) == 229, str(len(graded)))
    check(
        "등급 합 = 전체",
        sum(counts.values()) == 229,
        str(counts),
    )
    check(
        "5등급이 약 10% (23개 내외)",
        20 <= counts[5] <= 26,
        str(counts[5]),
    )
    check(
        "3등급이 가장 많음 (약 40%)",
        counts[3] == max(counts.values()),
        str(counts),
    )
    check(
        "점수 1위가 5등급",
        graded[0].grade == 5,
        str(graded[0].grade),
    )
    check(
        "점수 최하위가 1등급",
        graded[-1].grade == 1,
        str(graded[-1].grade),
    )
    check(
        "등급이 단조 감소 (점수 내림차순 정렬 기준)",
        all(
            graded[index].grade >= graded[index + 1].grade
            for index in range(len(graded) - 1)
        ),
        "",
    )
    check(
        "백분위 범위 (0,1)",
        all(0 < item.percentile < 1 for item in graded),
        "",
    )

    print()
    print("동점 처리")
    tied = [
        _Item("A", "A", 0.9),
        _Item("B", "B", 0.5),
        _Item("C", "C", 0.5),
        _Item("D", "D", 0.5),
        _Item("E", "E", 0.1),
    ]
    tied_graded = assign_grades(tied)
    by_code = {item.sgg_code: item for item in tied_graded}
    check(
        "동점은 같은 등급",
        by_code["B"].grade == by_code["C"].grade == by_code["D"].grade,
        str([(i.sgg_code, i.grade) for i in tied_graded]),
    )
    check(
        "동점은 같은 순위",
        by_code["B"].rank == by_code["C"].rank == by_code["D"].rank,
        "",
    )
    # 1e-13 차이는 순위 기준(1e-12)으로는 동점입니다. 예전에는 순위만 묶이고
    # 등급은 float 정확 일치로 갈라져, 같은 1위가 5등급과 4등급으로 나뉘었습니다.
    near = assign_grades(
        [_Item("A", "A", 0.5), _Item("B", "B", 0.5 + 1e-13), _Item("C", "C", 0.1)]
    )
    near_map = {item.sgg_code: item for item in near}
    check(
        "1e-12 미만 차이는 순위와 등급이 함께 묶임",
        near_map["A"].rank == near_map["B"].rank
        and near_map["A"].grade == near_map["B"].grade,
        str([(i.sgg_code, i.rank, i.grade) for i in near]),
    )

    print()
    print("등급이 순위보다 안정적인가 (등급으로 내는 이유)")
    # 점수에 잡음을 섞어 여러 변형을 만들고, 순위 유지율과 등급 유지율을 비교합니다.
    state = 4242

    def rand() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    variants = []
    rank_retentions = []
    base_top_ranks = {item.sgg_code for item in graded if item.rank <= 23}

    for _ in range(30):
        noisy = [
            _Item(item.sgg_code, item.full_name, item.score + (rand() - 0.5) * 0.06)
            for item in items
        ]
        variant = assign_grades(noisy)
        variants.append(variant)

        variant_top = {item.sgg_code for item in variant if item.rank <= 23}
        rank_retentions.append(
            len(base_top_ranks & variant_top) / len(base_top_ranks)
        )

    stability = grade_stability(graded, variants)
    rank_mean = sum(rank_retentions) / len(rank_retentions)

    print(f"    순위 상위 23위 유지율   평균 {rank_mean:.1%}")
    print(f"    등급 유지율             평균 {stability['grade_retention_mean']:.1%}"
          f" (최소 {stability['grade_retention_min']:.1%})")
    print(f"    2등급 이상 이동한 구역  {stability['districts_moved_2plus_grades']}개")
    print(f"    최대 등급 이동          {stability['max_grade_shift']}등급")

    check(
        "등급 유지율 > 순위 유지율 (등급이 더 안정적)",
        stability["grade_retention_mean"] > rank_mean,
        f"등급 {stability['grade_retention_mean']:.3f} vs 순위 {rank_mean:.3f}",
    )
    check(
        "2등급 이상 이동한 구역이 적음",
        stability["districts_moved_2plus_grades"] <= len(items) * 0.05,
        str(stability["districts_moved_2plus_grades"]),
    )

    print()
    print("weighted / supervised 결과와 호환되는가")
    from scoring.weighted import score_districts

    sample = [
        {
            "sgg_code": f"{20000 + index}",
            "full_name": f"S{index}",
            "combined_sewer_ratio": index / 60.0,
            "facility_avg_age_years": float(index),
            "sewer_subsidence_per_km": index / 200.0,
            "unconnected_ratio": index / 300.0,
        }
        for index in range(60)
    ]
    weighted_scored = score_districts(sample)
    weighted_graded = assign_grades(weighted_scored)
    check(
        "ScoredDistrict 호환",
        len(weighted_graded) == 60 and weighted_graded[0].grade == 5,
        "",
    )

    print()
    print("경계 조건")
    check("빈 입력", assign_grades([]) == [], "")

    # 1개만 넣으면 비교 대상이 없으므로 3등급(소수부 최대)이 옳습니다.
    single = assign_grades([_Item("X", "X", 1.0)])
    check("1개 입력은 중간 등급 (비교 대상 없음)", single[0].grade == 3, str(single[0].grade))
    check("1개 입력의 백분위는 0.5", single[0].percentile == 0.5, str(single[0].percentile))

    three = assign_grades([_Item(c, c, s) for c, s in zip("ABC", [3.0, 2.0, 1.0])])
    check(
        "3개 입력도 전부 등급 부여",
        len(three) == 3 and {i.grade for i in three} <= {1, 2, 3, 4, 5},
        str([(i.sgg_code, i.grade) for i in three]),
    )

    # 광주 5개 구만 등급화하는 경우: 최상위 등급이 비면 안 됩니다.
    print()
    print("표본이 작을 때 (광주 5개 구 시나리오)")
    gwangju = [
        _Item("29140", "광주 서구", 0.90),
        _Item("29110", "광주 동구", 0.75),
        _Item("29155", "광주 남구", 0.55),
        _Item("29170", "광주 북구", 0.40),
        _Item("29200", "광주 광산구", 0.20),
    ]
    small = assign_grades(gwangju)
    for item in small:
        print(f"    {item.sgg_code} {item.full_name:12} {item.grade}등급 {item.grade_label}")
    small_counts = distribution(small)
    check(
        "5개여도 등급 합 = 5",
        sum(small_counts.values()) == 5,
        str(small_counts),
    )
    check(
        "5개여도 최상위(5등급)가 비지 않음",
        small_counts[5] >= 1,
        str(small_counts),
    )
    check(
        "최고 점수가 5등급",
        small[0].grade == 5,
        str(small[0].grade),
    )

    all_same = assign_grades([_Item(f"{i}", f"{i}", 0.5) for i in range(20)])
    check(
        "전부 동점이면 모두 같은 등급",
        len({item.grade for item in all_same}) == 1,
        str({item.grade for item in all_same}),
    )
    check(
        "전부 동점이면 최고 등급 (보수적)",
        all_same[0].grade == 5,
        str(all_same[0].grade),
    )

    try:
        assign_grades(items, bands=((5, 0.5), (1, 0.3)))
        check("비율 합이 1이 아니면 예외", False, "")
    except ValueError:
        check("비율 합이 1이 아니면 예외", True)

    try:
        assign_grades(items, bands=((5, 1.5), (1, -0.5)))
        check("음수 비율은 예외", False, "")
    except ValueError:
        check("음수 비율은 예외", True)

    try:
        grade_stability(graded, [])
        check("변형이 없으면 예외", False, "")
    except ValueError:
        check("변형이 없으면 예외", True)

    print()
    print("사용자 지정 밴드")
    custom = assign_grades(items, bands=((5, 0.05), (4, 0.15), (3, 0.6), (2, 0.15), (1, 0.05)))
    custom_counts = distribution(custom)
    check(
        "5% 밴드 적용 (11~12개)",
        10 <= custom_counts[5] <= 13,
        str(custom_counts[5]),
    )

    print()
    print("지역(시도) 내 순위")
    # 서울 3개 + 광주 5개 + 경기 2개로 시도 그룹핑을 검증합니다.
    mixed = [
        _Item("11110", "서울특별시 종로구", 0.95),
        _Item("11140", "서울특별시 중구", 0.90),
        _Item("11170", "서울특별시 용산구", 0.30),
        _Item("29110", "광주광역시 동구", 0.60),
        _Item("29140", "광주광역시 서구", 0.65),
        _Item("29155", "광주광역시 남구", 0.55),
        _Item("29170", "광주광역시 북구", 0.50),
        _Item("29200", "광주광역시 광산구", 0.20),
        _Item("41110", "경기도 수원시", 0.70),
        _Item("41130", "경기도 성남시", 0.40),
    ]
    master = [
        {"sido_code": "11", "sido_name": "서울특별시"},
        {"sido_code": "29", "sido_name": "광주광역시"},
        {"sido_code": "41", "sido_name": "경기도"},
    ]
    mixed_graded = add_regional_ranks(assign_grades(mixed), master)
    by_code = {item.sgg_code: item for item in mixed_graded}

    check(
        "시도 코드가 sgg_code 앞 2자리",
        by_code["29140"].sido_code == "29",
        by_code["29140"].sido_code,
    )
    check(
        "시도명이 붙음",
        by_code["29140"].sido_name == "광주광역시",
        str(by_code["29140"].sido_name),
    )
    check(
        "광주 내 1위는 서구 (0.65)",
        by_code["29140"].sido_rank == 1,
        str(by_code["29140"].sido_rank),
    )
    check(
        "광주 내 최하위는 광산구 (0.20)",
        by_code["29200"].sido_rank == 5,
        str(by_code["29200"].sido_rank),
    )
    check(
        "광주 구역 수 5개로 기록",
        all(by_code[c].sido_count == 5 for c in ("29110", "29140", "29200")),
        "",
    )
    check(
        "서울 내 1위는 종로구",
        by_code["11110"].sido_rank == 1 and by_code["11110"].sido_count == 3,
        str((by_code["11110"].sido_rank, by_code["11110"].sido_count)),
    )
    check(
        "전국 순위와 지역 순위가 다름 (광주 서구)",
        by_code["29140"].rank != by_code["29140"].sido_rank,
        f"전국 {by_code['29140'].rank}위 / 광주 {by_code['29140'].sido_rank}위",
    )
    check(
        "등급은 전국 기준 유지 (지역 재계산 안 함)",
        by_code["29140"].grade == next(
            i.grade for i in assign_grades(mixed) if i.sgg_code == "29140"
        ),
        "",
    )

    print()
    print("    " + regional_table(mixed_graded, "29").replace("\n", "\n    "))

    print()
    print("지역 순위: 동점 처리")
    tied_region = [
        _Item("29110", "광주광역시 동구", 0.5),
        _Item("29140", "광주광역시 서구", 0.5),
        _Item("29155", "광주광역시 남구", 0.2),
    ]
    tied_graded = add_regional_ranks(assign_grades(tied_region), master)
    tied_map = {item.sgg_code: item for item in tied_graded}
    check(
        "지역 내 동점은 같은 순위",
        tied_map["29110"].sido_rank == tied_map["29140"].sido_rank == 1,
        str([(i.sgg_code, i.sido_rank) for i in tied_graded]),
    )

    print()
    print("시도 단위 집계")
    # 연장 가중: 서울 종로구에 큰 연장을 주면 서울 점수가 올라가야 합니다.
    exposures = {item.sgg_code: 100.0 for item in mixed}
    exposures["11110"] = 10000.0
    summaries = summarize_by_sido(mixed_graded, exposures, master)
    print("    " + sido_summary_table(summaries).replace("\n", "\n    "))

    check(
        "시도 3개 집계",
        len(summaries) == 3,
        str(len(summaries)),
    )
    check(
        "순위가 1부터 부여",
        summaries[0].rank == 1,
        str(summaries[0].rank),
    )
    check(
        "가중점수 내림차순",
        all(
            summaries[i].weighted_score >= summaries[i + 1].weighted_score
            for i in range(len(summaries) - 1)
        ),
        "",
    )
    check(
        "연장 가중이 반영됨 (종로구 가중치 100배 → 서울 1위)",
        summaries[0].sido_code == "11",
        str([(s.sido_code, round(s.weighted_score, 3)) for s in summaries]),
    )

    unweighted = summarize_by_sido(mixed_graded, None, master)
    seoul_weighted = next(s for s in summaries if s.sido_code == "11").weighted_score
    seoul_plain = next(s for s in unweighted if s.sido_code == "11").weighted_score
    check(
        "가중/비가중 결과가 다름 (가중이 실제로 동작)",
        abs(seoul_weighted - seoul_plain) > 1e-6,
        f"{seoul_weighted:.4f} vs {seoul_plain:.4f}",
    )

    check(
        "시도별 등급 분포 기록",
        all(sum(s.grade_counts.values()) == s.district_count for s in summaries),
        str([(s.sido_code, s.grade_counts) for s in summaries]),
    )

    print()
    print("직렬화")
    try:
        json.dumps([item.to_dict() for item in graded[:5]], ensure_ascii=False)
        json.dumps([item.to_dict() for item in mixed_graded], ensure_ascii=False)
        json.dumps([item.to_dict() for item in summaries], ensure_ascii=False)
        check("JSON 직렬화 가능 (지역 순위·시도 집계 포함)", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    check(
        "지역 순위 없으면 to_dict에 시도 키가 없음",
        "sido_rank" not in graded[0].to_dict(),
        str(graded[0].to_dict().keys()),
    )

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
