from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import dataset
import paths
from analysis import charts as charts_mod
from analysis import cluster, pca, profile


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="수집한 지표의 특징을 봅니다. 기술통계·결측 패턴·상관·PCA·군집화."
    )
    parser.add_argument("--data", type=Path, default=paths.DATA)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"분석 결과 JSON 경로 (기본: <data>/{paths.DATA_PROFILE_FILE})",
    )
    parser.add_argument(
        "--charts",
        type=Path,
        default=None,
        help=f"차트 저장 디렉터리 (기본: {paths.CHARTS})",
    )
    parser.add_argument(
        "--no-charts", action="store_true", help="차트를 만들지 않습니다"
    )
    parser.add_argument(
        "--k",
        type=int,
        default=None,
        help="군집 수를 직접 지정 (기본: 실루엣 계수로 자동 선택)",
    )
    parser.add_argument(
        "--no-cluster", action="store_true", help="군집화를 건너뜁니다"
    )
    parser.add_argument(
        "--no-pca", action="store_true", help="PCA를 건너뜁니다"
    )
    parser.add_argument(
        "--extra",
        action="store_true",
        help="모델에서 제거한 지표(관로 평균연령·처리시설 연령)도 함께 봅니다",
    )
    args = parser.parse_args()

    rows, sources = dataset.merge(args.data)
    names = list(dataset.INDICATORS)
    if args.extra:
        names += ["pipe_mean_age_years", "facility_avg_age_years"]

    report: dict = {"sources": sources}
    pca_result = None
    cluster_result = None

    section("데이터 개요")
    for name, count in sources.items():
        mark = "" if count else "  ← 없음"
        print(f"  {name:12} {count:4d}개{mark}")
    print()
    print(f"  분석 단위: 기초자치단체 {len(rows)}개 (표본이 아니라 전수)")
    print(f"  지표: {len(names)}개")

    section("지표별 기술통계")
    stats = profile.profile(rows, names)
    print(profile.stats_table(stats))
    print()
    print("  왜도가 크면 소수 구역에 값이 몰려 있다는 뜻입니다. 0비율이 높으면")
    print("  변별력이 낮아 가중치를 크게 줄 근거가 약합니다.")

    section("결측 패턴")
    missing = profile.missing_report(rows, names)
    print(profile.missing_table(missing))

    section("결측이 무작위인가 (MNAR 진단)")
    print(profile.mnar_table(missing))

    section("지표 간 상관")
    available = dataset.available_indicators(rows, names)
    matrix, counts = profile.correlation_matrix(rows, available)
    print(profile.correlation_table(matrix, counts))

    pairs = profile.high_pairs(matrix, counts)
    print()
    if pairs:
        print(f"  |r| >= {profile.HIGH_CORRELATION} 인 쌍 {len(pairs)}개 — 같은 것을 재고 있을 수 있습니다.")
        for left, right, value, count in pairs:
            level = "치명적" if abs(value) >= profile.CRITICAL_CORRELATION else "주의"
            print(f"    {left:24} ↔ {right:24} r={value:6.3f} (n={count})  [{level}]")
    else:
        print(f"  |r| >= {profile.HIGH_CORRELATION} 인 쌍이 없습니다. 지표 중복이 크지 않습니다.")

    report["profile"] = profile.build_report(rows, names)

    if not args.no_pca:
        section("주성분 분석 — 지표 몇 개가 실질 차원인가")
        try:
            pca_result = pca.analyze(rows, names)
        except ValueError as error:
            print(f"  건너뜁니다: {error}")
        else:
            print(pca_result.summary)
            print()
            print(pca.scree_table(pca_result))
            print()
            print(pca.loadings_table(pca_result))
            print()
            for component in range(min(2, len(pca_result.eigenvalues))):
                makeup = pca.component_makeup(pca_result, component)
                if makeup:
                    print(f"  PC{component + 1} 을 주로 구성하는 지표: {makeup}")
            print()
            print(f"  판정: {pca_result.verdict}")
            report["pca"] = pca_result.to_dict()

    if not args.no_cluster:
        section("군집 분석 — 시군구를 유형으로 나누면")
        try:
            cluster_result = cluster.analyze(rows, names, k=args.k)
        except ValueError as error:
            print(f"  건너뜁니다: {error}")
        else:
            print(cluster.k_table(cluster_result))
            print()
            print(cluster_result.summary)
            print()
            print(cluster.cluster_table(cluster_result))
            print()
            print(f"  판정: {cluster_result.verdict}")

            report["cluster"] = cluster_result.to_dict()
            report["cluster"]["assignments"] = {
                row["sgg_code"]: cluster_result.labels[index]
                for index, row in enumerate(rows)
            }

    section("시도별 평균")
    print(profile.sido_table(rows, available))

    out_path = args.out or (args.data / paths.DATA_PROFILE_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)

    print()
    print("=" * 72)
    print(f"→ {out_path}  (기계 판독용)")

    if not args.no_charts:
        try:
            made = charts_mod.render_all(
                rows, names, out_dir=args.charts or paths.CHARTS, k=args.k
            )
        except charts_mod.MatplotlibMissing as error:
            print(f"  차트 생략: {error}")
        else:
            total = sum(path.stat().st_size for path in made)
            print(f"→ {made[0].parent}/  (차트 {len(made)}개, {total / 1024:.0f} KB)")
            for path in made:
                print(f"    {path.name}")

    print()
    print("  문서에 인용한 상관·결측 수치는 이 파일들이 근거입니다.")


if __name__ == "__main__":
    main()
