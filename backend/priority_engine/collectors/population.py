"""행정동 주민등록 인구·세대를 읍면동과 시군구로 집계합니다.

    python3 -m collectors.population          # 자체 검증
    python3 -m collectors.population --build  # 실제 수집

원본은 행정안전부 주민등록 통계 두 파일입니다(활용신청 불필요, CP949).
읍면동 코드가 행안부 10자리라 `admin_dongs_master.json` 의 `adm_code` 와
가공 없이 붙습니다. 시군구 집계는 마스터의 `sgg_code`(기초자치단체 229 단위)를
따라가므로 일반구가 자동으로 통합됩니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import csv
import json

import paths

ENCODING = "cp949"

CODE_COLUMN = "행정기관코드"
BASE_MONTH_COLUMN = "기준연월"
POPULATION_COLUMN = "계"
HOUSEHOLD_COLUMN = "전체세대수"
ONE_PERSON_COLUMN = "1인세대"


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding=ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def to_int(value: str | None) -> int | None:
    """천단위 구분자와 공백을 걷어내고 정수로 만듭니다."""
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def index_by_code(rows: list[dict], columns: dict[str, str]) -> dict[str, dict]:
    """행정기관코드 → {필드: 값} 으로 접습니다.

    같은 코드가 두 번 나오면 뒤엣것이 이깁니다. 실제 파일에는 중복이 없지만
    (3,618행 전부 유일) 조용히 합산해서 인구를 두 배로 만드는 사고를 막습니다.
    """
    indexed: dict[str, dict] = {}
    for row in rows:
        code = (row.get(CODE_COLUMN) or "").strip()
        if not code:
            continue
        indexed[code] = {
            name: to_int(row.get(source)) for name, source in columns.items()
        }
    return indexed


SIDO_COLUMN = "시도명"
SIGUNGU_COLUMN = "시군구명"


def municipality_index(district_master: list[dict]) -> dict[tuple[str, str], str]:
    """(시도, 시군구) → 기초자치단체 코드. 이름으로 찾기 위한 색인입니다."""
    index: dict[tuple[str, str], str] = {}
    for row in district_master:
        parts = (row.get("full_name") or "").split()
        if len(parts) >= 2:
            index[(parts[0], parts[-1])] = row["sgg_code"]
    return index


def resolve_municipality(
    row: dict, index: dict[tuple[str, str], str]
) -> str | None:
    """행정동 한 줄을 기초자치단체 코드로 옮깁니다.

    `시군구명` 에는 일반구가 붙어 옵니다('화성시 효행구'). 점수 단위는
    기초자치단체 229 라 **첫 토큰만** 씁니다. 세종은 `시군구명` 이 비어 있어
    시도명을 그대로 씁니다(마스터에서도 '세종특별자치시 세종시').
    """
    sido = (row.get(SIDO_COLUMN) or "").strip()
    sigungu = (row.get(SIGUNGU_COLUMN) or "").strip()
    if not sido:
        return None
    if not sigungu:
        # 세종처럼 시군구가 없는 단층 자치단체.
        for (index_sido, index_sgg), code in index.items():
            if index_sido == sido:
                return code
        return None
    return index.get((sido, sigungu.split()[0]))


def build_districts(
    population_rows: list[dict],
    household_rows: list[dict],
    district_master: list[dict],
) -> tuple[list[dict], dict]:
    """시군구 집계는 **코드가 아니라 지역명**으로 붙입니다.

    읍면동 코드는 행정구역 개편마다 바뀝니다. 화성시가 특례시로 전환되며 29개
    동 코드가 전부 바뀌었는데, 코드로만 붙이면 화성시 인구가 조용히 0이 됩니다.
    지역명은 개편에도 살아남으므로 시군구 단위는 이름으로 집계합니다.
    """
    index = municipality_index(district_master)
    households_by_code = index_by_code(
        household_rows,
        {"households": HOUSEHOLD_COLUMN, "one_person_households": ONE_PERSON_COLUMN},
    )

    buckets: dict[str, dict] = {}
    unresolved: set[tuple[str, str]] = set()

    for row in population_rows:
        code = resolve_municipality(row, index)
        if code is None:
            unresolved.add(
                ((row.get(SIDO_COLUMN) or "").strip(), (row.get(SIGUNGU_COLUMN) or "").strip())
            )
            continue
        bucket = buckets.setdefault(
            code,
            {
                "sgg_code": code,
                "population": 0,
                "households": 0,
                "one_person_households": 0,
                "dong_count": 0,
            },
        )
        bucket["dong_count"] += 1
        bucket["population"] += to_int(row.get(POPULATION_COLUMN)) or 0
        house = households_by_code.get((row.get(CODE_COLUMN) or "").strip(), {})
        bucket["households"] += house.get("households") or 0
        bucket["one_person_households"] += house.get("one_person_households") or 0

    districts = []
    for bucket in buckets.values():
        total_house = bucket["households"]
        districts.append(
            {
                **bucket,
                "one_person_share": (
                    round(bucket["one_person_households"] / total_house, 4)
                    if total_house
                    else None
                ),
                "persons_per_household": (
                    round(bucket["population"] / total_house, 2) if total_house else None
                ),
            }
        )
    districts.sort(key=lambda row: row["sgg_code"])

    stats = {
        "districts": len(districts),
        "master_districts": len(district_master),
        "population_total": sum(row["population"] for row in districts),
        "unresolved": sorted(unresolved),
    }
    return districts, stats


def build(
    population_rows: list[dict],
    household_rows: list[dict],
    dong_master: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    """읍면동·시군구 두 단위로 집계합니다.

    반환: (읍면동 목록, 시군구 목록, 통계)
    """
    population = index_by_code(population_rows, {"population": POPULATION_COLUMN})
    households = index_by_code(
        household_rows,
        {"households": HOUSEHOLD_COLUMN, "one_person_households": ONE_PERSON_COLUMN},
    )

    dongs: list[dict] = []
    by_sgg: dict[str, dict] = {}
    matched = 0

    for entry in dong_master:
        code = str(entry.get("adm_code") or "")
        sgg_code = entry.get("sgg_code")
        pop = population.get(code, {}).get("population")
        house = households.get(code, {})
        if pop is not None:
            matched += 1

        dongs.append(
            {
                "adm_code": code,
                "sgg_code": sgg_code,
                "adm_name": entry.get("adm_name"),
                "population": pop,
                "households": house.get("households"),
                "one_person_households": house.get("one_person_households"),
            }
        )

        if not sgg_code:
            continue
        bucket = by_sgg.setdefault(
            sgg_code,
            {
                "sgg_code": sgg_code,
                "population": 0,
                "households": 0,
                "one_person_households": 0,
                "dong_count": 0,
                "dong_missing": 0,
            },
        )
        bucket["dong_count"] += 1
        if pop is None:
            bucket["dong_missing"] += 1
            continue
        bucket["population"] += pop
        bucket["households"] += house.get("households") or 0
        bucket["one_person_households"] += house.get("one_person_households") or 0

    districts = []
    for bucket in by_sgg.values():
        # 동이 하나라도 빠진 시군구는 인구 합계가 과소집계입니다. 값을 버리지는
        # 않지만 몇 개가 빠졌는지 남겨서 쓰는 쪽이 판단하게 합니다.
        one_person = bucket["one_person_households"]
        total_house = bucket["households"]
        districts.append(
            {
                **bucket,
                "one_person_share": (
                    round(one_person / total_house, 4) if total_house else None
                ),
                "persons_per_household": (
                    round(bucket["population"] / total_house, 2) if total_house else None
                ),
            }
        )
    districts.sort(key=lambda row: row["sgg_code"])

    stats = {
        "dong_master": len(dong_master),
        "dong_matched": matched,
        "file_rows": len(population_rows),
        "districts": len(districts),
        "population_total": sum(row["population"] for row in districts),
    }
    return dongs, districts, stats


def base_month(rows: list[dict]) -> str | None:
    for row in rows:
        value = (row.get(BASE_MONTH_COLUMN) or "").strip()
        if value:
            return value
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="행정동 인구·세대를 읍면동·시군구로 집계합니다."
    )
    parser.add_argument("--build", action="store_true", help="실제 수집 실행")
    parser.add_argument("--population", type=Path, default=paths.POPULATION_CSV)
    parser.add_argument("--households", type=Path, default=paths.HOUSEHOLDS_CSV)
    parser.add_argument("--master", type=Path, default=paths.ADMIN_DONGS_MASTER)
    parser.add_argument(
        "--districts", type=Path, default=paths.DISTRICTS_MASTER
    )
    parser.add_argument("--data", type=Path, default=paths.DATA)
    args = parser.parse_args()

    # 트리거가 "인자 하나라도 있으면"이라 --population 만 줘도 수집이 돌았습니다.
    # 도움말대로 --build 를 명시해야 실행합니다.
    if not args.build:
        parser.error("실제 수집은 --build 를 명시해야 합니다 (인자 없이 실행 = 자체 검증)")

    for path in (args.population, args.households):
        if not path.exists():
            raise SystemExit(
                f"원본이 없습니다: {path}\n"
                "  raw/MANIFEST.md 의 주민등록 인구·세대 항목을 참고하세요."
            )

    population_rows = read_rows(args.population)
    household_rows = read_rows(args.households)
    dong_master = json.loads(args.master.read_text(encoding="utf-8"))

    print(
        f"인구 {len(population_rows)}행 · 세대 {len(household_rows)}행 · "
        f"기준 {base_month(population_rows)}"
    )

    district_master = json.loads(args.districts.read_text(encoding="utf-8"))

    dongs, _, stats = build(population_rows, household_rows, dong_master)
    rate = stats["dong_matched"] / stats["dong_master"] if stats["dong_master"] else 0
    print(f"읍면동 조인 {stats['dong_matched']}/{stats['dong_master']} ({rate:.1%})")
    if rate < 1.0:
        missing = [row for row in dongs if row["population"] is None]
        names = ", ".join(f"{row['adm_code']} {row['adm_name']}" for row in missing[:5])
        print(f"  [!] 인구가 없는 동 {len(missing)}개: {names} ...")
        print("      마스터가 최근 행정구역 개편을 아직 반영하지 않은 경우입니다.")
        print("      시군구 집계는 지역명으로 붙이므로 이 누락에 영향받지 않습니다.")

    districts, district_stats = build_districts(
        population_rows, household_rows, district_master
    )
    print(
        f"시군구 {district_stats['districts']}/{district_stats['master_districts']}개 · "
        f"인구 합계 {district_stats['population_total']:,}명"
    )
    if district_stats["unresolved"]:
        print(f"  [!] 지역명을 못 붙인 조합: {district_stats['unresolved'][:5]}")

    args.data.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        (paths.POPULATION_BY_SGG_FILE, districts),
        (paths.POPULATION_BY_DONG_FILE, dongs),
    ):
        target = args.data / filename
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"→ {target} ({len(payload)}개)")


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'OK  ' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures += 1

    print("정수 변환")
    check("천단위 구분자 제거", to_int("1,234") == 1234)
    check("공백 허용", to_int(" 42 ") == 42)
    check("빈 값은 None", to_int("") is None)
    check("하이픈은 None", to_int("-") is None)
    check("None 은 None", to_int(None) is None)
    check("숫자 아닌 값은 None", to_int("없음") is None)

    print("\n코드 색인")
    rows = [
        {CODE_COLUMN: "1111051500", POPULATION_COLUMN: "10,682"},
        {CODE_COLUMN: " 1111053000 ", POPULATION_COLUMN: "8874"},
        {CODE_COLUMN: "", POPULATION_COLUMN: "999"},
    ]
    indexed = index_by_code(rows, {"population": POPULATION_COLUMN})
    check("2건만 색인 (빈 코드 제외)", len(indexed) == 2, str(len(indexed)))
    check("코드 공백 제거", "1111053000" in indexed)
    check("값 파싱", indexed["1111051500"]["population"] == 10682)

    duplicated = [
        {CODE_COLUMN: "1111051500", POPULATION_COLUMN: "100"},
        {CODE_COLUMN: "1111051500", POPULATION_COLUMN: "200"},
    ]
    check(
        "중복 코드는 합산하지 않음 (뒤엣것 채택)",
        index_by_code(duplicated, {"population": POPULATION_COLUMN})["1111051500"][
            "population"
        ]
        == 200,
    )

    print("\n집계")
    master = [
        {"adm_code": "2911052500", "sgg_code": "29110", "adm_name": "충장동"},
        {"adm_code": "2911053000", "sgg_code": "29110", "adm_name": "동명동"},
        {"adm_code": "4111100000", "sgg_code": "41110", "adm_name": "없는동"},
    ]
    pop_rows = [
        {CODE_COLUMN: "2911052500", POPULATION_COLUMN: "5929", BASE_MONTH_COLUMN: "2026-06-30"},
        {CODE_COLUMN: "2911053000", POPULATION_COLUMN: "8000", BASE_MONTH_COLUMN: "2026-06-30"},
    ]
    house_rows = [
        {CODE_COLUMN: "2911052500", HOUSEHOLD_COLUMN: "3000", ONE_PERSON_COLUMN: "1500"},
        {CODE_COLUMN: "2911053000", HOUSEHOLD_COLUMN: "5000", ONE_PERSON_COLUMN: "1500"},
    ]
    dongs, districts, stats = build(pop_rows, house_rows, master)

    check("읍면동은 마스터 기준 3개", len(dongs) == 3, str(len(dongs)))
    check("조인된 동 2개", stats["dong_matched"] == 2, str(stats["dong_matched"]))
    check("못 붙은 동은 None (0 아님)", dongs[2]["population"] is None, str(dongs[2]))

    by_code = {row["sgg_code"]: row for row in districts}
    check("시군구 2개", len(districts) == 2, str(len(districts)))
    check("29110 인구 합산", by_code["29110"]["population"] == 13929, str(by_code["29110"]))
    check("29110 세대 합산", by_code["29110"]["households"] == 8000)
    check("1인세대 비율 = 3000/8000", by_code["29110"]["one_person_share"] == 0.375)
    check("세대당 인구 = 13929/8000", by_code["29110"]["persons_per_household"] == 1.74)
    check("빠진 동 수 기록", by_code["41110"]["dong_missing"] == 1)
    check(
        "전부 결측이면 비율은 None",
        by_code["41110"]["one_person_share"] is None,
        str(by_code["41110"]),
    )
    check("기준연월 추출", base_month(pop_rows) == "2026-06-30")

    print("\n시군구 집계는 지역명 기준 (코드 개편에 안 깨짐)")
    district_master = [
        {"sgg_code": "41590", "full_name": "경기도 화성시"},
        {"sgg_code": "36110", "full_name": "세종특별자치시 세종시"},
        {"sgg_code": "29110", "full_name": "광주광역시 동구"},
    ]
    renamed = [
        # 코드는 개편으로 바뀌었지만 이름은 살아 있는 상황.
        {CODE_COLUMN: "4159325000", SIDO_COLUMN: "경기도",
         SIGUNGU_COLUMN: "화성시 효행구", POPULATION_COLUMN: "30000"},
        {CODE_COLUMN: "4159425000", SIDO_COLUMN: "경기도",
         SIGUNGU_COLUMN: "화성시 동탄구", POPULATION_COLUMN: "20000"},
        {CODE_COLUMN: "3611051000", SIDO_COLUMN: "세종특별자치시",
         SIGUNGU_COLUMN: "", POPULATION_COLUMN: "5000"},
        {CODE_COLUMN: "9999999999", SIDO_COLUMN: "없는도",
         SIGUNGU_COLUMN: "없는시", POPULATION_COLUMN: "111"},
    ]
    houses = [
        {CODE_COLUMN: "4159325000", HOUSEHOLD_COLUMN: "10000", ONE_PERSON_COLUMN: "4000"},
    ]
    got, got_stats = build_districts(renamed, houses, district_master)
    by_code = {row["sgg_code"]: row for row in got}
    check("일반구가 화성시로 합쳐짐", by_code["41590"]["population"] == 50000, str(by_code.get("41590")))
    check("동 2개로 셈", by_code["41590"]["dong_count"] == 2)
    check("세종은 시군구명이 비어도 붙음", by_code["36110"]["population"] == 5000)
    check("없는 지역은 미해결로 기록", got_stats["unresolved"] == [("없는도", "없는시")], str(got_stats["unresolved"]))
    check("이력 없는 시군구는 아예 없음", "29110" not in by_code)
    check("세대 없으면 비율 None", by_code["36110"]["one_person_share"] is None)

    print("\n빈 입력")
    dongs, districts, stats = build([], [], [])
    check("빈 결과", dongs == [] and districts == [])
    check("인구 합계 0", stats["population_total"] == 0)
    empty_districts, empty_stats = build_districts([], [], [])
    check("시군구도 빈 결과", empty_districts == [])
    check("시군구 인구 합계 0", empty_stats["population_total"] == 0)

    print()
    print(f"결과: {'실패 ' + str(failures) + '건' if failures else '전부 통과'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        raise SystemExit(_self_test())
