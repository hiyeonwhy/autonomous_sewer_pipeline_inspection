"""발표용 차트 6종을 다시 그립니다.

    python3 -m analysis.presentation            # 자체 검증
    python3 -m analysis.presentation --build    # ~/docs/charts 에 생성
    python3 -m analysis.presentation --build --out <디렉터리>

`analysis/charts.py` 는 **데이터 탐색(EDA)** 용이고 이 모듈은 **발표** 용입니다.
같은 팔레트·폰트·해설 규칙을 charts.py 에서 그대로 가져다 씁니다.

왜 따로 만들었나: 이 6장은 원래 일회성으로 만들어져 재생성 경로가 없었습니다.
그래서 가중치를 개정한 뒤에도 옛 수치를 보여주는 채로 남아 있었고, 그 상태로
발표하면 틀린 숫자를 말하게 됩니다. 이제 한 명령으로 다시 그려집니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json

import dataset  # noqa: E402
import paths  # noqa: E402
from analysis.charts import (  # noqa: E402
    BASELINE,
    DIVERGING_HIGH,
    DIVERGING_LOW,
    DIVERGING_MID,
    DPI,
    GRID,
    INK,
    INK_MUTED,
    INK_SECONDARY,
    SEQUENTIAL,
    SERIES,
    SURFACE,
    MatplotlibMissing,
    _explain,
    setup,
)
from analysis.profile import correlation_matrix  # noqa: E402

# 대시보드 지도와 같은 등급 색입니다(frontend/src/components/priority/geo.ts).
# 등급은 1~5 순서가 있는 값이라 범주형 색이 아니라 **한 색의 밝기 단계**를 씁니다.
GRADE_COLORS = {
    5: "#0f458f",
    4: "#3d6fb5",
    3: "#7d9fd1",
    2: "#b6c9e4",
    1: "#dde5f2",
}
GRADE_LABELS = {5: "최우선", 4: "우선", 3: "보통", 2: "관찰", 1: "양호"}

# 기여도 스택에 쓰는 색. 지표가 5개인데 검증된 범주형 슬롯은 3개뿐이라
# (charts.py 주석 참조) 색을 늘리지 않고 **가중치 순서대로 한 색의 단계**를
# 씁니다. 가중치는 크기 순서가 있으므로 순차 램프가 오히려 맞습니다.
# 램프에서 한 칸씩 띄워 고릅니다. 붙여 고르면 인접 조각이 구분되지 않습니다.
STACK_STEPS = [SEQUENTIAL[6], SEQUENTIAL[4], SEQUENTIAL[2], SEQUENTIAL[1], SEQUENTIAL[0]]


def load(data_dir: Path | None = None) -> tuple[dict, list[dict]]:
    """산정 결과를 읽습니다. 반환: (meta, districts)"""
    path = (data_dir or paths.DATA) / paths.PRIORITY_SCORES_FILE
    if not path.exists():
        raise SystemExit(
            f"산정 결과가 없습니다: {path}\n  python3 run.py 를 먼저 실행하세요."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["meta"], payload["districts"]


def _spread(values: list[float], min_gap: float) -> list[float]:
    """겹치는 라벨 위치를 최소 간격만큼 밀어냅니다.

    순위가 붙어 있는 구역이 여러 개면 라벨이 서로 덮어 읽을 수 없게 됩니다.
    입력 순서를 유지한 채 위치만 조정해서 돌려줍니다.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    adjusted = list(values)
    for position, index in enumerate(order):
        if position == 0:
            continue
        previous = adjusted[order[position - 1]]
        if adjusted[index] - previous < min_gap:
            adjusted[index] = previous + min_gap
    return adjusted


def _bare(ax) -> None:
    """축 장식을 걷어냅니다. 데이터가 주인공이어야 합니다."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0, labelsize=9, colors=INK_SECONDARY)


# ------------------------------------------------------------------ 차트 6종


def chart_grades(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """등급 분포. 순서 있는 5개 구간이라 가로 막대로 눕힙니다."""
    counts = {grade: 0 for grade in (5, 4, 3, 2, 1)}
    for row in districts:
        counts[row["grade"]] = counts.get(row["grade"], 0) + 1
    total = sum(counts.values())

    fig, ax = plt.subplots(figsize=(9, 3.4), constrained_layout=True)
    grades = [5, 4, 3, 2, 1]
    positions = range(len(grades))
    values = [counts[g] for g in grades]

    ax.barh(
        list(positions), values,
        color=[GRADE_COLORS[g] for g in grades],
        height=0.62, zorder=3,
    )
    for index, (grade, value) in enumerate(zip(grades, values)):
        ax.text(
            value + total * 0.012, index,
            f"{value}개  ({value / total:.0%})",
            va="center", ha="left", fontsize=9.5, color=INK_SECONDARY,
        )

    ax.set_yticks(list(positions))
    ax.set_yticklabels([f"{g}등급 {GRADE_LABELS[g]}" for g in grades], fontsize=10)
    ax.set_xlim(0, max(values) * 1.28)
    ax.set_xticks([])
    ax.invert_yaxis()
    _bare(ax)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.set_title(
        f"점검 우선순위 등급 분포 — 전국 {total}개 기초자치단체",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )

    top = [row for row in districts if row["grade"] == 5][:3]
    _explain(
        fig,
        f"5등급(최우선)은 {counts[5]}개이고 최상위는 "
        + " · ".join(row["full_name"] for row in top)
        + " 입니다.",
        "읽는 법 — 등급은 절대 기준이 아니라 상대 배분입니다. 위험도 순으로 줄을 세운 뒤\n"
        "상위 10%를 5등급, 다음 20%를 4등급으로 자릅니다(40 · 20 · 10%가 이어집니다).\n"
        "따라서 '5등급이 늘었다'는 말은 성립하지 않습니다 — 개수는 항상 같고 구성만 바뀝니다.",
    )
    path = out / "01_등급분포.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_contributions(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """상위 12개 구역의 위험도를 지표별로 쪼갠 누적 막대."""
    names = list(meta["occurrence_weights"])
    display = meta["display_names"]
    top = districts[:12]

    fig, ax = plt.subplots(figsize=(10.5, 4.6), constrained_layout=True)
    positions = list(range(len(top)))
    bottom = [0.0] * len(top)

    for index, name in enumerate(names):
        values = [row["contributions"].get(name, 0.0) for row in top]
        ax.bar(
            positions, values, bottom=bottom,
            color=STACK_STEPS[index % len(STACK_STEPS)],
            width=0.68,
            label=f"{display.get(name, name)} ({meta['occurrence_weights'][name]:.2f})",
            # 조각 사이 2px 배경색 간격 — 경계가 붙어 보이지 않게.
            edgecolor=SURFACE, linewidth=1.6,
            zorder=3,
        )
        bottom = [b + v for b, v in zip(bottom, values)]

    for index, row in enumerate(top):
        ax.text(
            index, bottom[index] + max(bottom) * 0.02,
            f"{row['rank']}위",
            ha="center", va="bottom", fontsize=8.5, color=INK_MUTED,
        )

    ax.set_xticks(positions)
    ax.set_xticklabels(
        [row["full_name"].split(" ", 1)[-1] for row in top],
        rotation=38, ha="right", fontsize=9,
    )
    ax.set_ylabel("위험도", fontsize=9.5)
    ax.set_ylim(0, max(bottom) * 1.14)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    _bare(ax)
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=2,
        frameon=False, fontsize=8.5, labelcolor=INK_SECONDARY,
    )
    ax.set_title(
        "상위 12개 구역은 왜 위험한가 — 위험도를 지표별로 분해",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )

    lead = max(names, key=lambda n: sum(row["contributions"].get(n, 0.0) for row in top))
    _explain(
        fig,
        f"상위권을 가장 많이 끌어올린 지표는 {display.get(lead, lead)} 입니다.",
        "읽는 법 — 막대 하나가 그 구역의 위험도이고, 색 조각이 지표별 기여분입니다.\n"
        "조각 높이 = 그 지표의 전국 백분위 × 가중치 × 영향배율이라, 같은 지표라도\n"
        "구역마다 높이가 다릅니다. 범례의 괄호 안 숫자가 가중치입니다.\n"
        "정확도(%)를 주장하지 않는 대신 이렇게 근거를 전부 분해해서 보여줍니다.",
    )
    path = out / "02_기여도분해.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_model_shift(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """v1(단순 가중합) → v2(발생확률 × 영향도) 순위 이동."""
    from scoring.weighted import score_districts as score_v1

    merged, _ = dataset.merge(data_dir)
    v1 = {item.sgg_code: item.rank for item in score_v1(merged)}
    v2 = {row["sgg_code"]: row["rank"] for row in districts}
    names = {row["sgg_code"]: row["full_name"] for row in districts}

    shared = [code for code in v2 if code in v1]
    shifts = sorted(shared, key=lambda c: -abs(v2[c] - v1[c]))[:12]

    fig, ax = plt.subplots(figsize=(9.5, 5.4), constrained_layout=True)

    # 라벨은 점과 따로 배치합니다. 순위가 붙은 구역이 많아 점 위치에 그대로
    # 얹으면 서로 덮습니다(영천·화순·파주가 3위 차이 안에 몰려 있었습니다).
    befores = [v1[code] for code in shifts]
    afters = [v2[code] for code in shifts]
    label_gap = 9.0
    left_labels = _spread(befores, label_gap)
    right_labels = _spread(afters, label_gap)

    for index, code in enumerate(shifts):
        before, after = befores[index], afters[index]
        rose = after < before  # 순위 숫자가 작아짐 = 더 위험해짐
        color = SERIES[1] if rose else SERIES[0]
        ax.plot(
            [0, 1], [before, after],
            color=color, linewidth=2, zorder=3, solid_capstyle="round",
        )
        ax.scatter(
            [0, 1], [before, after],
            s=42, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=4,
        )
        # 라벨을 밀어냈으면 점과 라벨을 얇은 선으로 이어 줍니다.
        if abs(right_labels[index] - after) > 0.5:
            ax.plot(
                [1, 1.045], [after, right_labels[index]],
                color=BASELINE, linewidth=0.7, zorder=2,
            )
        ax.text(
            1.06, right_labels[index],
            f"{names[code].split(' ', 1)[-1]} {after}위",
            va="center", fontsize=8.5, color=INK_SECONDARY,
        )
        if abs(left_labels[index] - before) > 0.5:
            ax.plot(
                [-0.045, 0], [left_labels[index], before],
                color=BASELINE, linewidth=0.7, zorder=2,
            )
        ax.text(
            -0.06, left_labels[index], f"{before}위",
            va="center", ha="right", fontsize=8.5, color=INK_MUTED,
        )

    ax.set_xlim(-0.42, 1.82)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["v1 단순 가중합", "v2 발생확률 × 영향도"], fontsize=10)
    ax.invert_yaxis()
    ax.set_ylabel("전국 순위 (위쪽이 더 위험)", fontsize=9.5)
    ax.xaxis.grid(False)
    _bare(ax)
    ax.spines["left"].set_color(BASELINE)
    ax.set_title(
        "모델을 바꾸면 순위가 얼마나 움직이나 — 이동이 가장 큰 12개",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )
    # 색이 방향을 뜻하므로 범례가 필요합니다(색만으로 구분하지 않게 라벨도 함께).
    ax.plot([], [], color=SERIES[1], linewidth=2, label="더 위험해짐 (순위 상승)")
    ax.plot([], [], color=SERIES[0], linewidth=2, label="덜 위험해짐 (순위 하락)")
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=2,
        frameon=False, fontsize=8.5, labelcolor=INK_SECONDARY,
    )

    moved = sum(abs(v2[c] - v1[c]) for c in shared) / len(shared)
    _explain(
        fig,
        f"평균 {moved:.1f}위 움직였습니다. 모델 선택이 결론을 바꾼다는 뜻입니다.",
        "읽는 법 — 왼쪽이 지표를 그냥 더한 v1, 오른쪽이 발생확률과 영향도를 곱한 v2입니다.\n"
        "선이 위로 가면 v2에서 더 위험하게 평가된 구역입니다. 곱셈으로 바꾸면 '터질 확률'과\n"
        "'터졌을 때 피해'가 함께 큰 구역이 위로 올라옵니다 — 둘 중 하나만 큰 구역은 내려갑니다.",
    )
    path = out / "03_v1v2_순위이동.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_correlation(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """지표 간 상관 히트맵. 0을 기준으로 부호가 뒤집히므로 발산 팔레트."""
    from matplotlib.colors import LinearSegmentedColormap

    merged, _ = dataset.merge(data_dir)
    names = list(meta["occurrence_weights"]) + list(meta["impact"]["features"])
    display = meta["display_names"]
    matrix, _counts = correlation_matrix(merged, names)

    cmap = LinearSegmentedColormap.from_list(
        "diverging", [DIVERGING_LOW, DIVERGING_MID, DIVERGING_HIGH]
    )
    grid = [[matrix.get(a, {}).get(b) for b in names] for a in names]
    plotted = [[0.0 if v is None else v for v in line] for line in grid]

    fig, ax = plt.subplots(figsize=(8.2, 6.4), constrained_layout=True)
    image = ax.imshow(plotted, cmap=cmap, vmin=-1, vmax=1)

    short = {name: display.get(name, name).split(" (")[0] for name in names}
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([short[n] for n in names], rotation=38, ha="right", fontsize=8.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([short[n] for n in names], fontsize=8.5)
    ax.tick_params(length=0, colors=INK_SECONDARY)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)

    strongest = (0.0, "", "")
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            value = grid[i][j]
            if value is None:
                continue
            ax.text(
                j, i, f"{value:.2f}".replace("0.", "."),
                ha="center", va="center", fontsize=8,
                color=SURFACE if abs(value) > 0.55 else INK_SECONDARY,
            )
            if i < j and abs(value) > abs(strongest[0]):
                strongest = (value, short[a], short[b])

    bar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.03)
    bar.outline.set_visible(False)
    bar.ax.tick_params(length=0, labelsize=8, colors=INK_SECONDARY)
    ax.set_title(
        "지표끼리 얼마나 겹치나 — 상관계수",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )

    _explain(
        fig,
        f"가장 많이 겹치는 짝은 {strongest[1]} ↔ {strongest[2]} 로 r = {strongest[0]:.3f} 입니다.",
        "읽는 법 — r 은 두 지표가 같이 움직이는 정도입니다. +1은 완전히 같은 방향,\n"
        "0은 무관, −1은 반대입니다. |r| 이 0.8을 넘으면 '거의 같은 것을 두 번 재는' 수준이라\n"
        "가중치를 나눠 줘도 사실상 한 지표에 두 배를 준 셈이 됩니다.\n"
        "빨강이 양(같이 커짐), 파랑이 음(반대로 움직임), 회색이 무관입니다.",
    )
    path = out / "04_지표상관.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_gwangju(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """광주 5개 구. 우리 팀 시연 지역이라 따로 뽑습니다."""
    rows = sorted(
        (row for row in districts if row["sido_name"] == "광주광역시"),
        key=lambda row: row["rank"],
    )
    if not rows:
        raise SystemExit("광주 구역을 찾지 못했습니다.")

    fig, ax = plt.subplots(figsize=(9, 3.8), constrained_layout=True)
    positions = list(range(len(rows)))
    scores = [row["score"] for row in rows]

    ax.bar(
        positions, scores,
        color=[GRADE_COLORS[row["grade"]] for row in rows],
        width=0.6, zorder=3,
    )
    for index, row in enumerate(rows):
        ax.text(
            index, row["score"] + max(scores) * 0.03,
            f"전국 {row['rank']}위\n{row['grade']}등급 {row['grade_label']}",
            ha="center", va="bottom", fontsize=9, color=INK_SECONDARY,
            linespacing=1.4,
        )

    ax.set_xticks(positions)
    ax.set_xticklabels([row["full_name"].split(" ")[-1] for row in rows], fontsize=10.5)
    ax.set_ylabel("위험도", fontsize=9.5)
    ax.set_ylim(0, max(scores) * 1.32)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    _bare(ax)
    ax.set_title(
        "광주광역시 5개 구 — 점검 우선순위",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )

    best, worst = rows[0], rows[-1]
    _explain(
        fig,
        f"{best['full_name'].split(' ')[-1]}가 전국 {best['rank']}위로 가장 급하고, "
        f"{worst['full_name'].split(' ')[-1]}는 {worst['rank']}위입니다.",
        "읽는 법 — 막대 높이가 위험도, 색이 등급입니다(진할수록 급함).\n"
        f"{worst['full_name'].split(' ')[-1]}는 인구가 적지 않은데도 순위가 낮은데, 굵은 관로 비율이\n"
        "낮아 '터졌을 때 피해' 쪽 배율이 눌린 결과입니다. 발생 가능성만 보는 것이 아니라\n"
        "피해 규모까지 곱하기 때문에 이런 차이가 생깁니다.",
    )
    path = out / "05_광주.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_quality(plt, meta: dict, districts: list[dict], out: Path, data_dir=None) -> Path:
    """지표별 자료 확보율. 약점을 감추지 않고 먼저 보여주는 카드입니다."""
    coverage = meta["coverage"]
    total = meta["district_count"]
    display = meta["display_names"]
    names = sorted(coverage, key=lambda n: coverage[n])

    fig, ax = plt.subplots(figsize=(9.5, 3.9), constrained_layout=True)
    positions = list(range(len(names)))
    shares = [coverage[n] / total for n in names]

    ax.barh(
        positions, shares,
        color=[SEQUENTIAL[6] if s > 0.95 else SEQUENTIAL[3] for s in shares],
        height=0.6, zorder=3,
    )
    ax.axvline(1.0, color=BASELINE, linewidth=1, zorder=2)
    for index, name in enumerate(names):
        ax.text(
            shares[index] + 0.015, index,
            f"{coverage[name]}/{total}  ({shares[index]:.0%})",
            va="center", fontsize=9, color=INK_SECONDARY,
        )

    ax.set_yticks(positions)
    ax.set_yticklabels(
        [display.get(n, n).split(" (")[0] for n in names], fontsize=9.5
    )
    ax.set_xlim(0, 1.3)
    ax.set_xticks([])
    _bare(ax)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.set_title(
        "자료가 얼마나 채워졌나 — 지표별 확보율",
        fontsize=12.5, fontweight="bold", color=INK, loc="left", pad=12,
    )

    weakest = names[0]
    _explain(
        fig,
        f"가장 비어 있는 지표는 {display.get(weakest, weakest).split(' (')[0]} 로 "
        f"{coverage[weakest]}/{total} 입니다.",
        "읽는 법 — 자료가 없는 구역은 그 지표에서 전국 중간값으로 채웁니다. 즉 '모르는 곳은\n"
        "평균으로 둔다'는 뜻이라, 실제로 위험한 곳이 중간으로 끌려 내려올 수 있습니다.\n"
        "빈 자료를 0으로 세지 않는 이유가 이것입니다 — 0은 '없다'가 아니라 '모른다'입니다.",
    )
    path = out / "06_데이터품질.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


# ------------------------------------------------------------------ 실행


CHARTS = (
    chart_grades,
    chart_contributions,
    chart_model_shift,
    chart_correlation,
    chart_gwangju,
    chart_quality,
)


def render_all(
    meta: dict,
    districts: list[dict],
    out_dir: Path,
    data_dir: Path | None = None,
) -> list[Path]:
    """data_dir 는 점수 산출물을 읽은 곳과 같아야 합니다. 예전에는 v1 비교·상관
    차트가 항상 기본 디렉터리의 수집 데이터를 읽어, --data 로 다른 산출물을
    지정하면 점수와 다른 데이터로 차트가 그려졌습니다."""
    plt = setup()
    out_dir.mkdir(parents=True, exist_ok=True)
    return [chart(plt, meta, districts, out_dir, data_dir) for chart in CHARTS]


def main() -> None:
    parser = argparse.ArgumentParser(description="발표용 차트 6종을 다시 그립니다.")
    parser.add_argument("--build", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "docs" / "charts",
        help="출력 디렉터리 (기본: ~/docs/charts)",
    )
    parser.add_argument("--data", type=Path, default=paths.DATA)
    args = parser.parse_args()

    # 트리거가 "인자 하나라도 있으면"이라 --out 만 줘도 생성이 돌았습니다.
    # 도움말대로 --build 를 명시해야 실행합니다.
    if not args.build:
        parser.error("차트 생성은 --build 를 명시해야 합니다 (인자 없이 실행 = 자체 검증)")

    meta, districts = load(args.data)
    print(f"배치 {meta['batch_id']} · 시군구 {len(districts)}개")

    try:
        made = render_all(meta, districts, args.out, args.data)
    except MatplotlibMissing as error:
        raise SystemExit(str(error)) from error

    for path in made:
        print(f"→ {path}  ({path.stat().st_size // 1024} KB)")
    print(f"\n차트 {len(made)}장 · 배치 {meta['batch_id']} 기준")


def _self_test() -> int:
    import tempfile

    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'OK  ' if condition else 'FAIL'}] {label}"
              + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures += 1

    print("팔레트 규칙")
    check("등급색은 5단계 전부 정의", set(GRADE_COLORS) == {1, 2, 3, 4, 5})
    check("누적 막대 색이 지표 수 이상", len(STACK_STEPS) >= 5)
    # 무지개 판정은 hex 문자열로 할 수 없습니다. 채널을 실제로 뜯어
    # ① 전 단계에서 파랑이 가장 세고 ② 밝기가 단조로 오르는지 봅니다.
    def rgb(color: str) -> tuple[int, int, int]:
        return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))

    steps = [rgb(GRADE_COLORS[g]) for g in (5, 4, 3, 2, 1)]
    check(
        "등급색은 전 단계에서 파랑이 우세 (단일 색상)",
        all(b >= r and b >= g for r, g, b in steps),
        str(steps),
    )
    brightness = [sum(step) for step in steps]
    check(
        "등급색 밝기가 단조 증가 (5등급이 가장 진함)",
        all(a < b for a, b in zip(brightness, brightness[1:])),
        str(brightness),
    )

    print("\n산출물 없으면 친절히 멈춤")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            load(Path(tmp))
            check("없는 데이터에서 SystemExit", False)
        except SystemExit as error:
            check("없는 데이터에서 SystemExit", True)
            check("run.py 를 안내", "run.py" in str(error), str(error))

    print("\n실제 산출물로 렌더")
    try:
        meta, districts = load()
    except SystemExit:
        print("  [건너뜀] data/priority_scores.json 이 없습니다")
        print()
        print(f"결과: {'실패 ' + str(failures) + '건' if failures else '전부 통과'}")
        return 1 if failures else 0

    check("메타에 가중치", bool(meta.get("occurrence_weights")))
    check("메타에 한글명", bool(meta.get("display_names")))
    check("메타에 커버리지", bool(meta.get("coverage")))
    check("영향도 지표 목록", bool(meta.get("impact", {}).get("features")))
    check("구역이 순위 순", [r["rank"] for r in districts[:5]] == [1, 2, 3, 4, 5],
          str([r["rank"] for r in districts[:5]]))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "charts"
            made = render_all(meta, districts, out)
            check("차트 6장 생성", len(made) == 6, str([p.name for p in made]))
            check("전부 파일로 존재", all(p.exists() for p in made))
            check("빈 파일 없음", all(p.stat().st_size > 5000 for p in made),
                  str([(p.name, p.stat().st_size) for p in made]))
            check("파일명이 기존과 동일",
                  [p.name for p in made] == [
                      "01_등급분포.png", "02_기여도분해.png", "03_v1v2_순위이동.png",
                      "04_지표상관.png", "05_광주.png", "06_데이터품질.png",
                  ],
                  str([p.name for p in made]))
            # 같은 입력이면 같은 결과여야 합니다. 그래야 "차트가 왜 바뀌었지"에
            # 답할 수 있습니다.
            again = render_all(meta, districts, Path(tmp) / "again")
            check(
                "재실행 시 같은 결과",
                [p.stat().st_size for p in made] == [p.stat().st_size for p in again],
            )
            check("출력 디렉터리를 만들어 줌", out.exists())
    except MatplotlibMissing as error:
        print(f"  [건너뜀] {error}")

    print()
    print(f"결과: {'실패 ' + str(failures) + '건' if failures else '전부 통과'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        raise SystemExit(_self_test())
