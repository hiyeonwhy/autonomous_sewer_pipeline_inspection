from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.odcloud import fetch_all  # noqa: E402
import paths  # noqa: E402
from mapping.sgg_normalize import SggResolver, diff_codes  # noqa: E402

# 연도별 uddi. Swagger에서 확정 (인증 불필요로 조회 가능).
#   https://infuser.odcloud.kr/oas/docs?namespace=15118453/v1
ENDPOINTS_BY_YEAR = {
    2021: "15118453/v1/uddi:f7720af5-3272-40e1-933b-998b6b599f21",
    2022: "15118453/v1/uddi:fbfb1729-a4b9-44b2-9ebf-d6796c66a7eb",
    2023: "15118453/v1/uddi:0a268994-e94b-42da-b23b-92d228cb87fc",
    2024: "15118453/v1/uddi:288c613e-60e7-4e6a-a51b-87c38e42caf6",
}

ENDPOINT_2024 = ENDPOINTS_BY_YEAR[2024]

# Swagger에서 확정한 컬럼명. JSON 키가 한글 그대로입니다.
COL_SIDO = "시도"
COL_SGG = "구군"
COL_PLANT = "하수처리장명"
COL_CAPACITY = "하수처리 시설용량"
COL_AREA_TOTAL = "처리구역면적_합계"
COL_AREA_SEPARATE = "처리구역면적_분류식"
COL_DRAIN_SEP_TOTAL = "배수설비_분류식합계(개소)"
COL_DRAIN_SEP_UNCONN = "배수설비_분류식미연결(개소)"
COL_PIPE_TOTAL = "하수관로_총시설연장"
COL_BRANCH_TOTAL = "하수관로_지선합계"
COL_BRANCH_COMBINED = "하수관로_지선합류식"


def to_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in {"", "-", "null", "None", "N/A", "해당없음"}:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator <= 0:
        return None
    return numerator / denominator


def load_from_csv(path: Path) -> list[dict]:
    for encoding in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            if rows and COL_SIDO in rows[0]:
                print(f"  CSV 인코딩: {encoding}, {len(rows):,}행")
                return rows
        except (UnicodeDecodeError, LookupError):
            continue

    raise SystemExit(
        f"CSV를 읽지 못했습니다: {path}\n"
        f"  '{COL_SIDO}' 컬럼이 있는지, 인코딩이 UTF-8/CP949인지 확인하세요."
    )


def aggregate_by_sgg(
    rows: list[dict],
    resolver: SggResolver,
) -> tuple[dict[str, dict], dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "plant_count": 0,
            "treatment_capacity": 0.0,
            "area_total": 0.0,
            "area_separate": 0.0,
            "drain_separate_total": 0.0,
            "drain_separate_unconnected": 0.0,
            "pipe_total_length": 0.0,
            "branch_total": 0.0,
            "branch_combined": 0.0,
            # 결측을 0으로 뭉개지 않기 위해 실제 값이 들어온 횟수를 따로 셉니다.
            "_has_pipe": 0,
            "_has_branch": 0,
            "_has_drain": 0,
            "_has_area": 0,
        }
    )

    stats = {"total": len(rows), "resolved": 0, "unresolved": 0, "blank_sgg": 0}

    for row in rows:
        sido = row.get(COL_SIDO)
        sgg = row.get(COL_SGG)

        if not str(sgg or "").strip():
            stats["blank_sgg"] += 1

        code = resolver.resolve(sido, sgg)
        if code is None:
            stats["unresolved"] += 1
            continue

        stats["resolved"] += 1
        bucket = buckets[code]
        bucket["plant_count"] += 1

        for column, key, flag in (
            (COL_CAPACITY, "treatment_capacity", None),
            (COL_AREA_TOTAL, "area_total", "_has_area"),
            (COL_AREA_SEPARATE, "area_separate", None),
            (COL_DRAIN_SEP_TOTAL, "drain_separate_total", "_has_drain"),
            (COL_DRAIN_SEP_UNCONN, "drain_separate_unconnected", None),
            (COL_PIPE_TOTAL, "pipe_total_length", "_has_pipe"),
            (COL_BRANCH_TOTAL, "branch_total", "_has_branch"),
            (COL_BRANCH_COMBINED, "branch_combined", None),
        ):
            value = to_number(row.get(column))
            if value is not None:
                bucket[key] += value
                if flag:
                    bucket[flag] += 1

    # 파생 지표 계산
    result: dict[str, dict] = {}
    for code, bucket in buckets.items():
        has_pipe = bucket.pop("_has_pipe") > 0
        has_branch = bucket.pop("_has_branch") > 0
        has_drain = bucket.pop("_has_drain") > 0
        has_area = bucket.pop("_has_area") > 0

        result[code] = {
            "sgg_code": code,
            "full_name": resolver.name_of(code),
            "plant_count": bucket["plant_count"],
            "treatment_capacity": bucket["treatment_capacity"],
            # 노출량. 점수 정규화의 분모입니다. 단위는 Phase 0에서 확인 필요(km 추정).
            "pipe_total_length": bucket["pipe_total_length"] if has_pipe else None,
            # 노후 프록시 1: 지선 중 합류식 비율. 합류식은 구세대 방식입니다.
            "combined_sewer_ratio": (
                safe_ratio(bucket["branch_combined"], bucket["branch_total"])
                if has_branch
                else None
            ),
            # 관리 부실: 분류식으로 만들었는데 연결 안 된 배수설비 비율
            "unconnected_ratio": (
                safe_ratio(
                    bucket["drain_separate_unconnected"],
                    bucket["drain_separate_total"],
                )
                if has_drain
                else None
            ),
            # 면적 기준 분류식화율. 처리장별 `분류식화율`을 단순 평균하면 규모가
            # 다른 처리장이 동일 가중치를 받으므로 면적 합으로 다시 계산합니다.
            "separate_ratio": (
                safe_ratio(bucket["area_separate"], bucket["area_total"])
                if has_area
                else None
            ),
            "_raw": {
                "branch_total": bucket["branch_total"],
                "branch_combined": bucket["branch_combined"],
                "drain_separate_total": bucket["drain_separate_total"],
                "drain_separate_unconnected": bucket["drain_separate_unconnected"],
                "area_total": bucket["area_total"],
                "area_separate": bucket["area_separate"],
            },
        }

    return result, stats


def report(
    aggregated: dict[str, dict],
    resolver: SggResolver,
    stats: dict,
    master_codes: set[str],
) -> None:
    print()
    print(f"입력 {stats['total']:,}행 → 매칭 {stats['resolved']:,} / 미해결 {stats['unresolved']:,}")
    if stats["blank_sgg"]:
        print(f"  `구군` 공란 {stats['blank_sgg']:,}행 (포털 명시 함정: 시도 단독으로 처리)")

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
        print(f"  [!] 마스터에만 있음 {len(only_map)}개 (하수관로 데이터 없음):")
        for name in names[:20]:
            print(f"      {name}")
        if len(names) > 20:
            print(f"      ... 외 {len(names) - 20}개")

    # 결측 현황: 점수 계산에서 제외되거나 대체값이 필요한 항목
    print()
    for field in ("pipe_total_length", "combined_sewer_ratio", "unconnected_ratio", "separate_ratio"):
        missing = [code for code, row in aggregated.items() if row[field] is None]
        print(f"  {field:24s} 결측 {len(missing):3d}개")

    print()
    print("=== 광주 5개 구 ===")
    for code in sorted(c for c in aggregated if c.startswith("29")):
        row = aggregated[code]
        pipe = row["pipe_total_length"]
        combined = row["combined_sewer_ratio"]
        unconn = row["unconnected_ratio"]
        print(
            f"  {code} {row['full_name']:18s} "
            f"처리장 {row['plant_count']:2d}  "
            f"연장 {'N/A' if pipe is None else format(pipe, ',.1f'):>12}  "
            f"합류식 {'  N/A' if combined is None else format(combined, '.3f')}  "
            f"미연결 {'  N/A' if unconn is None else format(unconn, '.3f')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="하수관로시설별설치현황을 수집해 시군구로 집계합니다.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--key", help="공공데이터포털 Decoding 인증키")
    source.add_argument(
        "--csv",
        type=Path,
        nargs="?",
        const=paths.SEWER_CSV,
        help=f"CSV 경로. 값 없이 --csv 만 주면 {paths.SEWER_CSV.name}",
    )
    parser.add_argument(
        "--master",
        type=Path,
        default=paths.DISTRICTS_MASTER,
        help="시군구 마스터 (기본: 기초자치단체 229개)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=paths.SEWER_BY_SGG,
    )
    parser.add_argument("--endpoint", default=ENDPOINT_2024)
    args = parser.parse_args()

    resolver = SggResolver.from_master(args.master)
    master_codes = set(resolver.names)
    print(f"마스터 로드: {len(master_codes)}개 ({args.master})")

    if args.csv:
        rows = load_from_csv(args.csv)
    else:
        print(f"odcloud 수집: {args.endpoint}")
        rows = fetch_all(args.endpoint, args.key)

    if not rows:
        raise SystemExit("수집된 행이 없습니다.")

    # 첫 행의 키를 찍어 Swagger 컬럼명과 일치하는지 즉시 확인
    print()
    print("응답 컬럼 확인:")
    missing_columns = [
        column
        for column in (COL_SIDO, COL_SGG, COL_PIPE_TOTAL, COL_BRANCH_TOTAL)
        if column not in rows[0]
    ]
    if missing_columns:
        print(f"  [!] 예상 컬럼 없음: {missing_columns}")
        print(f"  실제 컬럼: {list(rows[0].keys())}")
    else:
        print(f"  주요 컬럼 확인됨 (총 {len(rows[0])}개)")

    aggregated, stats = aggregate_by_sgg(rows, resolver)
    report(aggregated, resolver, stats, master_codes)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = sorted(aggregated.values(), key=lambda row: row["sgg_code"])
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print()
    print(f"→ {args.out} ({len(payload)}개)")


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    import contextlib
    import io

    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("숫자 변환")
    check("천단위 구분자·공백", to_number("1,234 567.5") == 1234567.5)
    check("정수·실수 통과", to_number(3) == 3.0 and to_number(2.5) == 2.5)
    check("None → None", to_number(None) is None)
    check("빈 문자열 → None", to_number("") is None)
    check("'-' → None", to_number("-") is None)
    check("'해당없음' → None", to_number("해당없음") is None)
    check("쓰레기 값 → None (예외로 죽지 않음)", to_number("점검중") is None)
    check("0 은 0 (None 아님)", to_number("0") == 0.0)

    print()
    print("안전 나눗셈")
    check("정상", safe_ratio(1.0, 4.0) == 0.25)
    check("분자 None → None", safe_ratio(None, 4.0) is None)
    check("분모 None → None", safe_ratio(1.0, None) is None)
    check("분모 0 → None (0-나눗셈 없음)", safe_ratio(1.0, 0.0) is None)
    check("분모 음수 → None", safe_ratio(1.0, -1.0) is None)

    print()
    print("시군구 집계")
    from mapping.sgg_normalize import SggResolver

    master_rows = [
        {"sgg_code": "29110", "sido_name": "광주광역시", "sgg_name": "동구",
         "full_name": "광주광역시 동구"},
        {"sgg_code": "29140", "sido_name": "광주광역시", "sgg_name": "서구",
         "full_name": "광주광역시 서구"},
        {"sgg_code": "36110", "sido_name": "세종특별자치시", "sgg_name": "세종시",
         "full_name": "세종특별자치시 세종시"},
    ]
    resolver = SggResolver.from_rows(master_rows)

    rows = [
        {  # 동구 처리장 1: 전 컬럼 정상
            COL_SIDO: "광주광역시", COL_SGG: "동구", COL_PLANT: "동구1",
            COL_CAPACITY: "100", COL_AREA_TOTAL: "10", COL_AREA_SEPARATE: "4",
            COL_DRAIN_SEP_TOTAL: "200", COL_DRAIN_SEP_UNCONN: "50",
            COL_PIPE_TOTAL: "1,000.5", COL_BRANCH_TOTAL: "300",
            COL_BRANCH_COMBINED: "150",
        },
        {  # 동구 처리장 2: 일부 결측
            COL_SIDO: "광주광역시", COL_SGG: "동구", COL_PLANT: "동구2",
            COL_CAPACITY: "50", COL_AREA_TOTAL: "-", COL_AREA_SEPARATE: "",
            COL_DRAIN_SEP_TOTAL: "", COL_DRAIN_SEP_UNCONN: "",
            COL_PIPE_TOTAL: "500", COL_BRANCH_TOTAL: "100",
            COL_BRANCH_COMBINED: "30",
        },
        {  # 서구: 관로·지선·배수 전부 결측
            COL_SIDO: "광주광역시", COL_SGG: "서구", COL_PLANT: "서구1",
            COL_CAPACITY: "70", COL_PIPE_TOTAL: "", COL_BRANCH_TOTAL: "-",
        },
        {  # 세종: `구군` 공란 (포털 명시 함정) — 단층제라 시도만으로 확정
            COL_SIDO: "세종특별자치시", COL_SGG: "", COL_PLANT: "세종1",
            COL_PIPE_TOTAL: "800",
        },
        {  # 미해결 지역
            COL_SIDO: "없는도", COL_SGG: "없는시", COL_PLANT: "유령",
            COL_PIPE_TOTAL: "999",
        },
    ]

    aggregated, stats = aggregate_by_sgg(rows, resolver)

    check("매칭 4 / 미해결 1", stats["resolved"] == 4 and stats["unresolved"] == 1,
          str(stats))
    check("`구군` 공란 1행 집계", stats["blank_sgg"] == 1, str(stats["blank_sgg"]))
    check("시군구 3개 산출", len(aggregated) == 3, str(sorted(aggregated)))

    donggu = aggregated["29110"]
    check("처리장 수 합산", donggu["plant_count"] == 2, str(donggu["plant_count"]))
    check("연장 합산 (쉼표 파싱 포함)", donggu["pipe_total_length"] == 1500.5,
          str(donggu["pipe_total_length"]))
    check("합류식 비율 = 지선합류식 합 / 지선합계 합",
          donggu["combined_sewer_ratio"] == (150 + 30) / (300 + 100),
          str(donggu["combined_sewer_ratio"]))
    check("미연결 비율 = 50/200", donggu["unconnected_ratio"] == 0.25,
          str(donggu["unconnected_ratio"]))
    check("면적 분류식화율 = 4/10", donggu["separate_ratio"] == 0.4,
          str(donggu["separate_ratio"]))

    seogu = aggregated["29140"]
    check("관로 결측은 None (0으로 뭉개지 않음)", seogu["pipe_total_length"] is None,
          str(seogu["pipe_total_length"]))
    check("지선 결측이면 합류식 비율 None", seogu["combined_sewer_ratio"] is None)
    check("배수 결측이면 미연결 비율 None", seogu["unconnected_ratio"] is None)
    check("면적 결측이면 분류식화율 None", seogu["separate_ratio"] is None)
    check("세종은 시도 단독으로 매칭", aggregated["36110"]["pipe_total_length"] == 800.0)

    print()
    print("report(): 연장 결측 행에서도 죽지 않음 (f-string 포맷 회귀)")
    # 서구(29xxx)는 pipe_total_length=None 이라 광주 표에 그대로 들어갑니다.
    # 조건식 전체가 아니라 format() 결과에만 정렬 폭을 걸어야 합니다.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            report(aggregated, resolver, stats, set(resolver.names))
        check("report() 정상 종료", True)
    except (TypeError, ValueError) as error:
        check("report() 정상 종료", False, f"{type(error).__name__}: {error}")
    output = buffer.getvalue()
    check("결측은 N/A 로 표기", "N/A" in output, output[-300:])
    check("광주 표에 서구 포함", "서구" in output, "")

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
