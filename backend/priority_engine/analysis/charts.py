from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dataset  # noqa: E402
import paths  # noqa: E402
from analysis import cluster as cluster_mod  # noqa: E402
from analysis import pca as pca_mod  # noqa: E402
from analysis import profile as profile_mod  # noqa: E402

# 팔레트는 검증된 참조 팔레트를 값 변경 없이 그대로 씁니다. 재배색하면 색약
# 판별 가능성(CVD ΔE)을 다시 검증해야 하는데 그 스크립트를 이 환경에서 돌릴 수
# 없으므로, 문서에 통과 결과가 명시된 조합만 사용합니다.
#
# 범주형은 앞 3개 슬롯만 씁니다. 그 3개가 all-pairs 게이트를 두 모드 모두
# 통과합니다(CVD ΔE 9.2 / 일반시야 24.0, light). 4번째부터는 노랑↔주황이
# 붙어 all-pairs 기준에서 떨어지므로 군집 수가 3을 넘으면 색을 늘리지 않고
# 회색으로 접습니다.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SERIES_OTHER = "#898781"

# 순차(크기) — 파랑 한 색, 밝음→어두움. 무지개는 쓰지 않습니다.
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# 발산(극성) — 파랑↔빨강 + 중립 회색 중간점. 상관·효과크기처럼 0을 기준으로
# 부호가 뒤집히는 값에만 씁니다.
DIVERGING_LOW = "#2a78d6"
DIVERGING_MID = "#f0efec"
DIVERGING_HIGH = "#d03b3b"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# 이 환경에 한글 폰트가 이것뿐입니다. Noto CJK 는 변형(JP/HK)이 달라도 한글
# 글리프를 공유하므로 누락 없이 렌더됩니다(글리프 누락 경고 0건 확인).
FONT_CANDIDATES = ["Noto Sans CJK JP", "Noto Sans CJK KR", "NanumGothic", "Malgun Gothic"]

DPI = 170

# 히스토그램 구간 수. 229개 표본에 20개면 한 칸에 10개 남짓입니다.
BINS = 20


class MatplotlibMissing(RuntimeError):
    pass


def setup():
    try:
        import matplotlib
    except ImportError as error:
        raise MatplotlibMissing(
            "matplotlib 이 없습니다. pip install matplotlib"
        ) from error

    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt

    for candidate in FONT_CANDIDATES:
        try:
            font_manager.findfont(candidate, fallback_to_default=False)
        except Exception:  # noqa: BLE001
            continue
        plt.rcParams["font.family"] = candidate
        break

    # 마이너스 기호가 한글 폰트에서 두부(□)로 나오는 것을 막습니다.
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK,
            "text.color": INK,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.linewidth": 0.8,
            "font.size": 9,
        }
    )
    return plt


def _diverging(value: float, limit: float) -> str:
    # 0 을 중립 회색으로 두고 양쪽으로 갈라지게 섞습니다. 중간점에 색조를 두면
    # "값이 없음"이 색으로 읽히지 않습니다.
    ratio = 0.0 if limit <= 0 else max(-1.0, min(1.0, value / limit))
    target = DIVERGING_HIGH if ratio >= 0 else DIVERGING_LOW
    weight = abs(ratio)

    def mix(a: str, b: str, t: float) -> str:
        pairs = [(int(a[i : i + 2], 16), int(b[i : i + 2], 16)) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in pairs)

    return mix(DIVERGING_MID, target, weight)


def _short(name: str) -> str:
    # 축 눈금에는 괄호 설명을 뗍니다. 글자수로 자르면 "대구경 비율 (500m" 처럼
    # 끊겨서 뜻이 사라집니다.
    return dataset.label(name).split(" (")[0]


def _thin_axes(ax, axis: str = "y") -> None:
    ax.grid(axis=axis, linestyle="-", alpha=0.9, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _explain(fig, conclusion: str, reading: str) -> None:
    # 해설을 그림 파일 안에 박습니다. 이 PNG 들은 발표·공유 자료로 단독 유통되므로
    # 별도 문서에 해설을 두면 그림과 분리되어 전달되지 않습니다. 통계 용어와
    # 숫자 기준(r 0.8, d ±0.5, 실루엣 0.5 등)은 전부 여기서 풀이합니다.
    # 줄바꿈은 \n 으로 직접 넣습니다. wrap=True 는 그림 폭에 따라 결과가 달라져
    # 재실행 결정성(같은 입력 → 같은 파일)이 깨집니다.
    fig.text(
        0.01, -0.02, "→ " + conclusion,
        ha="left", va="top", fontsize=10.5, color=INK, fontweight="bold",
    )
    # 결론(1줄)이 차지하는 높이만큼 내려 시작합니다. bbox_inches="tight" 가
    # 음수 y 텍스트까지 캔버스에 포함시킵니다.
    fig.text(
        0.01, -0.06, reading,
        ha="left", va="top", fontsize=8.5, color=INK_SECONDARY, linespacing=1.55,
    )


# ------------------------------------------------------------------ 차트 5종


def chart_distributions(plt, rows: list[dict], names: list[str], out: Path) -> Path:
    stats = {stat.name: stat for stat in profile_mod.profile(rows, names)}
    columns = 3
    rows_count = (len(names) + columns - 1) // columns
    fig, axes = plt.subplots(
        rows_count, columns, figsize=(11, 3.1 * rows_count), constrained_layout=True
    )
    flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for index, name in enumerate(names):
        ax = flat[index]
        stat = stats[name]
        values = dataset.observed(rows, name)

        if not values:
            ax.text(
                0.5, 0.5, "전부 결측", ha="center", va="center",
                color=INK_MUTED, fontsize=11, transform=ax.transAxes,
            )
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f"{dataset.label(name)}\n0/{stat.total} 관측", fontsize=9.5)
            for side in ("top", "right", "left", "bottom"):
                ax.spines[side].set_visible(False)
            continue

        ax.hist(values, bins=BINS, color=SERIES[0], edgecolor=SURFACE, linewidth=0.8)
        ax.axvline(stat.median, color=INK_SECONDARY, linestyle="--", linewidth=1.2)

        # 막대 위로 여유를 만들고 그 안에 라벨을 둡니다. 막대 높이에 맞춰
        # 배치하면 최고 막대와 겹칩니다.
        top = ax.get_ylim()[1]
        ax.set_ylim(0, top * 1.14)
        ax.annotate(
            f"중앙 {stat.median:.3f}",
            xy=(stat.median, 1.0), xycoords=("data", "axes fraction"),
            xytext=(4, -11), textcoords="offset points",
            color=INK_SECONDARY, fontsize=8,
        )
        ax.set_title(
            f"{dataset.label(name)}\n"
            f"{stat.count}/{stat.total} 관측 · 왜도 {stat.skew:+.2f} · 0비율 {stat.zero_share:.0%}",
            fontsize=9.5,
        )
        ax.set_ylabel("시군구 수")
        _thin_axes(ax)

    for extra in flat[len(names) :]:
        extra.set_visible(False)

    # constrained_layout 이 suptitle 자리를 잡아 줍니다. y 를 직접 주면
    # 첫 행 소제목과 겹칩니다.
    fig.suptitle(f"지표별 분포 (기초자치단체 {len(rows)}개 전수)", fontsize=13)
    _explain(
        fig,
        "지표마다 단위·분포 모양이 제각각이라, 원값 대신 전국 등수(백분위)로 바꿔서 점수를 만듭니다.",
        "읽는 법: 막대 = 그 값 구간에 속한 시군구 수, 점선 = 중앙값(전국 한가운데 값).\n"
        "용어: 왜도 = 분포가 한쪽으로 쏠린 정도(0이면 좌우 대칭, +면 오른쪽 꼬리) · "
        "0비율 = 값이 정확히 0인 시군구 비율(침하처럼 '대부분 0, 일부만 큼'인 지표를 표시).",
    )
    path = out / "01_지표분포.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_missing(plt, rows: list[dict], names: list[str], out: Path) -> Path:
    report = profile_mod.missing_report(rows, names)
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13, 5.2), constrained_layout=True,
        gridspec_kw={"width_ratios": [1, 1.35]},
    )

    labels = list(report.per_indicator)
    counts = [report.per_indicator[name] for name in labels]
    positions = range(len(labels))
    left.barh(positions, counts, color=SERIES[0], height=0.62)
    left.set_yticks(list(positions))
    left.set_yticklabels([dataset.label(name) for name in labels], fontsize=8.5)
    left.invert_yaxis()
    left.set_xlim(0, report.total * 1.12)
    left.set_xlabel(f"결측 시군구 수 (전체 {report.total})")
    for position, count in zip(positions, counts):
        left.annotate(
            f"{count}  ({count / report.total:.0%})",
            xy=(count, position), xytext=(5, 0), textcoords="offset points",
            va="center", color=INK_SECONDARY, fontsize=8.5,
        )
    left.set_title("지표별 결측", fontsize=11)
    _thin_axes(left, axis="x")

    # 결측 지표별로 묶어 그립니다. 한 줄에 "A 결측 → B" 를 다 쓰면 한글 라벨이
    # 길어 잘리므로, 축에는 비교 지표만 쓰고 결측 지표는 그룹 제목으로 뺍니다.
    groups = [
        (target, report.mnar.get(target, []))
        for target in report.partially_missing
        if report.mnar.get(target)
    ]
    checks = [check for _, group in groups for check in group]
    if checks:
        limit = max(abs(check.effect_size) for check in checks) or 1.0

        # 그룹 사이에 빈 줄을 하나 넣어 제목 자리를 만듭니다.
        slots: list[tuple[int, object]] = []
        row = 0
        for index, (target, group) in enumerate(groups):
            if index:
                row += 1
            slots.append((row, target))
            row += 1
            for check in group:
                slots.append((row, check))
                row += 1

        for position, item in slots:
            if isinstance(item, str):
                continue
            right.barh(
                position, item.effect_size,
                color=_diverging(item.effect_size, limit),
                edgecolor=BASELINE, linewidth=0.6, height=0.66,
            )

        ticks = [position for position, item in slots if not isinstance(item, str)]
        right.set_yticks(ticks)
        right.set_yticklabels(
            [dataset.label(item.other) for _, item in slots if not isinstance(item, str)],
            fontsize=8.5,
        )
        right.set_ylim(row - 0.5, -1.2)
        right.axvline(0, color=BASELINE, linewidth=1.0)
        for sign in (-1, 1):
            right.axvline(
                sign * profile_mod.MNAR_EFFECT_THRESHOLD,
                color=INK_MUTED, linestyle=":", linewidth=1.0,
            )

        # 라벨이 축 눈금과 겹치지 않도록 양쪽에 여유를 둡니다.
        low = min(0.0, min(check.effect_size for check in checks))
        high = max(0.0, max(check.effect_size for check in checks))
        span = (high - low) or 1.0
        right.set_xlim(low - span * 0.22, high + span * 0.16)

        for position, item in slots:
            if isinstance(item, str):
                right.annotate(
                    f"{dataset.label(item)} 결측 {report.per_indicator[item]}개",
                    xy=(right.get_xlim()[0], position),
                    xytext=(2, 0), textcoords="offset points",
                    va="center", ha="left", color=INK, fontsize=9,
                    fontweight="bold",
                )
                continue
            offset = 5 if item.effect_size >= 0 else -5
            right.annotate(
                f"{item.effect_size:+.2f}",
                xy=(item.effect_size, position),
                xytext=(offset, 0), textcoords="offset points",
                va="center", ha="left" if offset > 0 else "right",
                color=INK_SECONDARY, fontsize=8.5,
                fontweight="bold" if item.concerning else "normal",
            )
        right.set_xlabel("Cohen's d  (점선 ±0.5 = 다른 모집단 기준)")
        right.set_title(
            "결측이 무작위인가 — 결측군 vs 관측군의 다른 지표 평균 차이", fontsize=11
        )
        right.text(
            0.5, -0.16,
            "양수 = 결측군이 더 위험 (중앙값 패딩이 과소평가)   ·   "
            "음수 = 덜 위험 (과대평가)",
            transform=right.transAxes, ha="center", color=INK_SECONDARY, fontsize=8.5,
        )
        _thin_axes(right, axis="x")
    else:
        right.text(0.5, 0.5, "부분 결측 지표가 없습니다", ha="center", va="center",
                   color=INK_MUTED, transform=right.transAxes)
        right.set_axis_off()

    fig.suptitle("빈 값(결측)이 어디에, 어떤 지역에 몰려 있는가", fontsize=13)
    concerning = sum(1 for check in checks if check.concerning)
    _explain(
        fig,
        (
            f"빈칸은 아무 데나 뚫린 게 아닙니다 — 기준(±0.5)을 넘는 차이가 {concerning}건. "
            "값이 빈 시군구들은 성격이 다른 집단이라, 중앙값으로 채운 점수는 그만큼 보수적으로 읽어야 합니다."
            if concerning
            else "빈칸이 특정 성격의 지역에 몰린 징후는 기준(±0.5)을 넘지 않았습니다."
        ),
        "읽는 법: 왼쪽 = 지표별로 값이 없는 시군구 수. 오른쪽 = '값이 빈 그룹'과 '있는 그룹'이 다른 지표에서 평균이 얼마나 다른가.\n"
        "용어: Cohen's d = 두 그룹의 평균 차이를 흩어짐(표준편차)으로 나눈 값. ±0.5(점선)를 넘으면 '뚜렷이 다른 집단'으로 봅니다.\n"
        "빨간 막대(+) = 빈 곳이 오히려 더 위험한 지역 → 빈칸을 평균으로 채우면 위험을 낮게 볼 수 있음. 파란 막대(−)는 그 반대.",
    )
    path = out / "02_결측패턴.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_correlation(plt, rows: list[dict], names: list[str], out: Path) -> Path:
    available = dataset.available_indicators(rows, names)
    matrix, counts = profile_mod.correlation_matrix(rows, available)
    size = len(available)

    fig, ax = plt.subplots(figsize=(1.5 * size + 3.0, 1.5 * size + 2.2),
                           constrained_layout=True)

    # 하삼각만 그립니다. 대각(항상 1.000)과 상삼각(하삼각의 복사)은 정보가 없는데
    # 대각이 최대 채도라 정작 중요한 값을 시각적으로 눌러 버립니다.
    for row in range(size):
        for col in range(row):
            value = matrix[available[row]][available[col]]
            ax.add_patch(
                plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=_diverging(value, 1.0),
                    edgecolor=SURFACE, linewidth=2.0,
                )
            )
            high = abs(value) >= profile_mod.HIGH_CORRELATION
            ax.text(
                col, row,
                f"{value:.3f}\nn={counts[available[row]][available[col]]}",
                ha="center", va="center", fontsize=9,
                color=INK if abs(value) < 0.6 else "#ffffff",
                fontweight="bold" if high else "normal",
            )

    ax.set_aspect("equal")
    ax.set_xlim(-0.6, size - 1.4)
    ax.set_ylim(size - 0.4, 0.4)
    ax.set_xticks(range(size - 1))
    ax.set_yticks(range(1, size))
    ax.set_xticklabels([_short(n) for n in available[: size - 1]], rotation=24,
                       ha="right", fontsize=9)
    ax.set_yticklabels([_short(n) for n in available[1:]], fontsize=9)
    ax.set_title(
        "지표 간 상관 (쌍별 완전관측 — 쌍마다 n 이 다릅니다)\n"
        f"굵은 값은 |r| >= {profile_mod.HIGH_CORRELATION}: 같은 것을 재고 있을 수 있음",
        fontsize=11.5,
    )
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)

    strongest = None
    for row in range(size):
        for col in range(row):
            value = matrix[available[row]][available[col]]
            if strongest is None or abs(value) > abs(strongest[2]):
                strongest = (available[row], available[col], value)
    _explain(
        fig,
        (
            f"가장 강한 쌍은 '{_short(strongest[0])} ↔ {_short(strongest[1])}' "
            f"(r={strongest[2]:.2f}) — {profile_mod.HIGH_CORRELATION} 이상인 쌍은 "
            "사실상 같은 정보를 두 번 세는 것이라, 가중치 설계에서 이중 반영을 피했습니다."
            if strongest is not None
            else "비교 가능한 지표 쌍이 없습니다."
        ),
        "읽는 법: 칸의 숫자 = 상관계수 r (두 지표가 같이 움직이는 정도, -1~+1). "
        "빨강 = 한쪽이 크면 다른 쪽도 큼, 파랑 = 반대로 움직임, 회색 = 무관.\n"
        "n = 두 지표를 모두 관측한 시군구 수 (결측 때문에 쌍마다 다름 — n 이 작을수록 r 을 약하게 믿어야 합니다). "
        "대각선·위쪽 절반은 같은 정보의 복사라 생략했습니다.",
    )
    path = out / "03_상관히트맵.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_pca(plt, result, out: Path) -> Path:
    count = len(result.eigenvalues)
    fig, (scree, cumulative, loadings) = plt.subplots(
        1, 3, figsize=(15, 4.6), constrained_layout=True,
        gridspec_kw={"width_ratios": [1, 1, 1.25]},
    )

    labels = [f"PC{i + 1}" for i in range(count)]
    scree.bar(labels, result.eigenvalues, color=SERIES[0], width=0.62)
    scree.axhline(pca_mod.KAISER_THRESHOLD, color=INK_SECONDARY,
                  linestyle="--", linewidth=1.2)
    scree.annotate("카이저 기준 1.0", xy=(count - 0.6, pca_mod.KAISER_THRESHOLD),
                   xytext=(0, 5), textcoords="offset points",
                   color=INK_SECONDARY, fontsize=8.5, ha="right")
    # 카이저 기준선(1.0) 근처 막대의 값 라벨이 선과 겹치므로 위로 여유를 둡니다.
    scree.set_ylim(0, max(max(result.eigenvalues), pca_mod.KAISER_THRESHOLD) * 1.22)
    for index, value in enumerate(result.eigenvalues):
        # 막대 끝이 기준선 바로 아래면 라벨이 선에 겹칩니다. 그럴 때는 선 위에
        # 올려 둡니다 (PC3 = 0.93, 기준선 1.0 이 실제로 그 경우였습니다).
        gap = pca_mod.KAISER_THRESHOLD - value
        anchor = pca_mod.KAISER_THRESHOLD if 0 <= gap < 0.18 else value
        scree.annotate(f"{value:.2f}", xy=(index, anchor), xytext=(0, 4),
                       textcoords="offset points", ha="center",
                       color=INK_SECONDARY, fontsize=8.5)
    scree.set_ylabel("고유값")
    scree.set_title(f"성분별 고유값 (완전관측 {result.sample_size}개)", fontsize=11)
    _thin_axes(scree)

    shares = [share * 100 for share in result.cumulative]
    cumulative.plot(labels, shares, color=SERIES[0], linewidth=2.0,
                    marker="o", markersize=8, markeredgecolor=SURFACE,
                    markeredgewidth=2.0)
    cumulative.axhline(pca_mod.EFFECTIVE_VARIANCE * 100, color=INK_SECONDARY,
                       linestyle="--", linewidth=1.2)
    cumulative.annotate(f"{pca_mod.EFFECTIVE_VARIANCE:.0%}",
                        xy=(0, pca_mod.EFFECTIVE_VARIANCE * 100),
                        xytext=(2, 5), textcoords="offset points",
                        color=INK_SECONDARY, fontsize=8.5)
    # 누적 곡선은 오른쪽 아래가 비어 있습니다. 라벨을 위에 두면 90% 기준선과
    # 겹치므로 아래로 내립니다.
    for index, share in enumerate(shares):
        cumulative.annotate(f"{share:.0f}%", xy=(index, share), xytext=(0, -15),
                            textcoords="offset points", ha="center", va="top",
                            color=INK_SECONDARY, fontsize=8.5)
    cumulative.set_ylim(0, 108)
    cumulative.set_ylabel("누적 설명분산 (%)")
    cumulative.set_title(
        f"누적 설명분산 — {pca_mod.EFFECTIVE_VARIANCE:.0%}에 성분 "
        f"{result.effective_dimension}개 필요",
        fontsize=11,
    )
    _thin_axes(cumulative)

    shown = min(3, count)
    for row, name in enumerate(result.names):
        for col in range(shown):
            value = result.loading(row, col)
            loadings.add_patch(
                plt.Rectangle((col - 0.5, row - 0.5), 1, 1,
                              facecolor=_diverging(value, 1.0),
                              edgecolor=SURFACE, linewidth=2.0)
            )
            loadings.text(col, row, f"{value:.2f}", ha="center", va="center",
                          fontsize=9,
                          color=INK if abs(value) < 0.6 else "#ffffff",
                          fontweight="bold" if abs(value) >= 0.7 else "normal")
    loadings.set_xlim(-0.5, shown - 0.5)
    loadings.set_ylim(len(result.names) - 0.5, -0.5)
    loadings.set_xticks(range(shown))
    loadings.set_xticklabels(labels[:shown])
    loadings.set_yticks(range(len(result.names)))
    loadings.set_yticklabels([dataset.label(n) for n in result.names], fontsize=8.5)
    loadings.set_title("적재량 (지표↔성분 상관)\n굵은 값 |적재| >= 0.7", fontsize=11)
    for side in ("top", "right", "left", "bottom"):
        loadings.spines[side].set_visible(False)
    loadings.tick_params(length=0)

    fig.suptitle(
        f"지표 {len(result.names)}개가 실제로 몇 덩어리의 정보인가 — 주성분분석(PCA)",
        fontsize=13,
    )
    _explain(
        fig,
        f"지표는 {len(result.names)}개지만 서로 겹쳐서, 독립적인 정보는 "
        f"약 {result.effective_dimension}덩어리입니다 (전체 변동의 "
        f"{pca_mod.EFFECTIVE_VARIANCE:.0%}를 성분 {result.effective_dimension}개가 설명).",
        "용어: 주성분분석 = 겹치는 지표들을 몇 개의 '공통 축'으로 압축해 보는 방법. 고유값 = 그 축이 담은 정보량 — "
        "1.0(점선)이면 지표 하나 몫, 그보다 크면 여러 지표가 공유하는 축.\n"
        "읽는 법: 왼쪽 = 축별 정보량 (막대가 급히 꺾이는 지점까지가 의미 있는 축) · 가운데 = 축을 몇 개 쓰면 정보의 90%가 되는가 · "
        "오른쪽 = 각 축이 어느 지표와 연결되는가 (빨강 = 같은 방향, 파랑 = 반대 방향, 굵은 글씨 = 강한 연결).",
    )
    path = out / "04_주성분분석.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_clusters(plt, rows: list[dict], result, out: Path) -> Path:
    # 축을 주성분이 아니라 실제 지표로 잡습니다. PCA 는 완전관측 121개, 군집은
    # 중앙값 대체로 229개라 표본이 달라 같은 평면에 놓을 수 없습니다.
    x_name = "concrete_ratio"
    y_name = "aged_ratio_25y"

    fig, (scatter, means) = plt.subplots(
        1, 2, figsize=(13.5, 5.4), constrained_layout=True,
        gridspec_kw={"width_ratios": [1.15, 1]},
    )

    def colour(label: int) -> str:
        return SERIES[label] if label < len(SERIES) else SERIES_OTHER

    for item in result.profiles:
        members = [
            row for index, row in enumerate(rows) if result.labels[index] == item.label
        ]
        xs = [row.get(x_name) for row in members]
        ys = [row.get(y_name) for row in members]
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        scatter.scatter(
            [p[0] for p in pairs], [p[1] for p in pairs],
            s=48, color=colour(item.label), edgecolor=SURFACE, linewidth=1.4,
            label=f"유형 {item.label} ({item.size}개)", zorder=3,
        )

    hidden = sum(
        1 for row in rows if row.get(x_name) is None or row.get(y_name) is None
    )
    scatter.set_xlabel(dataset.label(x_name))
    scatter.set_ylabel(dataset.label(y_name))
    weak = result.silhouette < cluster_mod.STRONG_STRUCTURE
    scatter.set_title(
        f"군집 {result.k}개 — 실루엣 {result.silhouette:.3f}"
        f"{' (약한 구조: 경향성이지 경계가 아님)' if weak else ''}\n"
        + (
            f"두 축 중 하나가 결측인 {hidden}개는 표시되지 않음 "
            "(군집에는 중앙값 대체로 포함)"
            if hidden
            else "전 구역 표시"
        ),
        fontsize=11,
    )
    scatter.legend(frameon=False, fontsize=8.5, loc="lower right")
    _thin_axes(scatter, axis="both")

    indicators = result.names
    width = 0.8 / max(len(result.profiles), 1)
    for slot, item in enumerate(result.profiles):
        offsets = [index + slot * width - 0.4 + width / 2 for index in range(len(indicators))]
        means.bar(
            offsets, [item.means[name] for name in indicators],
            width=width * 0.88, color=colour(item.label),
            label=f"유형 {item.label}",
        )
    means.set_xticks(range(len(indicators)))
    means.set_xticklabels([_short(n) for n in indicators],
                          rotation=20, ha="right", fontsize=8.5)
    means.set_ylabel("유형 내 평균")
    means.set_title("유형별 지표 평균", fontsize=11)
    means.legend(frameon=False, fontsize=8.5)
    _thin_axes(means)

    fig.suptitle("비슷한 시군구끼리 자동으로 묶으면 — 군집분석(k-means)", fontsize=13)
    _explain(
        fig,
        f"전국 시군구는 {result.k}가지 유형으로 나뉘는 경향을 보입니다"
        + (
            f" — 다만 실루엣 {result.silhouette:.2f}는 '뚜렷한 경계'가 아니라 "
            "'경향' 수준이므로 유형을 절대 분류처럼 쓰면 안 됩니다."
            if result.silhouette < cluster_mod.STRONG_STRUCTURE
            else f" (실루엣 {result.silhouette:.2f} — 비교적 뚜렷한 구분)."
        ),
        "용어: 군집분석 = 성격이 비슷한 시군구끼리 컴퓨터가 자동으로 묶는 방법. 실루엣(0~1) = 묶음이 얼마나 뚜렷한가 — "
        "0.5 이상이면 뚜렷, 0.25~0.5는 약한 경향, 그 밑은 사실상 구분 없음.\n"
        "읽는 법: 왼쪽 = 대표 지표 2개 평면에서 유형별 분포 (색 = 유형) · 오른쪽 = 유형별 지표 평균 — "
        "어떤 유형이 '노후·위험형'이고 어떤 유형이 '양호형'인지 여기서 읽습니다.",
    )
    path = out / "05_군집.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def render_all(
    rows: list[dict],
    names: list[str] | None = None,
    out_dir: Path | None = None,
    k: int | None = None,
) -> list[Path]:
    plt = setup()
    names = list(names or dataset.INDICATORS)
    out = out_dir or paths.CHARTS
    out.mkdir(parents=True, exist_ok=True)

    made = [
        chart_distributions(plt, rows, names, out),
        chart_missing(plt, rows, names, out),
        chart_correlation(plt, rows, names, out),
    ]

    try:
        pca_result = pca_mod.analyze(rows, names)
    except ValueError:
        pca_result = None
    if pca_result is not None:
        made.append(chart_pca(plt, pca_result, out))

    try:
        cluster_result = cluster_mod.analyze(rows, names, k=k)
    except ValueError:
        cluster_result = None
    if cluster_result is not None:
        made.append(chart_clusters(plt, rows, cluster_result, out))

    return made


# ---------------------------------------------------------------- 자체 검증


def _rows(count: int, seed: int = 8181) -> list[dict]:
    state = seed

    def nxt() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    built = []
    for index in range(count):
        built.append(
            {
                "sgg_code": f"{10000 + index}",
                "full_name": f"테스트시 {index}구",
                "sido_name": "테스트도",
                "aged_ratio_25y": nxt() if index % 4 else None,
                "concrete_ratio": nxt(),
                "sewer_subsidence_per_km": None,
                "combined_sewer_ratio": nxt(),
                "unconnected_ratio": nxt() * 0.3,
                "large_diameter_ratio": nxt(),
            }
        )
    return built


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("팔레트 (검증된 참조 팔레트를 값 변경 없이 사용)")
    check("범주형 3슬롯", len(SERIES) == 3, str(SERIES))
    check("전부 6자리 hex", all(len(c) == 7 and c[0] == "#" for c in SERIES))
    check("순차는 한 색 램프", len(SEQUENTIAL) == 7)
    check("발산 중간점이 중립 회색", DIVERGING_MID == "#f0efec")
    check(
        "발산 양극이 서로 다른 색조",
        DIVERGING_LOW != DIVERGING_HIGH and DIVERGING_MID not in (DIVERGING_LOW, DIVERGING_HIGH),
    )

    print()
    print("발산 색 섞기")
    check("0 은 중립 회색", _diverging(0.0, 1.0) == DIVERGING_MID, _diverging(0.0, 1.0))
    check("최대는 고극 색", _diverging(1.0, 1.0) == DIVERGING_HIGH, _diverging(1.0, 1.0))
    check("최소는 저극 색", _diverging(-1.0, 1.0) == DIVERGING_LOW, _diverging(-1.0, 1.0))
    check("범위를 넘으면 포화", _diverging(9.0, 1.0) == DIVERGING_HIGH)
    check("limit 0 이면 중립", _diverging(5.0, 0.0) == DIVERGING_MID)
    check(
        "부호가 다르면 색도 다름",
        _diverging(0.7, 1.0) != _diverging(-0.7, 1.0),
    )

    print()
    try:
        plt = setup()
    except MatplotlibMissing as error:
        print(f"  matplotlib 이 없어 렌더링 검증을 건너뜁니다: {error}")
        print()
        print("전부 통과" if not failures else f"실패 {failures}건")
        return 1 if failures else 0

    print("폰트")
    check("한글 폰트가 잡힘", plt.rcParams["font.family"][0] in FONT_CANDIDATES,
          str(plt.rcParams["font.family"]))
    check("마이너스 기호 두부 방지", plt.rcParams["axes.unicode_minus"] is False)

    print()
    print("렌더링 (임시 디렉터리)")
    import tempfile

    rows = _rows(120)
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        made = render_all(rows, out_dir=out, k=3)

        check("차트 5개 생성", len(made) == 5, str([p.name for p in made]))
        check("전부 파일로 존재", all(p.exists() for p in made))
        check("빈 파일 없음", all(p.stat().st_size > 5000 for p in made),
              str([p.stat().st_size for p in made]))
        check("PNG 시그니처", all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in made))
        check(
            "파일명이 순서를 가짐",
            [p.name[:2] for p in made] == ["01", "02", "03", "04", "05"],
            str([p.name for p in made]),
        )

        # 같은 입력이면 같은 그림이어야 합니다. 군집 시드가 흔들리면 유형 색이
        # 실행마다 바뀌어 발표 자료와 어긋납니다.
        again = render_all(rows, out_dir=out / "again", k=3)
        sizes_first = [p.stat().st_size for p in made]
        sizes_again = [p.stat().st_size for p in again]
        check("재실행 시 같은 결과", sizes_first == sizes_again,
              f"{sizes_first} vs {sizes_again}")

    print()
    print("경계 조건")
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        # 전 지표 결측이면 PCA·군집이 불가능합니다. 죽지 않고 나머지만 그려야 합니다.
        blank = [
            dict(row, aged_ratio_25y=None, concrete_ratio=None,
                 combined_sewer_ratio=None, unconnected_ratio=None,
                 large_diameter_ratio=None)
            for row in _rows(30)
        ]
        made = render_all(blank, out_dir=out)
        check("전 지표 결측이면 3개만 생성", len(made) == 3, str([p.name for p in made]))
        check("그래도 파일은 정상", all(p.stat().st_size > 3000 for p in made))

    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary) / "없는" / "하위"
        made = render_all(_rows(60), out_dir=out, k=2)
        check("출력 디렉터리를 만들어 줌", out.exists() and len(made) == 5)

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
