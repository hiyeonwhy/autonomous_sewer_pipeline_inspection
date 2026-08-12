from __future__ import annotations

import json
from pathlib import Path

import paths

# 수집 결과에서 뽑는 지표 이름. 모델(scoring/risk.py)이 쓰는 이름과 같지만
# 여기가 데이터 계층입니다. 모델을 바꿔도 이 목록은 바뀌지 않습니다.
INDICATORS = [
    "aged_ratio_25y",
    "concrete_ratio",
    "subsidence_per_km",
    "combined_sewer_ratio",
    "unconnected_ratio",
    "heavy_rain_zone_ratio",
    "large_diameter_ratio",
    "population",
]

# 지표 한글명의 단일 출처. scoring/risk.py 가 이것을 재수출하고 산출물 meta 로
# 나가므로, 프론트가 한글명을 하드코딩하지 않습니다.
DISPLAY_NAMES: dict[str, str] = {
    "aged_ratio_25y": "실측 노후율 (25년 이상 경과)",
    "concrete_ratio": "콘크리트관 비율",
    "subsidence_per_km": "km당 침하 (원인 무관)",
    "combined_sewer_ratio": "합류식 비율",
    "unconnected_ratio": "배수설비 미연결률",
    "heavy_rain_zone_ratio": "집중강우 중점관리구역 비율",
    "large_diameter_ratio": "대구경 비율 (500mm 이상)",
    "population": "주민등록 인구",
}

# 분석에서만 쓰는 부가 지표. 모델에서 제거했지만 상관을 재확인할 때 필요합니다.
EXTRA_NAMES: dict[str, str] = {
    "pipe_mean_age_years": "관로 평균 경과연수",
    "facility_avg_age_years": "처리시설 평균 연령",
    "pipe_total_length_km": "관로 총연장 (km)",
}


def load_json(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def merge(data_dir: Path | None = None) -> tuple[list[dict], dict]:
    data_dir = data_dir or paths.DATA

    master = load_json(data_dir / paths.DISTRICTS_MASTER_FILE)
    if master is None:
        raise SystemExit(
            f"{data_dir / paths.DISTRICTS_MASTER_FILE} 가 없습니다.\n"
            "  boundary/build_boundaries.py 를 먼저 실행하세요."
        )

    sewer = load_json(data_dir / paths.SEWER_BY_SGG_FILE)
    if sewer is None:
        raise SystemExit(
            f"{data_dir / paths.SEWER_BY_SGG_FILE} 가 없습니다.\n"
            "  python3 -m collectors.sewer --csv 를 먼저 실행하세요."
        )

    facilities = load_json(data_dir / paths.FACILITY_AGE_BY_SGG_FILE) or []
    subsidence = load_json(data_dir / paths.SUBSIDENCE_BY_SGG_FILE) or []
    # 트리거용 최근 강수. collectors/rainfall.py 산출물 — 없으면 전부 결측.
    rainfall = load_json(data_dir / paths.RAINFALL_BY_SGG_FILE) or []
    # 집중강우 중점관리구역. 지하안전정보 중점관리대상(전국 5건)의 대체재로
    # 2차 개정에서 도입했습니다 (가중치 사전 등록 문서 참조).
    manholes = load_json(data_dir / paths.MANHOLE_BY_SGG_FILE) or []
    # 인구는 영향도 축(피해 규모)에 씁니다 — 관 굵기만으로는 피해를 입는
    # 사람 수가 빠집니다 (가중치 사전 등록 문서 3차 개정).
    population = load_json(data_dir / paths.POPULATION_BY_SGG_FILE) or []

    # 주력 지표. 없으면 노후도가 전부 결측이 되므로 run.py 가 경고합니다
    # (한때 이 파일을 만들고도 읽지 않고 있었습니다).
    pipe_age = load_json(data_dir / paths.PIPE_AGE_BY_SGG_FILE) or []

    sewer_map = {row["sgg_code"]: row for row in sewer}
    facility_map = {row["sgg_code"]: row for row in facilities}
    subsidence_map = {row["sgg_code"]: row for row in subsidence}
    pipe_age_map = {row["sgg_code"]: row for row in pipe_age}
    rainfall_map = {row["sgg_code"]: row for row in rainfall}
    manhole_map = {row["sgg_code"]: row for row in manholes}
    population_map = {row["sgg_code"]: row for row in population}

    sources = {
        "master": len(master),
        "sewer": len(sewer),
        "pipe_age": len(pipe_age),
        "facilities": len(facilities),
        "subsidence": len(subsidence),
        "rainfall": len(rainfall),
        "manholes": len(manholes),
        "population": len(population),
    }

    merged: list[dict] = []
    for row in master:
        code = row["sgg_code"]
        pipes = sewer_map.get(code, {})
        facility = facility_map.get(code, {})
        sinkholes = subsidence_map.get(code, {})
        ages = pipe_age_map.get(code, {})

        length = pipes.get("pipe_total_length")

        # 침하는 반드시 노출량(km)으로 정규화합니다. 원 건수를 쓰면 점수가
        # 사실상 총연장의 복사본이 되어 "관로 긴 곳 순위"가 됩니다.
        #
        # 지표(가중치 0.20)는 **하수 원인** 건수 기준입니다 — 사전 등록본 그대로.
        # 회귀 정답은 **원인 무관 전체** 건수를 씁니다. 원인 필드가 2018년 자료에만
        # 채워져 있어 하수 원인만 쓰면 시간 편향이 커지기 때문입니다.
        # 한때 전체 건수 자리에 하수 원인 건수를 담아 두어(이름만 subsidence_count)
        # 정답이 조용히 뒤바뀌던 버그가 있었습니다. 두 값을 분리해 둡니다.
        sewer_count = sinkholes.get("sewer_cause_count")
        total_count = sinkholes.get("subsidence_count")

        def rate(count) -> float | None:
            if count is None or not length or length <= 0:
                return None
            return count / (length / 1000.0)

        # 점수에 쓰는 값은 **원인 무관 전체**입니다. 하수 원인만 쓰면 원인 미기재
        # 84.7% 가 "하수 원인 0건"으로 관측되어, 조사가 부실한 지자체가 지표상
        # 안전해 보입니다 (3차 개정).
        per_km = rate(total_count)
        sewer_per_km = rate(sewer_count)

        merged.append(
            {
                "sgg_code": code,
                "full_name": row["full_name"],
                "sido_code": row.get("sido_code"),
                "sido_name": row.get("sido_name"),
                # ---- 발생확률 지표
                "aged_ratio_25y": ages.get("aged_ratio_25y"),
                "concrete_ratio": ages.get("concrete_ratio"),
                "subsidence_per_km": per_km,
                "combined_sewer_ratio": pipes.get("combined_sewer_ratio"),
                "unconnected_ratio": pipes.get("unconnected_ratio"),
                "heavy_rain_zone_ratio": manhole_map.get(code, {}).get(
                    "heavy_rain_zone_ratio"
                ),
                # ---- 영향도 지표
                "large_diameter_ratio": ages.get("large_diameter_ratio"),
                # ---- 모델에서 제거했지만 상관 확인에 씁니다
                "facility_avg_age_years": facility.get("facility_avg_age_years"),
                "pipe_mean_age_years": ages.get("pipe_mean_age_years"),
                # ---- 부가 정보
                "pipe_total_length": length,
                "pipe_total_length_km": round(length / 1000.0, 1) if length else None,
                "plant_count": facility.get("plant_count"),
                "oldest_facility": facility.get("oldest_facility"),
                "subsidence_count": total_count,
                "sewer_subsidence_count": sewer_count,
                "sewer_subsidence_per_km": sewer_per_km,
                "sewer_cause_share": (
                    round(sewer_count / total_count, 4)
                    if sewer_count is not None and total_count
                    else None
                ),
                "is_priority_target": bool(sinkholes.get("is_priority_target")),
                "age_missing_reason": ages.get("age_missing_reason"),
                "rainfall_30d_mm": rainfall_map.get(code, {}).get("rainfall_30d_mm"),
                "population": population_map.get(code, {}).get("population"),
                "households": population_map.get(code, {}).get("households"),
            }
        )

    return merged, sources


def observed(rows: list[dict], name: str) -> list[float]:
    return [row[name] for row in rows if row.get(name) is not None]


def available_indicators(rows: list[dict], names: list[str] | None = None) -> list[str]:
    return [
        name
        for name in (names or INDICATORS)
        if any(row.get(name) is not None for row in rows)
    ]


def complete_rows(rows: list[dict], names: list[str]) -> list[dict]:
    return [row for row in rows if all(row.get(name) is not None for name in names)]


def label(name: str) -> str:
    return DISPLAY_NAMES.get(name) or EXTRA_NAMES.get(name) or name


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    import tempfile

    failures = 0

    def check(label_text: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label_text}")
        else:
            failures += 1
            print(f"  [FAIL] {label_text}  {detail}")

    def write(directory: Path, filename: str, payload) -> None:
        (directory / filename).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    print("지표 목록과 표시명의 정합")
    check("모든 지표에 한글 표시명이 있음",
          all(name in DISPLAY_NAMES for name in INDICATORS),
          str([name for name in INDICATORS if name not in DISPLAY_NAMES]))
    check("지표 이름 중복 없음", len(set(INDICATORS)) == len(INDICATORS))
    check("표시명·부가명이 겹치지 않음",
          not set(DISPLAY_NAMES) & set(EXTRA_NAMES),
          str(set(DISPLAY_NAMES) & set(EXTRA_NAMES)))
    check("label(): 지표 → 표시명",
          all(label(name) == DISPLAY_NAMES[name] for name in INDICATORS), "")
    check("label(): 부가 지표 → 부가명",
          label("pipe_mean_age_years") == EXTRA_NAMES["pipe_mean_age_years"])
    check("label(): 모르는 이름은 그대로", label("unknown_x") == "unknown_x")

    master = [
        {"sgg_code": "29110", "full_name": "광주광역시 동구",
         "sido_code": "29", "sido_name": "광주광역시"},
        {"sgg_code": "29140", "full_name": "광주광역시 서구",
         "sido_code": "29", "sido_name": "광주광역시"},
        {"sgg_code": "29155", "full_name": "광주광역시 남구",
         "sido_code": "29", "sido_name": "광주광역시"},
    ]

    print()
    print("merge(): 필수 파일 검사")
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        try:
            merge(directory)
            check("마스터가 없으면 중단", False, "")
        except SystemExit as error:
            check("마스터가 없으면 중단", "boundary" in str(error), str(error))

        write(directory, paths.DISTRICTS_MASTER_FILE, master)
        try:
            merge(directory)
            check("하수관로 산출물이 없으면 중단", False, "")
        except SystemExit as error:
            check("하수관로 산출물이 없으면 중단",
                  "collectors.sewer" in str(error), str(error))

        print()
        print("merge(): 선택 산출물이 하나도 없어도 동작 (결측 전파)")
        write(directory, paths.SEWER_BY_SGG_FILE, [
            {"sgg_code": "29110", "pipe_total_length": 10000.0,
             "combined_sewer_ratio": 0.45, "unconnected_ratio": 0.25},
        ])
        merged, sources = merge(directory)
        check("마스터 전 구역이 결과에 있음 (자료 없어도 행 유지)",
              [row["sgg_code"] for row in merged] == ["29110", "29140", "29155"],
              str([row["sgg_code"] for row in merged]))
        check("없는 소스는 0으로 보고",
              sources["pipe_age"] == 0 and sources["population"] == 0, str(sources))
        by_code = {row["sgg_code"]: row for row in merged}
        check("수집 안 된 지표는 None (0 아님)",
              by_code["29110"]["aged_ratio_25y"] is None
              and by_code["29110"]["population"] is None
              and by_code["29110"]["rainfall_30d_mm"] is None, "")
        check("하수관로 값은 이미 조인됨",
              by_code["29110"]["combined_sewer_ratio"] == 0.45, "")

        print()
        print("merge(): 전체 조인")
        write(directory, paths.SEWER_BY_SGG_FILE, [
            {"sgg_code": "29110", "pipe_total_length": 10000.0,
             "combined_sewer_ratio": 0.45, "unconnected_ratio": 0.25},
            {"sgg_code": "29140", "pipe_total_length": None},
            {"sgg_code": "29155", "pipe_total_length": 0.0},
        ])
        write(directory, paths.PIPE_AGE_BY_SGG_FILE, [
            {"sgg_code": "29110", "aged_ratio_25y": 0.5, "concrete_ratio": 0.3,
             "pipe_mean_age_years": 20.0, "large_diameter_ratio": 0.2,
             "age_missing_reason": None},
            {"sgg_code": "29140", "aged_ratio_25y": None,
             "age_missing_reason": "연도별 보고 부족"},
        ])
        write(directory, paths.SUBSIDENCE_BY_SGG_FILE, [
            {"sgg_code": "29110", "subsidence_count": 5, "sewer_cause_count": 2,
             "is_priority_target": True},
            {"sgg_code": "29140", "subsidence_count": 3, "sewer_cause_count": 1},
            {"sgg_code": "29155", "subsidence_count": 4, "sewer_cause_count": 0},
        ])
        write(directory, paths.FACILITY_AGE_BY_SGG_FILE, [
            {"sgg_code": "29110", "facility_avg_age_years": 30.5,
             "plant_count": 2, "oldest_facility": "제1처리장"},
        ])
        write(directory, paths.RAINFALL_BY_SGG_FILE, [
            {"sgg_code": "29110", "rainfall_30d_mm": 120.5},
        ])
        write(directory, paths.MANHOLE_BY_SGG_FILE, [
            {"sgg_code": "29110", "heavy_rain_zone_ratio": 0.05},
        ])
        write(directory, paths.POPULATION_BY_SGG_FILE, [
            {"sgg_code": "29110", "population": 100000, "households": 45000},
        ])

        merged, sources = merge(directory)
        by_code = {row["sgg_code"]: row for row in merged}
        donggu = by_code["29110"]

        check("소스별 행 수 집계", sources["master"] == 3 and sources["sewer"] == 3
              and sources["pipe_age"] == 2 and sources["subsidence"] == 3,
              str(sources))

        # 침하율: 지표는 **원인 무관 전체** 건수 / km. 한때 전체 건수 자리에
        # 하수 원인 건수가 들어가 정답이 조용히 뒤바뀌던 버그의 회귀 검사입니다.
        check("subsidence_per_km = 전체 건수 기준 (5건/10km)",
              donggu["subsidence_per_km"] == 0.5, str(donggu["subsidence_per_km"]))
        check("sewer_subsidence_per_km = 하수 원인 기준 (2건/10km)",
              donggu["sewer_subsidence_per_km"] == 0.2,
              str(donggu["sewer_subsidence_per_km"]))
        check("두 건수가 필드로 분리 보존",
              donggu["subsidence_count"] == 5
              and donggu["sewer_subsidence_count"] == 2, str(donggu))
        check("하수 원인 비중 = 2/5", donggu["sewer_cause_share"] == 0.4)
        check("0건도 관측값 (남구 하수 원인 0)",
              by_code["29155"]["sewer_subsidence_count"] == 0
              and by_code["29155"]["sewer_cause_share"] == 0.0,
              str(by_code["29155"]["sewer_cause_share"]))

        check("연장 None 이면 침하율 None (건수는 보존)",
              by_code["29140"]["subsidence_per_km"] is None
              and by_code["29140"]["subsidence_count"] == 3, "")
        check("연장 0 이면 침하율 None (0-나눗셈 없음)",
              by_code["29155"]["subsidence_per_km"] is None, "")
        check("km 환산은 연장 있는 곳만",
              donggu["pipe_total_length_km"] == 10.0
              and by_code["29140"]["pipe_total_length_km"] is None, "")

        check("노후율·관종 조인", donggu["aged_ratio_25y"] == 0.5
              and donggu["concrete_ratio"] == 0.3, "")
        check("결측 사유 전파",
              by_code["29140"]["age_missing_reason"] == "연도별 보고 부족", "")
        check("강수·인구·중점관리구역 조인",
              donggu["rainfall_30d_mm"] == 120.5
              and donggu["population"] == 100000
              and donggu["heavy_rain_zone_ratio"] == 0.05, "")
        check("자료 없는 구역은 전 지표 None",
              all(by_code["29155"].get(name) is None for name in INDICATORS), "")
        check("is_priority_target 은 bool",
              donggu["is_priority_target"] is True
              and by_code["29140"]["is_priority_target"] is False, "")

        print()
        print("도우미 함수")
        check("observed(): 결측 제외", observed(merged, "aged_ratio_25y") == [0.5])
        check("available_indicators(): 값이 하나라도 있는 지표만",
              "aged_ratio_25y" in available_indicators(merged)
              and "population" in available_indicators(merged), "")
        empty = [{"sgg_code": "x", "aged_ratio_25y": None}]
        check("available_indicators(): 전부 결측이면 빠짐",
              available_indicators(empty) == [], str(available_indicators(empty)))
        complete = complete_rows(merged, ["aged_ratio_25y", "population"])
        check("complete_rows(): 전 지표 관측 행만",
              [row["sgg_code"] for row in complete] == ["29110"],
              str([row["sgg_code"] for row in complete]))

    print()
    print("커밋된 실제 산출물로 병합 (파일이 없으면 건너뜀)")
    real_master = paths.DATA / paths.DISTRICTS_MASTER_FILE
    real_sewer = paths.DATA / paths.SEWER_BY_SGG_FILE
    if real_master.exists() and real_sewer.exists():
        merged, sources = merge(paths.DATA)
        check("마스터 전 구역이 결과에 있음", len(merged) == sources["master"],
              f"{len(merged)} vs {sources['master']}")
        check("시군구 코드는 5자리 숫자",
              all(len(row["sgg_code"]) == 5 and row["sgg_code"].isdigit()
                  for row in merged), "")
        check("코드 중복 없음",
              len({row["sgg_code"] for row in merged}) == len(merged), "")
        ratio_names = [
            "aged_ratio_25y", "concrete_ratio", "combined_sewer_ratio",
            "unconnected_ratio", "heavy_rain_zone_ratio", "large_diameter_ratio",
        ]
        check("비율 지표는 결측이거나 [0,1]",
              all(row[name] is None or 0.0 <= row[name] <= 1.0
                  for row in merged for name in ratio_names), "")
        check("침하율은 결측이거나 0 이상",
              all(row["subsidence_per_km"] is None or row["subsidence_per_km"] >= 0
                  for row in merged), "")
        check("점수에 쓸 지표가 하나 이상 관측됨",
              len(available_indicators(merged)) > 0,
              str(available_indicators(merged)))
    else:
        print("  (건너뜀: data/ 산출물 없음 — 클론 직후 상태)")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
