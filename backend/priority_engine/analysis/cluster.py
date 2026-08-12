from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import math
from dataclasses import dataclass

import dataset  # noqa: E402
from analysis.profile import median, stdev  # noqa: E402

Point = list[float]

# 시드를 고정합니다. k-means 는 초기값에 따라 답이 달라지므로, 같은 데이터에
# 같은 결과가 나와야 발표 자료의 유형 번호가 실행마다 바뀌지 않습니다.
DEFAULT_SEED = 20260731

# 초기값이 나쁘면 국소 최적에 갇히므로 여러 번 돌려 최선을 고릅니다.
DEFAULT_RESTARTS = 10

MAX_ITERATIONS = 100

# 실루엣 계수 해석 기준(Kaufman & Rousseeuw 관례).
STRONG_STRUCTURE = 0.5
WEAK_STRUCTURE = 0.25

DEFAULT_K_RANGE = (2, 3, 4, 5, 6)


def _lcg(seed: int):
    state = seed % (2**31)

    def nxt() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    return nxt


def distance_squared(left: Point, right: Point) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def build_points(
    rows: list[dict], names: list[str]
) -> tuple[list[Point], list[int], list[float], list[float]]:
    # 결측은 관측값의 중앙값으로 채웁니다. 완전관측만 쓰면 229개가 절반으로
    # 줄어 유형을 못 받는 구역이 생기는데, 유형 부여가 이 분석의 목적입니다.
    # 대신 구역별로 몇 개를 채웠는지 세어 함께 보고합니다.
    fills = {}
    for name in names:
        observed = [row[name] for row in rows if row.get(name) is not None]
        fills[name] = median(observed) if observed else 0.0

    raw: list[Point] = []
    imputed: list[int] = []
    for row in rows:
        vector = []
        count = 0
        for name in names:
            value = row.get(name)
            if value is None:
                value = fills[name]
                count += 1
            vector.append(float(value))
        raw.append(vector)
        imputed.append(count)

    # 표준화 없이 거리를 재면 단위가 큰 지표가 군집을 지배합니다.
    means = []
    deviations = []
    for index in range(len(names)):
        column = [vector[index] for vector in raw]
        mean = sum(column) / len(column)
        deviation = stdev(column)
        means.append(mean)
        deviations.append(deviation if deviation > 1e-12 else 1.0)

    points = [
        [(value - means[i]) / deviations[i] for i, value in enumerate(vector)]
        for vector in raw
    ]
    return points, imputed, means, deviations


def kmeans_plus_plus(points: list[Point], k: int, seed: int) -> list[Point]:
    nxt = _lcg(seed)
    count = len(points)

    first = min(int(nxt() * count), count - 1)
    centroids = [list(points[first])]

    while len(centroids) < k:
        # 가장 가까운 중심까지의 거리² 에 비례해 다음 중심을 고릅니다.
        # 무작위로 고르는 것보다 국소 최적에 갇힐 확률이 낮습니다.
        weights = [
            min(distance_squared(point, centroid) for centroid in centroids)
            for point in points
        ]
        total = sum(weights)
        if total < 1e-18:
            # 남은 점이 모두 기존 중심과 같습니다. 중복 중심을 넣습니다.
            centroids.append(list(points[len(centroids) % count]))
            continue

        target = nxt() * total
        running = 0.0
        chosen = count - 1
        for index, weight in enumerate(weights):
            running += weight
            if running >= target:
                chosen = index
                break
        centroids.append(list(points[chosen]))

    return centroids


def assign(points: list[Point], centroids: list[Point]) -> list[int]:
    labels = []
    for point in points:
        best = 0
        best_distance = distance_squared(point, centroids[0])
        for index in range(1, len(centroids)):
            candidate = distance_squared(point, centroids[index])
            if candidate < best_distance:
                best = index
                best_distance = candidate
        labels.append(best)
    return labels


def recentre(points: list[Point], labels: list[int], k: int) -> list[Point]:
    dimensions = len(points[0])
    sums = [[0.0] * dimensions for _ in range(k)]
    counts = [0] * k

    for point, label in zip(points, labels):
        counts[label] += 1
        for index, value in enumerate(point):
            sums[label][index] += value

    centroids = []
    for index in range(k):
        if counts[index] == 0:
            # 빈 군집은 중심이 정의되지 않습니다. 현재 중심에서 가장 먼 점을
            # 새 중심으로 삼아 k개를 유지합니다(빈 군집을 버리면 k가 줄어듭니다).
            filled = [
                [sums[j][d] / counts[j] for d in range(dimensions)]
                for j in range(k)
                if counts[j] > 0
            ]
            farthest = max(
                points,
                key=lambda point: min(
                    distance_squared(point, centroid) for centroid in filled
                ),
            )
            centroids.append(list(farthest))
            continue
        centroids.append([sums[index][d] / counts[index] for d in range(dimensions)])

    return centroids


def inertia(points: list[Point], labels: list[int], centroids: list[Point]) -> float:
    return sum(
        distance_squared(point, centroids[label])
        for point, label in zip(points, labels)
    )


def kmeans(
    points: list[Point], k: int, seed: int = DEFAULT_SEED, restarts: int = DEFAULT_RESTARTS
) -> tuple[list[int], list[Point], float]:
    if not points:
        raise ValueError("points 가 비어 있습니다.")
    if not 1 <= k <= len(points):
        raise ValueError(f"k 는 1 이상 {len(points)} 이하여야 합니다: {k}")

    best: tuple[list[int], list[Point], float] | None = None

    for attempt in range(restarts):
        centroids = kmeans_plus_plus(points, k, seed + attempt * 7919)
        labels = assign(points, centroids)

        for _ in range(MAX_ITERATIONS):
            centroids = recentre(points, labels, k)
            updated = assign(points, centroids)
            if updated == labels:
                break
            labels = updated

        score = inertia(points, labels, centroids)
        if best is None or score < best[2] - 1e-12:
            best = (labels, centroids, score)

    return best


def silhouette(points: list[Point], labels: list[int]) -> float:
    # 각 점에 대해 (같은 군집 평균거리 a, 가장 가까운 다른 군집 평균거리 b) 를
    # 구해 (b - a) / max(a, b). 1에 가까우면 잘 나뉜 것, 0이면 경계, 음수면
    # 다른 군집에 있어야 할 점입니다.
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)

    if len(groups) < 2:
        return 0.0

    total = 0.0
    for index, point in enumerate(points):
        own = groups[labels[index]]
        if len(own) == 1:
            # 혼자인 군집은 a 가 정의되지 않습니다. 관례상 0으로 둡니다.
            continue

        a = sum(
            math.sqrt(distance_squared(point, points[other]))
            for other in own
            if other != index
        ) / (len(own) - 1)

        b = min(
            sum(
                math.sqrt(distance_squared(point, points[other]))
                for other in members
            )
            / len(members)
            for label, members in groups.items()
            if label != labels[index]
        )

        divisor = max(a, b)
        total += 0.0 if divisor < 1e-18 else (b - a) / divisor

    return total / len(points)


@dataclass
class ClusterProfile:
    label: int
    size: int
    means: dict[str, float]
    imputed_share: float
    representatives: list[str]
    distinctive: list[tuple[str, float]]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "size": self.size,
            "means": {name: round(value, 6) for name, value in self.means.items()},
            "imputed_share": round(self.imputed_share, 4),
            "representatives": self.representatives,
            "distinctive": [(name, round(value, 3)) for name, value in self.distinctive],
        }


@dataclass
class Clustering:
    k: int
    names: list[str]
    dropped_all_missing: list[str]
    labels: list[int]
    silhouette: float
    inertia: float
    profiles: list[ClusterProfile]
    tried: dict[int, float]

    def to_dict(self) -> dict:
        return {
            "k": self.k,
            "names": self.names,
            "dropped_all_missing": self.dropped_all_missing,
            "silhouette": round(self.silhouette, 4),
            "inertia": round(self.inertia, 4),
            "tried": {str(k): round(score, 4) for k, score in self.tried.items()},
            "profiles": [profile.to_dict() for profile in self.profiles],
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        sizes = ", ".join(str(profile.size) for profile in self.profiles)
        return (
            f"유형 {self.k}개로 나눴습니다 (크기 {sizes}). "
            f"실루엣 계수 {self.silhouette:.3f}. "
            f"지표 {len(self.names)}개 기준."
        )

    @property
    def verdict(self) -> str:
        lines = []
        if self.silhouette >= STRONG_STRUCTURE:
            lines.append("군집 구조가 뚜렷합니다. 유형 구분이 데이터에 실재합니다.")
        elif self.silhouette >= WEAK_STRUCTURE:
            lines.append(
                "군집 구조가 약합니다. 유형은 경향성이지 뚜렷한 경계가 아닙니다."
            )
        else:
            lines.append(
                "군집 구조가 거의 없습니다. 지표 공간이 연속적이라 유형 구분을 "
                "단정하면 안 됩니다."
            )

        lines.append(
            "군집은 순위와 독립적입니다. 같은 등급 안에서도 유형이 다를 수 있고, "
            "그때 점검 방법을 달리 골라야 합니다."
        )

        heavy = [p for p in self.profiles if p.imputed_share >= 0.5]
        if heavy:
            labels = ", ".join(f"유형 {p.label}" for p in heavy)
            lines.append(
                f"{labels}은 지표 절반 이상이 결측 대체값입니다. "
                "그 유형은 '데이터가 없는 구역들'일 수 있습니다."
            )
        return " ".join(lines)


def describe_clusters(
    rows: list[dict],
    names: list[str],
    labels: list[int],
    points: list[Point],
    centroids: list[Point],
    imputed: list[int],
) -> list[ClusterProfile]:
    k = len(centroids)
    overall = {
        name: (
            sum(row[name] for row in rows if row.get(name) is not None)
            / max(sum(1 for row in rows if row.get(name) is not None), 1)
        )
        for name in names
    }

    profiles = []
    for label in range(k):
        members = [index for index, value in enumerate(labels) if value == label]
        if not members:
            continue

        means = {}
        for name in names:
            values = [
                rows[index][name]
                for index in members
                if rows[index].get(name) is not None
            ]
            means[name] = sum(values) / len(values) if values else 0.0

        # 전국 평균과 가장 크게 다른 지표가 그 유형의 성격입니다.
        distinctive = sorted(
            (
                (name, means[name] - overall[name])
                for name in names
            ),
            key=lambda pair: -abs(pair[1]),
        )[:3]

        closest = sorted(
            members, key=lambda index: distance_squared(points[index], centroids[label])
        )[:3]

        profiles.append(
            ClusterProfile(
                label=label,
                size=len(members),
                means=means,
                imputed_share=sum(imputed[index] for index in members)
                / (len(members) * len(names)),
                representatives=[rows[index].get("full_name", "") for index in closest],
                distinctive=distinctive,
            )
        )

    return profiles


def analyze(
    rows: list[dict],
    names: list[str] | None = None,
    k: int | None = None,
    k_range: tuple[int, ...] = DEFAULT_K_RANGE,
    seed: int = DEFAULT_SEED,
) -> Clustering:
    if not rows:
        raise ValueError("rows 가 비어 있습니다.")

    names = list(names or dataset.INDICATORS)
    available = dataset.available_indicators(rows, names)
    dropped = [name for name in names if name not in available]
    if len(available) < 2:
        raise ValueError(f"관측된 지표가 {len(available)}개뿐입니다. 군집화를 할 수 없습니다.")

    points, imputed, _, _ = build_points(rows, available)

    tried: dict[int, float] = {}
    if k is None:
        for candidate in k_range:
            if candidate >= len(points):
                continue
            labels, _, _ = kmeans(points, candidate, seed)
            tried[candidate] = silhouette(points, labels)
        if not tried:
            raise ValueError(f"구역이 {len(points)}개뿐이라 시도할 k가 없습니다.")
        k = max(tried, key=lambda candidate: tried[candidate])

    labels, centroids, score = kmeans(points, k, seed)

    return Clustering(
        k=k,
        names=available,
        dropped_all_missing=dropped,
        labels=labels,
        silhouette=silhouette(points, labels),
        inertia=score,
        profiles=describe_clusters(rows, available, labels, points, centroids, imputed),
        tried=tried,
    )


def k_table(result: Clustering) -> str:
    if not result.tried:
        return f"  k={result.k} 를 지정받아 탐색을 건너뜁니다."

    lines = [f"  {'k':>3} {'실루엣':>9}", "  " + "-" * 24]
    for candidate in sorted(result.tried):
        score = result.tried[candidate]
        mark = "  ← 선택" if candidate == result.k else ""
        bar = "█" * int(max(score, 0) * 30)
        lines.append(f"  {candidate:3d} {score:9.3f} {bar}{mark}")
    return "\n".join(lines)


def cluster_table(result: Clustering) -> str:
    lines = [
        f"  {'유형':>4} {'구역':>4} {'대체':>6} "
        + "".join(f"{name[:9]:>11}" for name in result.names),
        "  " + "-" * (18 + 11 * len(result.names)),
    ]
    for profile in result.profiles:
        cells = "".join(f"{profile.means[name]:11.3f}" for name in result.names)
        lines.append(
            f"  {profile.label:4d} {profile.size:4d} {profile.imputed_share:6.0%} {cells}"
        )

    lines.append("")
    for profile in result.profiles:
        traits = ", ".join(
            f"{name} {'+' if gap > 0 else ''}{gap:.3f}"
            for name, gap in profile.distinctive
        )
        lines.append(f"  유형 {profile.label} ({profile.size}개) 전국 평균과의 차이: {traits}")
        lines.append(f"    대표 구역: {', '.join(profile.representatives)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 자체 검증


def _blobs(centers: list[Point], per_center: int, spread: float, seed: int = 7) -> list[dict]:
    nxt = _lcg(seed)
    rows = []
    index = 0
    for group, center in enumerate(centers):
        for _ in range(per_center):
            rows.append(
                {
                    "sgg_code": f"{10000 + index}",
                    "full_name": f"G{group}-{index}",
                    "_truth": group,
                    "aged_ratio_25y": center[0] + (nxt() - 0.5) * spread,
                    "concrete_ratio": center[1] + (nxt() - 0.5) * spread,
                    "combined_sewer_ratio": center[2] + (nxt() - 0.5) * spread,
                }
            )
            index += 1
    return rows


NAMES = ["aged_ratio_25y", "concrete_ratio", "combined_sewer_ratio"]


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("거리·전처리")
    check("거리² 계산", distance_squared([0.0, 0.0], [3.0, 4.0]) == 25.0)
    check("같은 점은 거리 0", distance_squared([1.0, 2.0], [1.0, 2.0]) == 0.0)

    rows = [
        {"sgg_code": "1", "full_name": "A", "aged_ratio_25y": 0.0, "concrete_ratio": 10.0},
        {"sgg_code": "2", "full_name": "B", "aged_ratio_25y": 1.0, "concrete_ratio": 20.0},
        {"sgg_code": "3", "full_name": "C", "aged_ratio_25y": None, "concrete_ratio": 30.0},
    ]
    points, imputed, _, _ = build_points(rows, ["aged_ratio_25y", "concrete_ratio"])
    check("점 개수 = 구역 수", len(points) == 3)
    check("결측 대체 횟수 기록", imputed == [0, 0, 1], str(imputed))
    check(
        "표준화 후 평균 ~0",
        abs(sum(point[1] for point in points) / 3) < 1e-12,
        str(sum(point[1] for point in points) / 3),
    )
    check(
        "단위가 큰 지표가 지배하지 않음",
        abs(max(point[1] for point in points)) < 2.0,
        str(max(point[1] for point in points)),
    )

    print()
    print("k-means - 구조를 아는 데이터 (3개 덩어리)")
    blobs = _blobs([[0.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 10.0]], 20, 1.0)
    result = analyze(blobs, NAMES, k=3)
    check("k=3", result.k == 3)
    check("군집 3개 생성", len(result.profiles) == 3, str(len(result.profiles)))
    check("크기 합 = 전체", sum(p.size for p in result.profiles) == 60,
          str(sum(p.size for p in result.profiles)))
    check("각 군집 크기 20", all(p.size == 20 for p in result.profiles),
          str([p.size for p in result.profiles]))

    # 진짜 그룹과 일치하는지: 같은 진짜 그룹이 같은 라벨을 받아야 합니다.
    pure = True
    for group in range(3):
        assigned = {
            result.labels[index]
            for index, row in enumerate(blobs)
            if row["_truth"] == group
        }
        if len(assigned) != 1:
            pure = False
    check("진짜 그룹이 하나의 군집으로 묶임", pure)
    check("실루엣이 높음 (>0.5)", result.silhouette > 0.5, str(result.silhouette))
    check("판정문이 뚜렷하다고 말함", "뚜렷" in result.verdict, result.verdict[:40])

    print()
    print("k-means - 구조가 없는 데이터")
    nxt = _lcg(31337)
    noise = [
        {
            "sgg_code": f"{i}",
            "full_name": f"N{i}",
            "aged_ratio_25y": nxt(),
            "concrete_ratio": nxt(),
            "combined_sewer_ratio": nxt(),
        }
        for i in range(60)
    ]
    noisy = analyze(noise, NAMES, k=3)
    check("무작위 데이터는 실루엣이 낮음", noisy.silhouette < result.silhouette,
          f"{noisy.silhouette} vs {result.silhouette}")
    check("무작위 데이터 판정이 신중함", "단정" in noisy.verdict or "약" in noisy.verdict,
          noisy.verdict[:50])

    print()
    print("실루엣 계수")
    points, _, _, _ = build_points(blobs, NAMES)
    check("범위 -1~1", -1.0 <= silhouette(points, result.labels) <= 1.0)
    check("군집 1개면 0", silhouette(points, [0] * len(points)) == 0.0)
    check(
        "잘 나뉜 쪽이 더 높음",
        silhouette(points, result.labels) > silhouette(points, [i % 3 for i in range(len(points))]),
    )

    print()
    print("k 자동 선택")
    auto = analyze(blobs, NAMES)
    check("k를 탐색함", len(auto.tried) > 1, str(auto.tried))
    check("실루엣이 가장 높은 k 선택", auto.k == max(auto.tried, key=lambda c: auto.tried[c]),
          f"{auto.k} vs {auto.tried}")
    check("3개 덩어리에서 k=3을 찾음", auto.k == 3, f"{auto.k}, {auto.tried}")

    print()
    print("결정성")
    first = analyze(blobs, NAMES, k=3)
    second = analyze(blobs, NAMES, k=3)
    check("같은 시드는 같은 라벨", first.labels == second.labels)
    check("같은 시드는 같은 실루엣", first.silhouette == second.silhouette)
    check(
        "같은 시드는 같은 대표 구역",
        [p.representatives for p in first.profiles] == [p.representatives for p in second.profiles],
    )

    print()
    print("이너셔")
    scores = [analyze(blobs, NAMES, k=k).inertia for k in (2, 3, 4)]
    check("k가 커지면 이너셔 감소", scores[0] > scores[1] > scores[2], str(scores))
    check("이너셔는 음수가 아님", all(score >= 0 for score in scores))

    print()
    print("빈 군집 처리 (중복점 + 큰 k)")
    duplicates = [
        {"sgg_code": f"{i}", "full_name": f"D{i}",
         "aged_ratio_25y": 0.5, "concrete_ratio": 0.5, "combined_sewer_ratio": 0.5}
        for i in range(10)
    ]
    duplicates[0]["aged_ratio_25y"] = 5.0
    duplicated = analyze(duplicates, NAMES, k=4)
    check("k개 군집 유지 (빈 군집을 버리지 않음)", duplicated.k == 4)
    check("전체 구역이 배정됨", sum(p.size for p in duplicated.profiles) == 10,
          str(sum(p.size for p in duplicated.profiles)))

    print()
    print("결측 대체 보고")
    with_missing = _blobs([[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], 15, 1.0)
    for row in with_missing[:15]:
        row["aged_ratio_25y"] = None
    missing_result = analyze(with_missing, NAMES, k=2)
    check(
        "대체 비율이 집계됨",
        any(p.imputed_share > 0 for p in missing_result.profiles),
        str([p.imputed_share for p in missing_result.profiles]),
    )
    check(
        "대체 비율은 0~1",
        all(0 <= p.imputed_share <= 1 for p in missing_result.profiles),
    )

    all_missing = _blobs([[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], 15, 1.0)
    for row in all_missing:
        row["sewer_subsidence_per_km"] = None
    dropped_result = analyze(
        all_missing, [*NAMES, "sewer_subsidence_per_km"], k=2
    )
    check(
        "전부 결측 지표 제외",
        dropped_result.dropped_all_missing == ["sewer_subsidence_per_km"],
        str(dropped_result.dropped_all_missing),
    )

    print()
    print("경계 조건")
    for bad_k, label_text in ((0, "k=0"), (999, "k > 구역 수")):
        try:
            points, _, _, _ = build_points(blobs, NAMES)
            kmeans(points, bad_k)
            check(f"{label_text} 는 예외", False, "예외가 안 났습니다")
        except ValueError:
            check(f"{label_text} 는 예외", True)

    try:
        analyze([], NAMES)
        check("빈 입력은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("빈 입력은 예외", True)

    try:
        analyze(_blobs([[0.0, 0.0, 0.0]], 5, 1.0), ["aged_ratio_25y"])
        check("지표 1개는 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("지표 1개는 예외", True)

    single = analyze(blobs, NAMES, k=1)
    check("k=1 은 전부 한 군집", len(single.profiles) == 1)
    check("k=1 실루엣은 0", single.silhouette == 0.0, str(single.silhouette))

    print()
    print("표 렌더링·직렬화")
    check("k_table", bool(k_table(auto)))
    check("k 지정 시 k_table 안내", "건너" in k_table(first))
    check("cluster_table", bool(cluster_table(result)))
    try:
        json.dumps(result.to_dict(), ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    print()
    print(f"    {result.summary}")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
