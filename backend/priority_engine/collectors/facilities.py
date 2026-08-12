from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402
from mapping.sgg_normalize import SggResolver, diff_codes  # noqa: E402

# 데이터 기준시점. 파일명의 _20241231 과 일치시킵니다.
REFERENCE_DATE = date(2024, 12, 31)

COL_SIDO = "시도"
COL_SGG = "구군"
COL_NAME = "시설명"
COL_CAPACITY = "시설용량"
COL_START = "가동개시일"
COL_COMPLETE = "준공일"

# 하수관로 데이터 컬럼: 처리장명으로 조인하고 연장으로 가중합니다.
COL_SEWER_PLANT = "하수처리장명"
COL_SEWER_LENGTH = "하수관로_총시설연장"

DAYS_PER_YEAR = 365.2425


def parse_date(value: object) -> date | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    for separator in ("-", ".", "/"):
        text = text.replace(separator, "-")

    parts = [part for part in text.split("-") if part]
    if len(parts) < 3:
        return None

    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def to_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if text in {"", "-", "null", "None"}:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def load_csv(path: Path, required: str, label: str) -> list[dict]:
    for encoding in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            if rows and required in rows[0]:
                print(f"  {label}: {encoding}, {len(rows):,}행")
                return rows
        except (UnicodeDecodeError, LookupError):
            continue

    raise SystemExit(
        f"CSV를 읽지 못했습니다: {path}\n  {required!r} 컬럼이 있는지 확인하세요."
    )


def normalize_name(value: object) -> str:
    return str(value or "").strip().replace(" ", "")


def build_plant_ages(rows: list[dict], stats: dict) -> tuple[dict, dict]:
    by_sido_name: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    by_name: dict[str, list[tuple[float, str]]] = defaultdict(list)

    for row in rows:
        started = parse_date(row.get(COL_START)) or parse_date(row.get(COL_COMPLETE))

        if started is None:
            stats["no_date"] += 1
            continue

        if started > REFERENCE_DATE:
            # 기준일 이후 가동 예정. 연령 0으로 두면 신규 시설이 최우수로 잡히므로 제외.
            stats["future_date"] += 1
            continue

        age = (REFERENCE_DATE - started).days / DAYS_PER_YEAR
        name = normalize_name(row.get(COL_NAME))
        sido = normalize_name(row.get(COL_SIDO))

        by_sido_name[(sido, name)].append((age, str(row.get(COL_NAME) or "")))
        by_name[name].append((age, str(row.get(COL_NAME) or "")))

    return by_sido_name, by_name


def aggregate(
    facility_rows: list[dict],
    sewer_rows: list[dict],
    resolver: SggResolver,
) -> tuple[dict[str, dict], dict]:
    stats = {
        "facility_rows": len(facility_rows),
        "sewer_rows": len(sewer_rows),
        "no_date": 0,
        "future_date": 0,
        "matched_exact": 0,
        "matched_name_only": 0,
        "unmatched_plant": 0,
        "unresolved_sgg": 0,
        "no_length": 0,
    }

    by_sido_name, by_name = build_plant_ages(facility_rows, stats)
    unmatched_samples: list[str] = []

    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "length_total": 0.0,
            "weighted_age_sum": 0.0,
            "plants": {},
            "oldest_age": None,
            "oldest_name": None,
        }
    )

    for row in sewer_rows:
        code = resolver.resolve(row.get(COL_SIDO), row.get(COL_SGG))
        if code is None:
            stats["unresolved_sgg"] += 1
            continue

        plant = normalize_name(row.get(COL_SEWER_PLANT))
        sido = normalize_name(row.get(COL_SIDO))

        candidates = by_sido_name.get((sido, plant))
        if candidates:
            stats["matched_exact"] += 1
        else:
            candidates = by_name.get(plant)
            if candidates:
                stats["matched_name_only"] += 1

        if not candidates:
            stats["unmatched_plant"] += 1
            if len(unmatched_samples) < 10:
                unmatched_samples.append(
                    f"{row.get(COL_SIDO)} {row.get(COL_SGG)} / {row.get(COL_SEWER_PLANT)!r}"
                )
            continue

        # 같은 이름의 처리장이 여러 계열일 수 있으므로 가장 오래된 것을 씁니다.
        # 노후도 지표이므로 보수적(위험한) 쪽을 택합니다.
        age, display_name = max(candidates, key=lambda pair: pair[0])

        length = to_number(row.get(COL_SEWER_LENGTH))
        if length is None or length <= 0:
            stats["no_length"] += 1
            # 연장이 없으면 가중치를 줄 수 없으나 처리장 목록에는 남깁니다.
            weight = 0.0
        else:
            weight = length

        bucket = buckets[code]
        bucket["length_total"] += weight
        bucket["weighted_age_sum"] += age * weight
        bucket["plants"][display_name] = round(age, 1)

        if bucket["oldest_age"] is None or age > bucket["oldest_age"]:
            bucket["oldest_age"] = age
            bucket["oldest_name"] = display_name

    result: dict[str, dict] = {}
    for code, bucket in buckets.items():
        ages = sorted(bucket["plants"].values())
        middle = len(ages) // 2
        median = (
            ages[middle]
            if len(ages) % 2 == 1
            else (ages[middle - 1] + ages[middle]) / 2.0
        )

        weighted = (
            bucket["weighted_age_sum"] / bucket["length_total"]
            if bucket["length_total"] > 0
            else None
        )
        # 연장 가중이 불가능하면 처리장 연령 중앙값으로 대체합니다.
        if weighted is None and ages:
            weighted = median

        result[code] = {
            "sgg_code": code,
            "full_name": resolver.name_of(code),
            "plant_count": len(bucket["plants"]),
            # 점수 산정에 쓰는 값: 관로 연장 가중 평균
            "facility_avg_age_years": round(weighted, 3) if weighted is not None else None,
            "facility_median_age_years": round(median, 3) if ages else None,
            "facility_max_age_years": round(max(ages), 3) if ages else None,
            "facility_min_age_years": round(min(ages), 3) if ages else None,
            "oldest_facility": bucket["oldest_name"],
            "plants": bucket["plants"],
            "matched_pipe_length": round(bucket["length_total"], 1),
        }

    stats["unmatched_samples"] = unmatched_samples
    return result, stats


def report(
    aggregated: dict[str, dict],
    resolver: SggResolver,
    stats: dict,
    master_codes: set[str],
) -> None:
    matched = stats["matched_exact"] + stats["matched_name_only"]
    print()
    print(f"처리시설 {stats['facility_rows']:,}행 / 하수관로 {stats['sewer_rows']:,}행")
    if stats["no_date"]:
        print(f"  가동개시일·준공일 모두 없음 {stats['no_date']:,}개 시설 → 제외")
    if stats["future_date"]:
        print(f"  기준일({REFERENCE_DATE}) 이후 가동 {stats['future_date']:,}개 시설 → 제외")

    print()
    print("=== 처리장명 조인 ===")
    print(f"  (시도+시설명) 정확 매칭  {stats['matched_exact']:,}행")
    print(f"  (시설명) 매칭: 광역 처리장 {stats['matched_name_only']:,}행")
    print(f"  매칭 합계 {matched:,} / {stats['sewer_rows']:,} ({matched / stats['sewer_rows']:.1%})")
    if stats["unmatched_plant"]:
        print(f"  [!] 미매칭 {stats['unmatched_plant']:,}행")
        for sample in stats["unmatched_samples"]:
            print(f"      {sample}")
    if stats["unresolved_sgg"]:
        print(f"  [!] 시군구 미해결 {stats['unresolved_sgg']:,}행")
    if stats["no_length"]:
        print(f"  연장 결측 {stats['no_length']:,}행 (가중치 제외, 처리장 목록에는 포함)")

    if resolver.unresolved:
        print()
        print(resolver.report_unresolved())

    print()
    print(f"집계된 시군구 {len(aggregated)}개 / 마스터 {len(master_codes)}개")

    only_data, only_map = diff_codes(set(aggregated), master_codes)
    if only_data:
        print(f"  [!] 데이터에만 있음 {len(only_data)}개: {sorted(only_data)}")
    if only_map:
        names = [resolver.name_of(code) for code in sorted(only_map)]
        print(f"  [!] 연령을 못 구한 시군구 {len(only_map)}개:")
        for name in names[:15]:
            print(f"      {name}")
        if len(names) > 15:
            print(f"      ... 외 {len(names) - 15}개")

    ages = [
        row["facility_avg_age_years"]
        for row in aggregated.values()
        if row["facility_avg_age_years"] is not None
    ]
    if ages:
        ages_sorted = sorted(ages)
        middle = len(ages_sorted) // 2
        print()
        print(f"=== 관로 연장 가중 평균 연령 분포 (n={len(ages)}) ===")
        print(f"  최소 {min(ages):.1f}년 / 중앙 {ages_sorted[middle]:.1f}년 / 최대 {max(ages):.1f}년")

        print()
        print("=== 가장 오래된 시군구 10곳 ===")
        oldest = sorted(
            (row for row in aggregated.values() if row["facility_avg_age_years"]),
            key=lambda row: -row["facility_avg_age_years"],
        )[:10]
        for row in oldest:
            print(
                f"  {row['facility_avg_age_years']:5.1f}년  {row['full_name']:22} "
                f"(처리장 {row['plant_count']:2d}개, 최고령 {row['facility_max_age_years']:.0f}년 "
                f"{row['oldest_facility']})"
            )

        print()
        print("=== 서울 자치구 (기존 방식에서 결측이던 지역) ===")
        seoul = sorted(code for code in aggregated if code.startswith("11"))
        for code in seoul[:8]:
            row = aggregated[code]
            plants = ", ".join(f"{n}({a:.0f}년)" for n, a in row["plants"].items())
            print(f"  {row['full_name']:18} {row['facility_avg_age_years']:5.1f}년  ← {plants}")
        print(f"  (서울 {len(seoul)}/25개 구 커버)")

    print()
    print("=== 광주 5개 구 ===")
    for code in sorted(c for c in aggregated if c.startswith("29")):
        row = aggregated[code]
        print(
            f"  {code} {row['full_name']:18} 처리장 {row['plant_count']:2d}개  "
            f"가중평균 {row['facility_avg_age_years']:5.1f}년  "
            f"최고령 {row['facility_max_age_years']:5.1f}년"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="처리시설 연령을 시군구별로 집계합니다.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=paths.FACILITIES_CSV,
        help="공공하수처리시설 현황 CSV",
    )
    parser.add_argument(
        "--sewer-csv",
        type=Path,
        default=paths.SEWER_CSV,
        help="하수관로시설별설치현황 CSV (처리장명 조인 및 연장 가중용)",
    )
    parser.add_argument(
        "--master",
        type=Path,
        default=paths.DISTRICTS_MASTER,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=paths.FACILITY_AGE_BY_SGG,
    )
    args = parser.parse_args()

    resolver = SggResolver.from_master(args.master)
    master_codes = set(resolver.names)
    print(f"마스터 로드: {len(master_codes)}개 ({args.master})")
    print(f"기준일: {REFERENCE_DATE}")

    facility_rows = load_csv(args.csv, COL_START, "처리시설 CSV")
    sewer_rows = load_csv(args.sewer_csv, COL_SEWER_PLANT, "하수관로 CSV")

    aggregated, stats = aggregate(facility_rows, sewer_rows, resolver)
    report(aggregated, resolver, stats, master_codes)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = sorted(aggregated.values(), key=lambda row: row["sgg_code"])
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print()
    print(f"→ {args.out} ({len(payload)}개)")


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("날짜 파싱")
    check("하이픈", parse_date("1990-06-15") == date(1990, 6, 15))
    check("점 구분", parse_date("2010.01.01") == date(2010, 1, 1))
    check("슬래시·한 자리", parse_date("2010/1/1") == date(2010, 1, 1))
    check("None·빈 값 → None", parse_date(None) is None and parse_date(" ") is None)
    check("연-월만 있으면 None", parse_date("2010-01") is None)
    check("불가능한 날짜 → None (예외 없음)", parse_date("2010-13-01") is None)
    check("쓰레기 값 → None", parse_date("미정") is None)

    print()
    print("숫자·이름 정규화")
    check("천단위 구분자", to_number("1,000.5") == 1000.5)
    check("결측 표기 → None",
          all(to_number(v) is None for v in (None, "", "-", "null")), "")
    check("이름 공백 제거", normalize_name("동구 처리장") == "동구처리장")
    check("None → 빈 문자열", normalize_name(None) == "")

    print()
    print("시설 연령 집계")
    from mapping.sgg_normalize import SggResolver

    resolver = SggResolver.from_rows([
        {"sgg_code": "29110", "sido_name": "광주광역시", "sgg_name": "동구",
         "full_name": "광주광역시 동구"},
        {"sgg_code": "29140", "sido_name": "광주광역시", "sgg_name": "서구",
         "full_name": "광주광역시 서구"},
    ])

    facility_rows = [
        # 같은 이름의 처리장이 두 계열: 노후도 지표이므로 오래된 쪽(1990)을 써야 함
        {COL_SIDO: "광주광역시", COL_NAME: "동구처리장", COL_START: "1990-06-15"},
        {COL_SIDO: "광주광역시", COL_NAME: "동구처리장", COL_START: "2010-01-01"},
        # 가동개시일이 없으면 준공일로 대체
        {COL_SIDO: "광주광역시", COL_NAME: "준공만", COL_COMPLETE: "2005-03-01"},
        # 날짜가 둘 다 없으면 제외
        {COL_SIDO: "광주광역시", COL_NAME: "무일자"},
        # 기준일(2024-12-31) 이후 가동 예정: 연령 0으로 두면 최우수로 잡히므로 제외
        {COL_SIDO: "광주광역시", COL_NAME: "미래", COL_START: "2026-01-01"},
        # 광역 처리장: 시도가 달라도 시설명만으로 매칭되어야 함
        {COL_SIDO: "서울특별시", COL_NAME: "광역센터", COL_START: "2000-12-31"},
    ]
    sewer_rows = [
        {COL_SIDO: "광주광역시", COL_SGG: "동구", COL_SEWER_PLANT: "동구처리장",
         COL_SEWER_LENGTH: "1000"},
        # 공백 차이는 정규화로 흡수 — 같은 처리장으로 매칭
        {COL_SIDO: "광주광역시", COL_SGG: "동구", COL_SEWER_PLANT: "동구 처리장",
         COL_SEWER_LENGTH: "500"},
        {COL_SIDO: "광주광역시", COL_SGG: "동구", COL_SEWER_PLANT: "준공만",
         COL_SEWER_LENGTH: "500"},
        # 시도+시설명 매칭 실패 → 시설명 단독(광역) 매칭. 연장 결측이라 가중치 0
        {COL_SIDO: "광주광역시", COL_SGG: "서구", COL_SEWER_PLANT: "광역센터",
         COL_SEWER_LENGTH: ""},
        # 처리시설 목록에 없는 처리장
        {COL_SIDO: "광주광역시", COL_SGG: "동구", COL_SEWER_PLANT: "없는처리장",
         COL_SEWER_LENGTH: "100"},
        # 시군구 미해결
        {COL_SIDO: "없는도", COL_SGG: "없는시", COL_SEWER_PLANT: "동구처리장"},
    ]

    aggregated, stats = aggregate(facility_rows, sewer_rows, resolver)

    check("날짜 없는 시설 제외 집계", stats["no_date"] == 1, str(stats["no_date"]))
    check("미래 가동 시설 제외 집계", stats["future_date"] == 1, str(stats["future_date"]))
    check("정확 매칭 3 / 광역 매칭 1",
          stats["matched_exact"] == 3 and stats["matched_name_only"] == 1, str(stats))
    check("미매칭 1 / 시군구 미해결 1",
          stats["unmatched_plant"] == 1 and stats["unresolved_sgg"] == 1, str(stats))
    check("연장 결측 1행", stats["no_length"] == 1, str(stats["no_length"]))

    age_1990 = (REFERENCE_DATE - date(1990, 6, 15)).days / DAYS_PER_YEAR
    age_2005 = (REFERENCE_DATE - date(2005, 3, 1)).days / DAYS_PER_YEAR
    age_2000 = (REFERENCE_DATE - date(2000, 12, 31)).days / DAYS_PER_YEAR

    donggu = aggregated["29110"]
    expected = (age_1990 * 1500 + age_2005 * 500) / 2000
    check("연장 가중 평균 연령", donggu["facility_avg_age_years"] == round(expected, 3),
          f"{donggu['facility_avg_age_years']} vs {round(expected, 3)}")
    check("같은 이름 두 계열이면 오래된 쪽 (보수적)",
          donggu["plants"]["동구처리장"] == round(age_1990, 1),
          str(donggu["plants"]))
    check("준공일 대체 시설도 포함", donggu["plant_count"] == 2, str(donggu["plants"]))
    # 최고령·최저령은 처리장 목록(소수 1자리 반올림)에서 계산됩니다.
    check("최고령 시설 기록",
          donggu["oldest_facility"] == "동구처리장"
          and donggu["facility_max_age_years"] == round(age_1990, 1),
          str((donggu["oldest_facility"], donggu["facility_max_age_years"])))
    check("최저령 = 준공만", donggu["facility_min_age_years"] == round(age_2005, 1),
          str(donggu["facility_min_age_years"]))
    check("매칭 연장 합", donggu["matched_pipe_length"] == 2000.0,
          str(donggu["matched_pipe_length"]))

    seogu = aggregated["29140"]
    check("연장이 전부 결측이면 중앙값으로 대체",
          seogu["facility_avg_age_years"] == round(age_2000, 1),
          str(seogu["facility_avg_age_years"]))
    check("가중치 0 이어도 처리장 목록에는 남음",
          seogu["plant_count"] == 1 and seogu["matched_pipe_length"] == 0.0,
          str(seogu))
    check("연령은 전부 양수",
          all(row["facility_avg_age_years"] > 0 for row in aggregated.values()), "")

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        raise SystemExit(_self_test())
