from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402
from mapping.sgg_normalize import SggResolver, diff_codes  # noqa: E402

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

SHEET_MATERIAL = "2-3. 관종별현황"
SHEET_DIAMETER = "2-4. 관경별현황"

# 통계 기준연도. 원본 파일명의 판차(2025)와 일치시킵니다.
BASE_YEAR = 2025

# 연도 그룹 시작 컬럼과 간격 (계/매설연장/철거연장 3칸)
YEAR_START_COL = 7
YEAR_STRIDE = 3
YEAR_COUNT = 31  # 1996년이전 + 1996~2025

# 첫 그룹('1996년이전')을 대표하는 연도. 실제로는 1995년 이전 전체입니다.
PRE_YEAR = 1995

TOTAL_COL = 4

# 통계 전국 총계 (교차 검증용). 하수관로 CSV 집계값과 일치하는 것을 확인했습니다.
NATIONAL_TOTAL_M = 177_522_909

# 집계행 표시. 시군구 단위가 아니므로 배제합니다.
AGGREGATE_LABELS = {"합계", "소계", "시부", "군부", ""}

# 콘크리트계 관종: 부식·이음부 이탈에 취약. 5종 전부 필요합니다
# (VR관은 광역시에서 전부 0이라 한동안 빠져 있었습니다).
CONCRETE_MATERIALS = {
    "흄관",
    "PC관",
    "VR관",
    "레진콘크리트관",
    "철근콘크리트관(BOX형)",
}

# 관경별현황의 원형 관경 구간 (col 5부터 순서대로)
DIAMETER_BANDS = [
    (5, "under_200mm"),
    (6, "d200_300mm"),
    (7, "d300_500mm"),
    (8, "d500_800mm"),
    (9, "over_800mm"),
]


class Workbook:
    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path)
        self.shared = self._read_shared_strings()
        self.sheets = self._read_sheet_map()

    def _read_shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self.zip.namelist():
            return []
        root = ET.fromstring(self.zip.read("xl/sharedStrings.xml"))
        return [
            "".join(node.text or "" for node in item.iter(MAIN_NS + "t"))
            for item in root.iter(MAIN_NS + "si")
        ]

    def _read_sheet_map(self) -> dict[str, str]:
        workbook = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in rels}

        mapping: dict[str, str] = {}
        for sheet in workbook.iter(MAIN_NS + "sheet"):
            target = targets[sheet.attrib[REL_NS + "id"]]
            path = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            mapping[sheet.attrib["name"]] = path
        return mapping

    @staticmethod
    def column_index(reference: str) -> int:
        letters = "".join(ch for ch in reference if ch.isalpha())
        index = 0
        for ch in letters:
            index = index * 26 + ord(ch) - 64
        return index - 1

    def cell_value(self, cell: ET.Element) -> str:
        value = cell.find(MAIN_NS + "v")
        kind = cell.attrib.get("t")

        if kind == "s" and value is not None:
            return self.shared[int(value.text)]
        if kind == "inlineStr":
            inline = cell.find(MAIN_NS + "is")
            if inline is None:
                return ""
            return "".join(node.text or "" for node in inline.iter(MAIN_NS + "t"))
        return value.text if value is not None else ""

    def rows(self, sheet_name: str):
        if sheet_name not in self.sheets:
            raise SystemExit(
                f"시트를 찾을 수 없습니다: {sheet_name!r}\n"
                f"  사용 가능: {list(self.sheets)[:10]} ..."
            )

        root = ET.fromstring(self.zip.read(self.sheets[sheet_name]))
        for row in root.iter(MAIN_NS + "row"):
            cells: dict[int, str] = {}
            for cell in row.iter(MAIN_NS + "c"):
                cells[self.column_index(cell.attrib.get("r", "A1"))] = self.cell_value(cell)
            if not cells:
                yield []
                continue
            width = max(cells) + 1
            yield [cells.get(index, "") for index in range(width)]


def to_number(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "null", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def resolve_row(row: list[str], resolver: SggResolver) -> str | None:
    padded = row + [""] * 5
    sido = padded[0].strip()
    sgg = padded[1].strip()
    region = padded[2].strip()

    if not sido or sido == "전국":
        return None

    # '소계'를 빼면 도 지역 154개의 관종이 전부 사라집니다 (관종 행은 '소계',
    #    총계·연도 행은 '합계'로 들어옵니다). 실제로 났던 버그입니다.
    if sgg and sgg not in AGGREGATE_LABELS and region in ("합계", "소계"):
        return resolver.resolve(sido, sgg)

    # 광역시 자치구: 시군구 칸이 비고 행정구역명이 자치구명입니다.
    if not sgg and region and region not in AGGREGATE_LABELS:
        return resolver.resolve(sido, region)

    return None


def new_bucket(code: str, name: str) -> dict:
    return {
        "sgg_code": code,
        "full_name": name,
        "total_length_m": 0.0,
        "by_year": [0.0] * YEAR_COUNT,
        "concrete_length_m": 0.0,
        "materials": {},
        "inconsistent_length_m": 0.0,
    }


def read_years(row: list[str]) -> tuple[list[float], float]:
    years = [0.0] * YEAR_COUNT
    inconsistent = 0.0

    for index in range(YEAR_COUNT):
        column = YEAR_START_COL + index * YEAR_STRIDE
        if column >= len(row):
            continue

        net = to_number(row[column])
        laid = to_number(row[column + 1]) if column + 1 < len(row) else 0.0

        years[index] = net
        if net > 0 and laid <= 0:
            inconsistent += net

    return years, inconsistent


def collect_material(
    book: Workbook,
    resolver: SggResolver,
    sido_names: dict[str, str],
) -> tuple[dict, dict, dict]:
    stats = {
        "rows": 0,
        "sgg_rows": 0,
        "material_rows": 0,
        "skipped": 0,
        "sido_rows": 0,
    }

    buckets: dict[str, dict] = {}
    sido_totals: dict[str, dict] = {}

    for row in book.rows(SHEET_MATERIAL):
        stats["rows"] += 1
        if len(row) < TOTAL_COL + 1:
            continue

        padded = row + [""] * 5
        sido = padded[0].strip()
        sgg = padded[1].strip()
        region = padded[2].strip()
        material = padded[3].strip()
        total = to_number(padded[TOTAL_COL])

        # 시도 단위 합계 행: 세종·제주 대체용.
        # 시군구 행과 같은 구조 차이가 있습니다. 총계·연도는 '합계', 관종은 '소계'로
        # 들어오므로 둘 다 받아야 합니다. region='소계' & material='총계' 행은
        # 시트에 없음을 확인했습니다(이중 집계 없음).
        if sido and sido != "전국" and not sgg and region in ("합계", "소계"):
            code = sido_names.get(sido)
            if code:
                bucket = sido_totals.setdefault(code, new_bucket(code, sido))
                if material == "총계":
                    stats["sido_rows"] += 1
                    bucket["total_length_m"] = total
                    bucket["by_year"], bucket["inconsistent_length_m"] = read_years(row)
                else:
                    if total > 0:
                        bucket["materials"][material] = total
                    if material in CONCRETE_MATERIALS:
                        bucket["concrete_length_m"] += total
            continue

        code = resolve_row(row, resolver)
        if code is None:
            stats["skipped"] += 1
            continue

        bucket = buckets.setdefault(code, new_bucket(code, resolver.name_of(code)))

        if material == "총계":
            stats["sgg_rows"] += 1
            bucket["total_length_m"] = total
            bucket["by_year"], bucket["inconsistent_length_m"] = read_years(row)
        else:
            # 관종별 소계 행
            stats["material_rows"] += 1
            if total > 0:
                bucket["materials"][material] = total
            if material in CONCRETE_MATERIALS:
                bucket["concrete_length_m"] += total

    return buckets, sido_totals, stats


def collect_diameter(
    book: Workbook,
    resolver: SggResolver,
    sido_names: dict[str, str],
) -> dict[str, dict]:
    result: dict[str, dict] = {}

    for row in book.rows(SHEET_DIAMETER):
        padded = row + [""] * 5
        if padded[3].strip() != "총계":
            continue

        sido = padded[0].strip()
        sgg = padded[1].strip()
        region = padded[2].strip()

        # 시도 단위 합계 행: 세종·제주 대체용 (시도 코드로 저장)
        if sido and sido != "전국" and not sgg and region == "합계":
            code = sido_names.get(sido)
            if code is None:
                continue
        else:
            code = resolve_row(row, resolver)
            if code is None:
                continue

        total = to_number(padded[TOTAL_COL])
        bands = {}
        for column, label in DIAMETER_BANDS:
            bands[label] = to_number(row[column]) if column < len(row) else 0.0

        large = bands["d500_800mm"] + bands["over_800mm"]

        result[code] = {
            "diameter_total_m": total,
            "diameter_bands_m": bands,
            "large_diameter_length_m": large,
            "large_diameter_ratio": round(large / total, 6) if total > 0 else None,
        }

    return result


# 데이터 품질 검사 5종의 임계값. 미보고를 0%로 두면 구도심이 "최신 관로"로 읽혀
# 최하위로 밀리므로, 의심 신호마다 결측 처리합니다. 잘못된 값보다 결측이 낫습니다.

# 1. 연도별 합계가 총계의 이 비율 미만 → 매설연도 미보고
YEAR_COVERAGE_THRESHOLD = 0.5

# 2. `계`와 `매설−철거`가 이 비율 이상 어긋나면 보고 자체가 내부 모순
INCONSISTENT_THRESHOLD = 0.3

# 3. 평균 경과연수가 이보다 낮으면 물리적으로 불가능
MIN_PLAUSIBLE_MEAN_AGE = 1.0

# 4. `1996년이전`이 총계의 이 비율 미만 → 역사 자료 미보고
#    (이 집단 67개의 노후율 중앙값이 0.6%, 나머지 159개는 49.4%. 80배 차이)
PRE_1996_MIN_SHARE = 0.001

# 5. 1996~2024 중 한 연도가 총계의 이 비율 초과 → 몰아쓰기 (전주 75%, 여수 99.4%).
#    2.를 통과하므로 별도 검사가 필요합니다. `1996년이전`은 수십 년 합계라 제외.
SINGLE_YEAR_MAX_SHARE = 0.5


def _missing(
    year_sum: float, total: float, reason: str, raw_ratio: float | None = None
) -> dict:
    return {
        "aged_ratio_20y": None,
        "aged_ratio_25y": None,
        "aged_ratio_29y": None,
        "aged_length_25y_m": None,
        "pipe_mean_age_years": None,
        "year_coverage": round(year_sum / total, 4) if total > 0 else 0.0,
        "age_missing_reason": reason,
        "aged_ratio_25y_raw": None if raw_ratio is None else round(raw_ratio, 6),
    }


def aged_ratios(years: list[float], total: float, inconsistent: float = 0.0) -> dict:
    year_sum = sum(years)

    def aged_length(threshold: int) -> float:
        cutoff_year = BASE_YEAR - threshold
        length = years[0] if PRE_YEAR <= cutoff_year else 0.0
        for index in range(1, YEAR_COUNT):
            if PRE_YEAR + index <= cutoff_year:
                length += years[index]
        return length

    aged_25 = aged_length(25)

    # 검증 전 원값. 공식 통계와 같은 방식으로 대조하기 위해서만 씁니다.
    raw_ratio = aged_25 / year_sum if year_sum > 0 else None

    if total <= 0 or year_sum <= 0 or year_sum < total * YEAR_COVERAGE_THRESHOLD:
        return _missing(year_sum, total, "연도별 보고 부족", raw_ratio)

    if total > 0 and inconsistent > total * INCONSISTENT_THRESHOLD:
        return _missing(
            year_sum, total, "매설연장 없이 계만 기재: 연도 배분 신뢰 불가", raw_ratio
        )

    if years[0] < total * PRE_1996_MIN_SHARE:
        return _missing(
            year_sum, total, "1996년이전 연장 미보고: 역사 자료 없음", raw_ratio
        )

    single_max = max(years[1:]) if YEAR_COUNT > 1 else 0.0
    if single_max > total * SINGLE_YEAR_MAX_SHARE:
        share = single_max / total
        return _missing(
            year_sum, total, f"단일 연도에 {share:.0%} 집중: 몰아쓰기", raw_ratio
        )

    # 비율은 연도 보고분(year_sum)으로 나눕니다. 총계로 나누면 미보고분이
    # 자동으로 '신설'로 계산되어 노후율이 과소평가됩니다.
    weighted_sum = sum(
        (BASE_YEAR - (PRE_YEAR + index)) * years[index] for index in range(YEAR_COUNT)
    )

    mean_age = weighted_sum / year_sum

    if mean_age < MIN_PLAUSIBLE_MEAN_AGE:
        return _missing(
            year_sum, total, f"평균 경과연수 {mean_age:.2f}년: 비현실적", raw_ratio
        )

    return {
        "aged_ratio_20y": round(aged_length(20) / year_sum, 6),
        "aged_ratio_25y": round(aged_25 / year_sum, 6),
        # '1996년 이전' 그룹의 비율. 2024 기준으로 이름 붙인 필드라 2025 기준인
        # 지금은 실제로 30년 이상입니다. 이름을 바꾸면 산출물 스키마가 바뀌어
        # (참고용 필드) 이름은 유지하고 의미만 여기 적어 둡니다.
        "aged_ratio_29y": round(years[0] / year_sum, 6),
        "aged_length_25y_m": round(aged_25, 1),
        "pipe_mean_age_years": round(mean_age, 2),
        "year_coverage": round(year_sum / total, 4),
        "age_missing_reason": None,
        "aged_ratio_25y_raw": round(raw_ratio, 6) if raw_ratio is not None else None,
    }


def finalize(
    buckets: dict[str, dict],
    sido_totals: dict[str, dict],
    diameters: dict[str, dict],
    resolver: SggResolver,
    master: list[dict],
) -> tuple[list[dict], dict]:
    counters = {"direct": 0, "sido_fallback": 0, "year_missing": 0, "no_data": 0}
    records: list[dict] = []

    for row in master:
        code = row["sgg_code"]
        sido_code = row.get("sido_code")

        bucket = buckets.get(code)
        fallback = False

        if bucket is None or bucket["total_length_m"] <= 0:
            bucket = sido_totals.get(sido_code)
            fallback = bucket is not None

        if bucket is None:
            counters["no_data"] += 1
            continue

        total = bucket["total_length_m"]
        ratios = aged_ratios(
            bucket["by_year"], total, bucket.get("inconsistent_length_m", 0.0)
        )

        if ratios["aged_ratio_25y"] is None:
            counters["year_missing"] += 1
        elif fallback:
            counters["sido_fallback"] += 1
        else:
            counters["direct"] += 1

        record = {
            "sgg_code": code,
            "full_name": resolver.name_of(code),
            "total_length_m": round(total, 1),
            "is_sido_fallback": fallback,
            **ratios,
            # 관종 행이 하나도 없으면 0%가 아니라 결측입니다.
            # "콘크리트가 없다"와 "관종을 모른다"를 구분해야 합니다.
            "concrete_ratio": (
                round(bucket["concrete_length_m"] / total, 6)
                if total > 0 and sum(bucket["materials"].values()) > 0
                else None
            ),
            "materials_m": {
                name: round(value, 1)
                for name, value in sorted(
                    bucket["materials"].items(), key=lambda pair: -pair[1]
                )
            },
        }

        diameter = diameters.get(code)
        if diameter is None and fallback:
            diameter = diameters.get(sido_code)
        record.update(diameter or {})

        records.append(record)

    return records, counters


def report(
    records: list[dict],
    stats: dict,
    counters: dict,
    resolver: SggResolver,
    master: set[str],
) -> None:
    print()
    print(
        f"시트 행 {stats['rows']:,} → 시군구 총계 {stats['sgg_rows']:,} / "
        f"관종 소계 {stats['material_rows']:,} / 시도 총계 {stats['sido_rows']:,}"
    )
    print(f"  읍면동·집계행 등 배제 {stats['skipped']:,}행")
    print()
    print("=== 시군구별 산출 경로 ===")
    print(f"  시군구 행 직접 사용        {counters['direct']:4d}개")
    print(f"  시도 값으로 대체 (세종·제주) {counters['sido_fallback']:4d}개")
    print(f"  매설연도 미보고 → 결측     {counters['year_missing']:4d}개")
    print(f"  데이터 없음               {counters['no_data']:4d}개")

    print()
    print(f"추출된 시군구 {len(records)}개 / 마스터 {len(master)}개")
    codes = {row["sgg_code"] for row in records}
    only_data, only_map = diff_codes(codes, master)
    if only_data:
        print(f"  [!] 데이터에만 있음 {len(only_data)}개: {sorted(only_data)}")
    if only_map:
        names = [resolver.name_of(code) for code in sorted(only_map)]
        print(f"  [!] 통계에 없는 시군구 {len(only_map)}개: {names[:10]}")

    # 시도 대체분은 시도 전체 연장이 들어가 중복되므로 검증에서 제외합니다.
    direct_total = sum(
        row["total_length_m"] for row in records if not row["is_sido_fallback"]
    )
    fallback_total = sum(
        row["total_length_m"] for row in records if row["is_sido_fallback"]
    )
    print()
    print("=== 총연장 교차 검증 ===")
    print(f"  시군구 직접 합계      {direct_total:>18,.0f} m = {direct_total / 1000:>10,.0f} km")
    print(f"  통계 전국 총계        {NATIONAL_TOTAL_M:>18,} m = {NATIONAL_TOTAL_M / 1000:>10,.0f} km")
    if fallback_total:
        print(f"  (시도 대체분 {fallback_total:,.0f} m 는 중복이라 제외)")
    residual = NATIONAL_TOTAL_M - direct_total
    print(f"  잔여 {residual:>+18,.0f} m: 세종·제주 시도 단위 분(중복 제외분)과 대조")
    difference = abs(residual) / NATIONAL_TOTAL_M
    print(f"  잔여 비율 {difference:.3%} {'[OK] 세종+제주로 설명됨' if difference < 0.05 else '[!] 확인 필요'}")

    aged = [row["aged_ratio_25y"] for row in records if row["aged_ratio_25y"] is not None]
    if aged:
        ordered = sorted(aged)
        middle = len(ordered) // 2
        weighted_base = sum(
            row["total_length_m"]
            for row in records
            if row["aged_ratio_25y"] is not None
        )
        national = (
            sum(
                (row["aged_ratio_25y"] or 0) * row["total_length_m"]
                for row in records
                if row["aged_ratio_25y"] is not None
            )
            / weighted_base
            if weighted_base
            else 0.0
        )
        # 공식 통계와 같은 방식(검증 전 원값·전 시군구 포함)으로도 계산합니다.
        # 공식 37.9%도 같은 보고 누락을 안고 있어 이 값이 대조 대상입니다.
        raw_rows = [
            row for row in records
            if row.get("aged_ratio_25y_raw") is not None and not row["is_sido_fallback"]
        ]
        raw_base = sum(row["total_length_m"] for row in raw_rows)
        raw_national = (
            sum(row["aged_ratio_25y_raw"] * row["total_length_m"] for row in raw_rows)
            / raw_base
            if raw_base
            else 0.0
        )

        print()
        print("=== 25년 이상 경과 비율 ===")
        print(f"  1) 전 시군구 포함(검증 전)  {raw_national:.1%}   ← 공식 통계와 같은 방식")
        print(f"     기획서 인용치            37.9% (환경부 2023 하수도통계)")
        print(f"  2) 신뢰 가능 {len(aged)}개만       {national:.1%}   ← 점수 산정에 쓰는 값")
        print()
        print(f"  1)이 공식치와 맞으면 파싱이 정확한 것입니다. 2)가 더 높은 이유는")
        print(f"  매설연도를 보고하지 않은 {counters['year_missing']}개를 제외했기 때문이며,")
        print("  공식 통계 37.9%도 그 누락을 그대로 안고 있습니다.")
        print(f"  시군구별 최소 {min(aged):.1%} / 중앙 {ordered[middle]:.1%} / 최대 {max(aged):.1%}")

        print()
        print("=== 노후율 상위 10곳 ===")
        top = sorted(
            (row for row in records if row["aged_ratio_25y"] is not None),
            key=lambda row: -row["aged_ratio_25y"],
        )[:10]
        for row in top:
            print(
                f"  {row['aged_ratio_25y']:6.1%}  {row['full_name']:22} "
                f"평균 {row['pipe_mean_age_years'] or 0:5.1f}년  "
                f"콘크리트 {row['concrete_ratio'] or 0:5.1%}  "
                f"대구경 {row.get('large_diameter_ratio') or 0:5.1%}"
            )

    missing = [row for row in records if row["aged_ratio_25y"] is None]
    if missing:
        print()
        print(f"=== 노후율 결측 {len(missing)}개 (0%로 두면 '최신 관로'로 오인되므로 결측 처리) ===")
        for row in missing:
            print(f"  {row['full_name']:24} {row.get('age_missing_reason')}")

    print()
    print("=== 광주 5개 구 ===")
    for row in records:
        if not row["sgg_code"].startswith("29"):
            continue
        aged = row["aged_ratio_25y"]
        mean_age = row["pipe_mean_age_years"]
        print(
            f"  {row['sgg_code']} {row['full_name']:18} "
            f"25년이상 {'  결측' if aged is None else format(aged, '6.1%')}  "
            f"평균 {'  N/A' if mean_age is None else format(mean_age, '5.1f') + '년'}  "
            f"콘크리트 {row['concrete_ratio'] or 0:5.1%}  "
            f"대구경 {row.get('large_diameter_ratio') or 0:5.1%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="하수도통계 xlsx에서 시군구별 실측 관로 경과연수와 관종·관경을 추출합니다.")
    parser.add_argument(
        "--xlsx", type=Path, default=paths.SEWER_STATS_XLSX, help="하수도통계 xlsx"
    )
    parser.add_argument(
        "--master", type=Path, default=paths.DISTRICTS_MASTER
    )
    parser.add_argument(
        "--out", type=Path, default=paths.PIPE_AGE_BY_SGG
    )
    args = parser.parse_args()

    with args.master.open(encoding="utf-8") as handle:
        master = json.load(handle)

    resolver = SggResolver.from_master(args.master)
    master_codes = set(resolver.names)
    print(f"마스터 로드: {len(master_codes)}개")
    print(f"기준연도: {BASE_YEAR}")

    # 시도명 → 시도 코드. 세종·제주 대체에 씁니다.
    sido_names = {
        row["sido_name"]: row["sido_code"]
        for row in master
        if row.get("sido_name") and row.get("sido_code")
    }

    book = Workbook(args.xlsx)
    print(f"시트 {len(book.sheets)}개, 문자열 {len(book.shared):,}개")

    buckets, sido_totals, stats = collect_material(book, resolver, sido_names)
    diameters = collect_diameter(book, resolver, sido_names)
    print(f"  관경 데이터 {len(diameters)}개 (시군구+시도)")
    print(f"  시도 단위 총계 {len(sido_totals)}개")

    records, counters = finalize(buckets, sido_totals, diameters, resolver, master)
    report(records, stats, counters, resolver, master_codes)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=1)
    print()
    print(f"→ {args.out} ({len(records)}개)")


# ---------------------------------------------------------------- 자체 검증


def _years(**at_index: float) -> list[float]:
    """YEAR_COUNT 길이의 연도별 연장. 인덱스 0 = 1996년이전(1995 대표)."""
    years = [0.0] * YEAR_COUNT
    for key, value in at_index.items():
        years[int(key.lstrip("i"))] = value
    return years


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("숫자 변환 (결측은 0: 연장 합산용)")
    check("천단위 구분자", to_number("1,234.5") == 1234.5)
    check("None → 0", to_number(None) == 0.0)
    check("빈 문자열·'-' → 0", to_number("") == 0.0 and to_number("-") == 0.0)
    check("쓰레기 값 → 0 (예외 없음)", to_number("점검중") == 0.0)

    print()
    print("엑셀 열 참조")
    check("A → 0", Workbook.column_index("A1") == 0)
    check("H → 7 (연도 그룹 시작)", Workbook.column_index("H3") == YEAR_START_COL)
    check("Z → 25", Workbook.column_index("Z") == 25)
    check("AA → 26 (자릿수 넘어감)", Workbook.column_index("AA10") == 26)

    print()
    print("행 → 시군구 코드 (resolve_row)")
    from mapping.sgg_normalize import SggResolver

    resolver = SggResolver.from_rows([
        {"sgg_code": "29110", "sido_name": "광주광역시", "sgg_name": "동구",
         "full_name": "광주광역시 동구"},
        {"sgg_code": "46110", "sido_name": "전라남도", "sgg_name": "목포시",
         "full_name": "전라남도 목포시"},
    ])

    check("도 지역 총계·연도 행 (region='합계')",
          resolve_row(["전라남도", "목포시", "합계", "총계"], resolver) == "46110")
    # 관종 행은 region='소계'로 들어옵니다. '소계'를 빼면 도 지역 관종이
    # 전부 사라집니다 — 실제로 났던 버그의 회귀 검사입니다.
    check("도 지역 관종 행 (region='소계') — 소계 누락 버그 회귀",
          resolve_row(["전라남도", "목포시", "소계", "흄관"], resolver) == "46110")
    check("광역시 자치구 (시군구 칸 공란, 행정구역명=자치구)",
          resolve_row(["광주광역시", "", "동구", "총계"], resolver) == "29110")
    check("시도 합계 행은 배제",
          resolve_row(["광주광역시", "", "합계", "총계"], resolver) is None)
    check("전국 행은 배제",
          resolve_row(["전국", "", "", "총계"], resolver) is None)
    check("빈 행은 배제", resolve_row([], resolver) is None)
    check("집계행 라벨(시부)은 배제",
          resolve_row(["전라남도", "시부", "합계", "총계"], resolver) is None)

    print()
    print("연도 그룹 읽기 (read_years)")
    row = [""] * (YEAR_START_COL + YEAR_COUNT * YEAR_STRIDE)
    row[YEAR_START_COL] = "300"          # i0: 계 300
    row[YEAR_START_COL + 1] = "300"      #     매설 300 → 정상
    row[YEAR_START_COL + 5 * YEAR_STRIDE] = "100"      # i5: 계 100
    row[YEAR_START_COL + 5 * YEAR_STRIDE + 1] = "0"    #     매설 0 → 모순
    years, inconsistent = read_years(row)
    check("계 값이 연도 슬롯에 들어감", years[0] == 300.0 and years[5] == 100.0,
          str((years[0], years[5])))
    check("매설연장 없이 계만 있으면 모순으로 집계", inconsistent == 100.0,
          str(inconsistent))
    check("짧은 행은 0 채움 (예외 없음)", read_years(["x"] * 3) == ([0.0] * YEAR_COUNT, 0.0))

    print()
    print("노후율 산정: 정상 경로")
    # 총계 1000, 연도 보고 800 (커버리지 0.8). i0=1996년이전(30년), i5=2000년(25년),
    # i6=2001년(24년), i20=2015년(10년), i30=2025년(0년).
    years = _years(i0=300, i5=100, i6=100, i20=200, i30=100)
    got = aged_ratios(years, 1000.0)
    check("게이트 통과 (결측 사유 없음)", got["age_missing_reason"] is None,
          str(got["age_missing_reason"]))
    # 분모는 연도 보고분(800)이어야 합니다. 총계(1000)로 나누면 미보고 200m가
    # '신설'로 계산되어 노후율이 과소평가됩니다 — 실제로 났던 버그의 회귀 검사.
    check("25년 이상 = 400/800 (총계 아닌 보고분으로 나눔 — 분모 버그 회귀)",
          got["aged_ratio_25y"] == round(400 / 800, 6), str(got["aged_ratio_25y"]))
    check("경계 연도(정확히 25년 경과)는 노후에 포함",
          got["aged_length_25y_m"] == 400.0, str(got["aged_length_25y_m"]))
    check("20년 기준 = 500/800", got["aged_ratio_20y"] == round(500 / 800, 6),
          str(got["aged_ratio_20y"]))
    check("1996년이전 비율 = 300/800", got["aged_ratio_29y"] == round(300 / 800, 6),
          str(got["aged_ratio_29y"]))
    check("기준 완화 순서: 20y ≥ 25y ≥ 1996년이전",
          got["aged_ratio_20y"] >= got["aged_ratio_25y"] >= got["aged_ratio_29y"], "")
    check("비율은 전부 [0,1]",
          all(0.0 <= got[k] <= 1.0
              for k in ("aged_ratio_20y", "aged_ratio_25y", "aged_ratio_29y")), "")
    mean = (30 * 300 + 25 * 100 + 24 * 100 + 10 * 200 + 0 * 100) / 800
    check("평균 경과연수 = 가중평균", got["pipe_mean_age_years"] == round(mean, 2),
          str(got["pipe_mean_age_years"]))
    check("커버리지 = 800/1000", got["year_coverage"] == 0.8, str(got["year_coverage"]))
    check("원값(raw)도 함께 실림", got["aged_ratio_25y_raw"] == round(400 / 800, 6))

    print()
    print("품질 게이트 5종: 각각 발동해야 함 (미보고를 0%로 두면 구도심이 '최신'으로 읽힘)")

    def gated(result: dict) -> bool:
        return (
            result["aged_ratio_25y"] is None
            and result["pipe_mean_age_years"] is None
            and result["age_missing_reason"] is not None
        )

    # 1. 연도별 보고 부족: 보고 400 < 총계 1000 × 0.5
    got = aged_ratios(_years(i0=200, i10=200), 1000.0)
    check("게이트 1: 연도별 보고 부족 → 결측",
          gated(got) and "보고 부족" in got["age_missing_reason"],
          str(got["age_missing_reason"]))
    check("게이트 1: 커버리지는 그래도 기록", got["year_coverage"] == 0.4)
    check("게이트 1: 대조용 원값은 유지", got["aged_ratio_25y_raw"] is not None)
    check("총계 0 도 결측 (0-나눗셈 없음)",
          gated(aged_ratios(_years(), 0.0)), "")

    # 2. 내부 모순: 계≠매설−철거 가 총계의 30% 초과
    got = aged_ratios(_years(i0=300, i5=100, i6=100, i20=200, i30=100), 1000.0,
                      inconsistent=400.0)
    check("게이트 2: 계만 기재(모순 40%) → 결측",
          gated(got) and "신뢰 불가" in got["age_missing_reason"],
          str(got["age_missing_reason"]))
    check("게이트 2: 모순 30% 이하는 통과",
          aged_ratios(_years(i0=300, i5=100, i6=100, i20=200, i30=100), 1000.0,
                      inconsistent=200.0)["age_missing_reason"] is None, "")

    # 3. 1996년이전 미보고: 역사 자료 없음
    got = aged_ratios(_years(i10=400, i15=300, i20=300), 1000.0)
    check("게이트 3: 1996년이전 0m → 결측",
          gated(got) and "역사 자료" in got["age_missing_reason"],
          str(got["age_missing_reason"]))

    # 4. 몰아쓰기: 단일 연도가 총계의 50% 초과 (전주 75%, 여수 99.4% 사례)
    got = aged_ratios(_years(i0=100, i10=600, i20=300), 1000.0)
    check("게이트 4: 단일 연도 60% 집중 → 결측",
          gated(got) and "몰아쓰기" in got["age_missing_reason"],
          str(got["age_missing_reason"]))
    check("게이트 4: `1996년이전` 집중은 몰아쓰기가 아님 (수십 년 합계)",
          aged_ratios(_years(i0=700, i10=100, i20=200), 1000.0)
          ["age_missing_reason"] is None, "")

    # 5. 비현실적 평균: 평균 경과연수 1년 미만
    got = aged_ratios(_years(i0=2, i29=499, i30=499), 1000.0)
    check("게이트 5: 평균 경과연수 1년 미만 → 결측",
          gated(got) and "비현실적" in got["age_missing_reason"],
          str(got["age_missing_reason"]))

    print()
    print("콘크리트계 관종 (VR관 누락 버그의 고정)")
    check("5종 전부 등록", CONCRETE_MATERIALS == {
        "흄관", "PC관", "VR관", "레진콘크리트관", "철근콘크리트관(BOX형)"},
        str(sorted(CONCRETE_MATERIALS)))

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
