from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import paths
from dataset import load_json, merge
from mapping.sgg_normalize import diff_codes
from scoring.grading import (
    DEFAULT_BANDS,
    GRADE_DESCRIPTIONS,
    GRADE_LABELS,
    add_regional_ranks,
    assign_grades,
    distribution,
    regional_table,
    sido_summary_table,
    summarize_by_sido,
    summary,
)
from scoring.dominance import analyze as dominance_analyze
from scoring.dominance import bounds_table
from scoring.objective_weights import compare_weights, critic_weights, entropy_weights
from scoring.risk import (
    DISPLAY_NAMES,
    IMPACT_FEATURES,
    IMPACT_FLOOR,
    IMPACT_SPAN,
    MODEL_VERSION,
    OCCURRENCE_WEIGHTS,
    TRIGGER_SPAN,
    contribution_shares,
    score_districts,
)
from scoring.sensitivity import analyze
from scoring.weighted import WEIGHTS as WEIGHTS_V1
from scoring.weighted import score_districts as score_districts_v1

# 패키지 위치 기준이므로 어느 디렉터리에서 실행해도 동작합니다.
DATA = paths.DATA

# 위험도 = 발생확률 × 영향도 (risk.py). 커버리지 보고용 전체 지표 목록입니다.
FEATURES = [*OCCURRENCE_WEIGHTS, *IMPACT_FEATURES]


# 다르면 막지 않고 알립니다. 8/2 회귀가 가중치를 바꾸는 것은 정상 경로이고, 그때
# 가중치 사전 등록 문서 변경 이력에 사유를 적으면 됩니다. 조용히 바뀌는 것만
# 막습니다.
def report_weight_registration(data_dir: Path) -> dict | None:
    path = data_dir / paths.WEIGHTS_PREREGISTERED_FILE
    if not path.exists():
        return None

    registered = json.loads(path.read_text(encoding="utf-8"))
    want = registered.get("occurrence_weights", {})
    drift: dict[str, tuple] = {
        name: (want.get(name), OCCURRENCE_WEIGHTS.get(name))
        for name in set(want) | set(OCCURRENCE_WEIGHTS)
        if want.get(name) != OCCURRENCE_WEIGHTS.get(name)
    }

    # 가중치 외의 등록 항목도 대조합니다. 예전에는 occurrence_weights 만 봐서
    # 영향배율 바닥·폭이나 트리거 폭을 코드에서 바꿔도 "일치합니다"가 나왔습니다.
    want_impact = registered.get("impact", {})
    fixed = {
        "model_version": (registered.get("model_version"), MODEL_VERSION),
        "impact.features": (want_impact.get("features"), list(IMPACT_FEATURES)),
        "impact.floor": (want_impact.get("floor"), IMPACT_FLOOR),
        "impact.span": (want_impact.get("span"), IMPACT_SPAN),
        "rain_trigger.span": (
            registered.get("rain_trigger", {}).get("span"),
            TRIGGER_SPAN,
        ),
    }
    drift.update({name: pair for name, pair in fixed.items() if pair[0] != pair[1]})

    # 중점관리대상 배율 상수는 2026-08-03 에 제거했습니다 (항상 1.0 인 곱).
    # 등록값이 1.0 이면 제거와 수학적으로 동일하므로 일치로 봅니다.
    multiplier = registered.get("priority_target_multiplier")
    if multiplier not in (None, 1.0):
        drift["priority_target_multiplier"] = (multiplier, "제거됨(코드에 없음)")

    print()
    print("=" * 72)
    # 개정본이면 개정일까지 보여줍니다. 최초 등록일만 찍으면 개정 사실이
    # 출력에서 사라져, 발표에서 "처음 고정한 값 그대로"로 오해됩니다.
    stamp = registered.get("registered_at")
    if registered.get("revised_at"):
        stamp = (
            f"{stamp} → {registered['revised_at']} "
            f"{registered.get('revision', 2)}차 개정"
        )
    print(f"가중치 사전 등록 ({stamp})")
    print("=" * 72)
    if not drift:
        print("  등록본과 일치합니다.")
        if registered.get("revised_at"):
            # 개정본에는 "데이터를 보기 전에 고정" 이라고 쓸 수 없습니다. 개정은
            # 회귀 결과를 본 뒤에 사전 등록된 판정 규칙을 적용한 결과입니다.
            print(
                "  이 값은 개정본입니다 — 사전 등록한 판정 규칙(규칙 2·4·5)이"
                " 발동해 재배분한 값이며,"
            )
            print(
                "  변경 사유는 가중치 사전 등록 문서 '2차 개정' 에 회귀 결과와"
                " 함께 적혀 있습니다."
            )
        else:
            print("  데이터를 보기 전에 고정한 값 그대로입니다.")
    else:
        print("  등록본과 다릅니다. 가중치 사전 등록 문서 변경 이력에 사유를 남기세요.")
        for name, (before, after) in sorted(drift.items()):
            print(f"    {name:26} {before} → {after}")
    return registered


# 입력 지표값만 해싱합니다. 값이 같은데 순위가 달라졌다면 모델이 바뀐 것이고, 값이
# 다르면 데이터가 갱신된 것입니다. 두 원인을 구분해야 "왜 지난주랑 순위가 다르죠?"에
# 답할 수 있습니다.
#
# 강수(rainfall_30d_mm)는 의도적으로 제외합니다. 강수는 등급·순위에 반영되지 않고
# triggered_score 로만 나가므로, 비가 올 때마다 batch_id 가 바뀌면 "순위는 그대로인데
# 새 배치"가 양산됩니다. 대신 triggered_score 만 다른 파일이 같은 batch_id 를 가질
# 수 있다는 것이 트레이드오프입니다.
def input_digest(merged: list[dict]) -> str:
    rows = sorted(
        (row["sgg_code"], [row.get(name) for name in FEATURES]) for row in merged
    )
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# 산출물 앞에 붙는 메타 봉투. 프론트·API가 "이 점수가 언제, 어떤 가중치로, 어떤
# 데이터로 계산됐는지"를 파일만 보고 알 수 있어야 합니다.
def build_meta(merged: list[dict], sources: dict) -> dict:
    digest = input_digest(merged)
    total = len(merged)
    return {
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_id": f"{MODEL_VERSION}-{digest[:12]}",
        "input_digest": digest,
        "district_count": total,
        "admin_basis": "2026-07-01 개편 전 (광주=29, 전남=46)",
        "occurrence_weights": dict(OCCURRENCE_WEIGHTS),
        "impact": {
            "features": list(IMPACT_FEATURES),
            "floor": IMPACT_FLOOR,
            "span": IMPACT_SPAN,
        },
        # 강수 트리거는 등급·순위에 반영되지 않습니다. triggered_score 로만
        # 제공하므로 소비하는 쪽이 이 사실을 알 수 있게 규칙을 함께 싣습니다.
        "rain_trigger": {
            "span": TRIGGER_SPAN,
            "applies_to_score": False,
            "field": "triggered_score",
        },
        "display_names": dict(DISPLAY_NAMES),
        "grades": {
            str(grade): {
                "label": GRADE_LABELS[grade],
                "share": fraction,
                "description": GRADE_DESCRIPTIONS[grade],
            }
            for grade, fraction in DEFAULT_BANDS
        },
        "coverage": {
            name: sum(1 for row in merged if row.get(name) is not None)
            for name in FEATURES
        },
        "sources": dict(sources),
    }


def report_coverage(merged: list[dict], sources: dict) -> None:
    print("=" * 72)
    print("입력")
    print("=" * 72)
    for name, count in sources.items():
        mark = "" if count else "  ← 없음"
        print(f"  {name:12} {count:4d}개{mark}")

    print()
    print("지표 커버리지")
    total = len(merged)
    for feature in FEATURES:
        present = sum(1 for row in merged if row.get(feature) is not None)
        share = present / total if total else 0.0
        bar = "█" * int(share * 30)
        note = ""
        if present == 0:
            note = "  ← 전부 결측. 순위에 영향 없음(모두 중앙값 처리)"
        print(f"  {feature:26} {present:4d}/{total} ({share:5.1%}) {bar}{note}")

    # 0개는 '대상 없음'이 아니라 '수집 전'일 수 있어 구분해 보여줍니다 —
    # 중점관리·강수는 지하안전·기상청 API 키가 있어야 채워집니다.
    # 중점관리대상은 점수에 쓰지 않습니다(전국 5건, 시군구 조인 불가). 파레토
    # 지배의 한 축으로만 쓰이므로 수집 상태만 확인합니다.
    targets = sum(1 for row in merged if row.get("is_priority_target"))
    if targets:
        print(f"  {'중점관리대상':26} {targets:4d}개 (점수 미반영, 지배 분석에만)")
    else:
        print(f"  {'중점관리대상':26}    0개  ← 지하안전 API 미수집일 수 있음")
    rain = sum(1 for row in merged if row.get("rainfall_30d_mm") is not None)
    if rain == 0:
        print(f"  {'최근 30일 강수':26}    0개  ← collectors/rainfall.py --key 로 수집 (정보용)")

    # 조인 누락 검증
    codes_with_pipes = {
        row["sgg_code"] for row in merged if row.get("pipe_total_length")
    }
    missing = {row["sgg_code"] for row in merged} - codes_with_pipes
    if missing:
        print()
        print(f"  [!] 총연장이 없는 시군구 {len(missing)}개: {sorted(missing)[:10]}")


def report_model_change(scored, scored_v1, top_n: int) -> None:
    rank_v2 = {item.sgg_code: item.rank for item in scored}
    rank_v1 = {item.sgg_code: item.rank for item in scored_v1}
    name_by_code = {item.sgg_code: item.full_name for item in scored}

    shared = set(rank_v2) & set(rank_v1)
    if not shared:
        return

    shifts = {code: rank_v2[code] - rank_v1[code] for code in shared}

    top_v2 = {item.sgg_code for item in scored[:top_n]}
    top_v1 = {item.sgg_code for item in scored_v1[:top_n]}
    kept = len(top_v2 & top_v1)

    print()
    print("=" * 72)
    print("모델 변경 영향: v1 가중합 → v2 발생확률 × 영향도")
    print("=" * 72)
    print(f"  상위 {top_n}위 유지  {kept}/{top_n} ({kept / top_n:.0%})")
    print(f"  평균 순위 이동  {sum(abs(v) for v in shifts.values()) / len(shifts):.1f}위")

    biggest = sorted(shifts.items(), key=lambda pair: -abs(pair[1]))[:6]
    print()
    print("  가장 크게 움직인 구역")
    for code, shift in biggest:
        arrow = "▲" if shift < 0 else "▼"
        print(
            f"    {name_by_code.get(code, code):22} "
            f"{rank_v1[code]:3d}위 → {rank_v2[code]:3d}위  {arrow}{abs(shift)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="우선순위 산정 파이프라인. 수집 결과를 합쳐 점수·등급·순위를 산출합니다.")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="산출물 경로 (기본: <data>/priority_scores.json)",
    )
    parser.add_argument(
        "--sido",
        default=None,
        help="상세 출력할 시도 코드 (예: 29 광주). 기본은 광주",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="전국 상위 몇 개를 표시할지",
    )
    parser.add_argument(
        "--supervised",
        action="store_true",
        help="침하 데이터로 가중치를 학습해 함께 비교 (침하 데이터 필요)",
    )
    args = parser.parse_args()

    out_path = args.out or (args.data / paths.PRIORITY_SCORES_FILE)

    merged, sources = merge(args.data)
    report_coverage(merged, sources)

    # ---- 점수 산정 (v2: 위험도 = 발생확률 × 영향도)
    scored = score_districts(merged)

    # v1(가중합)로도 매겨 순위 변화를 봅니다. 지표를 바꾼 것이 결과에 실제로
    # 영향을 줬는지 확인해야 "재설계했다"는 말에 근거가 생깁니다.
    scored_v1 = score_districts_v1(merged)
    report_model_change(scored, scored_v1, args.top)

    print()
    print("=" * 72)
    print("가중치")
    print("=" * 72)

    # 전부 결측인 지표는 분포 기반 가중치가 정의되지 않습니다. 비교 대상은
    # 발생확률 지표만: 영향도는 합산항이 아니라 배율이라 같은 축이 아닙니다.
    available = [
        feature
        for feature in OCCURRENCE_WEIGHTS
        if any(row.get(feature) is not None for row in merged)
    ]
    excluded = [feature for feature in OCCURRENCE_WEIGHTS if feature not in available]

    if excluded:
        print(f"  전부 결측이라 제외: {excluded}")
        print()

    if len(available) >= 2:
        entropy = entropy_weights(merged, available)
        critic = critic_weights(merged, available)
        print(
            compare_weights(
                {
                    "수동": {name: OCCURRENCE_WEIGHTS[name] for name in available},
                    "엔트로피": entropy.weights,
                    "CRITIC": critic.weights,
                }
            )
        )
        print()
        print("  수동 가중치는 설계자가 정한 값입니다. 엔트로피·CRITIC은 데이터 분포에서")
        print("  도출되지만 침하와의 관련성은 반영하지 않습니다 (--supervised 참조).")
        print("  ※ 수동 가중치는 제외된 지표를 포함한 값이라 합이 1이 아닙니다.")
        print(f"  ※ 영향도({', '.join(IMPACT_FEATURES)})는 배율이라 이 비교에서 제외됩니다.")
    else:
        print(f"  가용 지표가 {len(available)}개뿐이라 분포 기반 가중치를 건너뜁니다.")

    # ---- 등급·순위
    graded = assign_grades(scored)
    master = load_json(args.data / paths.DISTRICTS_MASTER_FILE)
    graded = add_regional_ranks(graded, master)

    print()
    print("=" * 72)
    print("전국 등급 분포")
    print("=" * 72)
    print(summary(graded))

    print()
    print("=" * 72)
    print(f"전국 상위 {args.top}개")
    print("=" * 72)
    by_code = {row["sgg_code"]: row for row in merged}
    scored_by_code = {item.sgg_code: item for item in scored}

    def cell(value, spec: str) -> str:
        return "N/A".rjust(len(format(0, spec))) if value is None else format(value, spec)

    print(
        f"  {'순위':>4} {'등급':>3} {'구역':22} {'위험도':>7} "
        f"{'노후율':>7} {'콘크리트':>8} {'대구경':>7} {'영향배율':>7} {'지역순위':>9}"
    )
    print("  " + "-" * 92)
    for item in graded[: args.top]:
        row = by_code[item.sgg_code]
        detail = scored_by_code[item.sgg_code].features
        print(
            f"  {item.rank:4d} {item.grade:3d} {item.full_name:22} "
            f"{item.score:7.4f} "
            f"{cell(row.get('aged_ratio_25y'), '7.3f')} "
            f"{cell(row.get('concrete_ratio'), '8.3f')} "
            f"{cell(row.get('large_diameter_ratio'), '7.3f')} "
            f"{detail['impact_multiplier']:7.3f} "
            f"{item.sido_name or '':>5} {item.sido_rank:2d}/{item.sido_count}"
        )

    # ---- 지역 상세
    target_sido = args.sido or "29"
    print()
    print("=" * 72)
    print("지역 내 우선순위")
    print("=" * 72)
    print(regional_table(graded, target_sido))

    # ---- 시도 집계
    exposures = {
        row["sgg_code"]: row["pipe_total_length"] or 0.0 for row in merged
    }
    summaries = summarize_by_sido(graded, exposures, master)
    print()
    print("=" * 72)
    print("시도 순위 (관로 연장 가중 평균)")
    print("=" * 72)
    print(sido_summary_table(summaries))

    # ---- 민감도
    print()
    print("=" * 72)
    print("가중치 민감도")
    print("=" * 72)
    sensitivity = analyze(merged, top_n=max(10, len(merged) // 10))
    print(f"  {sensitivity.summary}")
    print(f"  판정: {sensitivity.verdict}")

    # ---- 가중치를 아예 쓰지 않는 판정
    print()
    print("=" * 72)
    print("가중치 없는 판정 (파레토 지배)")
    print("=" * 72)
    dominance = dominance_analyze(merged)
    print(f"  {dominance.summary}")
    print(f"  판정: {dominance.verdict}")
    print()
    print(bounds_table(dominance, merged))

    report_weight_registration(args.data)

    # ---- 지도학습 (선택)
    if args.supervised:
        has_outcome = any(row.get("subsidence_count") is not None for row in merged)
        if not has_outcome:
            print()
            print("  [!] --supervised 를 지정했으나 침하 데이터가 없습니다. 건너뜁니다.")
        else:
            from scoring.supervised import fit_priority_model, score_with_model

            print()
            print("=" * 72)
            print("지도학습: 침하를 정답으로 가중치 도출")
            print("=" * 72)
            # 정답은 원인 무관 전체 침하 **건수**이고 분포는 포아송입니다.
            # 관로연장을 노출량(offset)으로 넣습니다 — 안 넣으면 관로가 긴 곳이
            # 자동으로 위험해 보입니다. 등록 당시의 binomial 은 양성률이 76.9%로
            # 나와 변별력이 없어 교체했습니다 (가중치 사전 등록 문서 변경 이력).
            trainable = [
                dict(row, outcome=float(row.get("subsidence_count") or 0))
                for row in merged
            ]
            usable = [
                feature
                for feature in FEATURES
                # 정답 누출 방지: 침하로 만든 지표는 회귀 설명변수에서 뺍니다.
                if feature != "subsidence_per_km"
                and sum(1 for row in trainable if row.get(feature) is not None) > 0
            ]
            model = fit_priority_model(
                trainable, usable, "outcome", "poisson", "pipe_total_length"
            )
            print(model.summary())
            model_scored = score_with_model(model, trainable)
            print()
            print("  학습 모형 기준 상위 10개:")
            for item in model_scored[:10]:
                print(f"    {item.rank:3d}위 {item.full_name:22} 확률 {item.score:.3f}")

    # ---- 저장
    districts = []
    for item in graded:
        record = item.to_dict()
        row = by_code[item.sgg_code]
        scored_item = scored_by_code[item.sgg_code]
        record["features"] = scored_item.features
        record["contributions"] = scored_item.contributions
        record["contribution_shares"] = contribution_shares(scored_item)
        record["missing_features"] = scored_item.missing_features
        record["pipe_total_length_km"] = row.get("pipe_total_length_km")
        districts.append(record)

    meta = build_meta(merged, sources)
    payload = {"meta": meta, "districts": districts}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    sido_path = out_path.parent / paths.PRIORITY_BY_SIDO_FILE
    with sido_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"meta": meta, "sido": [item.to_dict() for item in summaries]},
            handle,
            ensure_ascii=False,
            indent=1,
        )

    print()
    print("=" * 72)
    print(f"→ {out_path} ({len(districts)}개, batch {meta['batch_id']})")
    print(f"→ {sido_path} ({len(summaries)}개)")
    print()
    print("  다음 단계: Mongo 적재 (POST /api/priority/recalculate 가 이 엔진을 호출)")


if __name__ == "__main__":
    main()
