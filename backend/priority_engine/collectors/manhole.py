"""하수도통계 맨홀현황에서 집중강우 중점관리구역 비율을 뽑습니다.

    python3 -m collectors.manhole          # 자체 검증
    python3 -m collectors.manhole --build  # 실제 수집

2025 하수도통계에 신설된 `2-5. 맨홀현황` 시트에서 나옵니다. 시트가 시군구 ×
구역구분(집중강우중점관리구역 내 / 그외 / 총계) 롱포맷이라 구분별로 접습니다.

**왜 이 지표인가**: 지하안전정보 API의 중점관리대상은 전국 5건뿐이고 시설물명만
줘서 시군구에 붙일 수 없습니다(배율 ×1.2 가 사실상 죽은 코드였음). 이 시트는
지자체가 직접 지정한 집중강우 중점관리구역을 시군구 단위로 담고 있어 근거가
훨씬 낫습니다. 자세한 판단은 가중치 사전 등록 문서 2차 개정 참조.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json

import paths
from collectors.pipe_age import Workbook

SHEET = "2-5. 맨홀현황"

# 시트 앞 3줄은 제목·병합 헤더입니다. 4번째 줄부터 데이터입니다.
HEADER_ROWS = 3

# C열 구역구분 값. 원문 표기 그대로 씁니다.
ZONE_INSIDE = "집중강우중점관리구역 내"
ZONE_OUTSIDE = "그외"
ZONE_TOTAL = "총계"

COLUMN_SIDO = 0
COLUMN_SIGUNGU = 1
COLUMN_ZONE = 2
COLUMN_COUNT = 3

# 시군구 자리에 오는 소계 라벨. 지역이 아니라 도 안의 '시 합계 / 군 합계' 라
# 그대로 두면 지역명 미해결로 잡히고, 잘못 붙으면 이중 계상됩니다.
SUBTOTAL_LABELS = {"시부", "군부", "소계", "계", "합계"}


def to_int(value) -> int | None:
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def parse_rows(rows: list[list]) -> dict[tuple[str, str], dict[str, int]]:
    """(시도, 시군구) → {구역구분: 맨홀 개수} 로 접습니다.

    시군구가 빈 행은 시도 소계·전국 총계라 건너뜁니다 — 안 건너뛰면 전국 합계가
    한 지역처럼 섞여 들어갑니다.
    """
    folded: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows[HEADER_ROWS:]:
        if len(row) <= COLUMN_COUNT:
            continue
        sido = str(row[COLUMN_SIDO] or "").strip()
        sigungu = str(row[COLUMN_SIGUNGU] or "").strip()
        if not sido or not sigungu or sigungu in SUBTOTAL_LABELS:
            continue
        zone = str(row[COLUMN_ZONE] or "").strip()
        count = to_int(row[COLUMN_COUNT])
        if count is None:
            continue
        folded.setdefault((sido, sigungu), {})[zone] = count
    return folded


def parse_sido_rows(rows: list[list]) -> dict[str, dict[str, int]]:
    """시군구가 빈 행(시도 소계)만 따로 모읍니다.

    세종처럼 **시군구 행이 아예 없는 단층 자치단체**를 채우는 데만 씁니다.
    자치구가 따로 있는 시도(서울 등)에서는 쓰면 이중 계상이 됩니다.
    """
    folded: dict[str, dict[str, int]] = {}
    for row in rows[HEADER_ROWS:]:
        if len(row) <= COLUMN_COUNT:
            continue
        sido = str(row[COLUMN_SIDO] or "").strip()
        sigungu = str(row[COLUMN_SIGUNGU] or "").strip()
        if not sido or sido == "전국" or sigungu:
            continue
        count = to_int(row[COLUMN_COUNT])
        if count is None:
            continue
        zone = str(row[COLUMN_ZONE] or "").strip()
        folded.setdefault(sido, {})[zone] = count
    return folded


def zone_counts(zones: dict[str, int]) -> tuple[int, int]:
    """(총계, 구역 내) 를 돌려줍니다. 총계 열이 있으면 그것을 신뢰합니다."""
    inside = zones.get(ZONE_INSIDE, 0)
    outside = zones.get(ZONE_OUTSIDE, 0)
    total = zones.get(ZONE_TOTAL)
    if total is None:
        total = inside + outside
    return total, inside


def municipality_index(district_master: list[dict]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for row in district_master:
        parts = (row.get("full_name") or "").split()
        if len(parts) >= 2:
            index[(parts[0], parts[-1])] = row["sgg_code"]
    return index


def build(
    rows: list[list], district_master: list[dict]
) -> tuple[list[dict], dict]:
    folded = parse_rows(rows)
    index = municipality_index(district_master)

    buckets: dict[str, dict] = {}
    unresolved: set[tuple[str, str]] = set()

    covered_sido: set[str] = set()

    for (sido, sigungu), zones in folded.items():
        # 시군구명에 일반구가 붙어 오면('성남시 수정구') 첫 토큰만 씁니다.
        code = index.get((sido, sigungu.split()[0]))
        if code is None:
            unresolved.add((sido, sigungu))
            continue
        covered_sido.add(sido)

        total, inside = zone_counts(zones)
        bucket = buckets.setdefault(
            code,
            {"sgg_code": code, "manhole_total": 0, "heavy_rain_zone_manholes": 0},
        )
        bucket["manhole_total"] += total
        bucket["heavy_rain_zone_manholes"] += inside

    # 시군구 행이 하나도 없는 시도(세종)는 시도 행으로 채웁니다. 자치구가 따로
    # 있는 시도는 covered_sido 에 들어 있어 여기서 걸러지므로 이중 계상되지
    # 않습니다.
    for sido, zones in parse_sido_rows(rows).items():
        if sido in covered_sido:
            continue
        code = index.get((sido, sido)) or next(
            (value for (key_sido, _), value in index.items() if key_sido == sido), None
        )
        if code is None or code in buckets:
            continue
        total, inside = zone_counts(zones)
        buckets[code] = {
            "sgg_code": code,
            "manhole_total": total,
            "heavy_rain_zone_manholes": inside,
        }

    districts = []
    for bucket in buckets.values():
        total = bucket["manhole_total"]
        districts.append(
            {
                **bucket,
                # 비율입니다. 개수를 그대로 쓰면 점수가 "맨홀 많은 곳 순위"가
                # 되어 사실상 도시 규모의 복사본이 됩니다.
                "heavy_rain_zone_ratio": (
                    round(bucket["heavy_rain_zone_manholes"] / total, 6)
                    if total
                    else None
                ),
            }
        )
    districts.sort(key=lambda row: row["sgg_code"])

    zoned = sum(1 for row in districts if (row["heavy_rain_zone_manholes"] or 0) > 0)
    stats = {
        "districts": len(districts),
        "master_districts": len(district_master),
        "with_zone": zoned,
        "manhole_total": sum(row["manhole_total"] for row in districts),
        "zone_total": sum(row["heavy_rain_zone_manholes"] for row in districts),
        "unresolved": sorted(unresolved),
    }
    return districts, stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="맨홀현황에서 집중강우 중점관리구역 비율을 추출합니다."
    )
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--xlsx", type=Path, default=paths.SEWER_STATS_XLSX)
    parser.add_argument("--districts", type=Path, default=paths.DISTRICTS_MASTER)
    parser.add_argument("--data", type=Path, default=paths.DATA)
    args = parser.parse_args()

    # 트리거가 "인자 하나라도 있으면"이라 --xlsx 만 줘도 수집이 돌았습니다.
    # 도움말대로 --build 를 명시해야 실행합니다.
    if not args.build:
        parser.error("실제 수집은 --build 를 명시해야 합니다 (인자 없이 실행 = 자체 검증)")

    if not args.xlsx.exists():
        raise SystemExit(f"하수도통계 xlsx 가 없습니다: {args.xlsx}")

    book = Workbook(str(args.xlsx))
    if SHEET not in book.sheets:
        raise SystemExit(
            f"시트를 찾을 수 없습니다: {SHEET!r}\n"
            "  2-5 맨홀현황은 2025판에 신설된 시트입니다. 2024판에는 없습니다."
        )

    rows = list(book.rows(SHEET))
    district_master = json.loads(args.districts.read_text(encoding="utf-8"))
    districts, stats = build(rows, district_master)

    print(f"'{SHEET}' 행 {len(rows)}개")
    print(
        f"시군구 {stats['districts']}/{stats['master_districts']}개 · "
        f"맨홀 {stats['manhole_total']:,}개 · "
        f"중점관리구역 내 {stats['zone_total']:,}개"
    )
    print(
        f"구역이 지정된 시군구 {stats['with_zone']}개 "
        f"(0개인 곳은 미지정이며 결측이 아닙니다)"
    )
    if stats["unresolved"]:
        print(f"  [!] 지역명을 못 붙인 조합 {len(stats['unresolved'])}개: "
              f"{stats['unresolved'][:5]}")

    top = sorted(
        (row for row in districts if row["heavy_rain_zone_ratio"]),
        key=lambda row: -row["heavy_rain_zone_ratio"],
    )[:8]
    master_names = {row["sgg_code"]: row["full_name"] for row in district_master}
    print("\n중점관리구역 비율 상위 8곳")
    for row in top:
        print(
            f"  {master_names.get(row['sgg_code'], row['sgg_code']):22} "
            f"{row['heavy_rain_zone_ratio']:.1%}  "
            f"({row['heavy_rain_zone_manholes']:,}/{row['manhole_total']:,})"
        )

    args.data.mkdir(parents=True, exist_ok=True)
    target = args.data / paths.MANHOLE_BY_SGG_FILE
    target.write_text(
        json.dumps(districts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n→ {target} ({len(districts)}개)")


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "OK  " if condition else "FAIL"
        print(f"  [{mark}] {label}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures += 1

    print("정수 변환")
    check("천단위 구분자", to_int("3,857,636") == 3857636)
    check("빈 값은 None", to_int("") is None)
    check("하이픈은 None", to_int("-") is None)
    check("0 은 0 (None 아님)", to_int("0") == 0)

    print("\n행 접기")
    rows = [
        ["2_5_맨홀현황"],
        ["행정구역", None, "집중강우"],
        ["시도", "시군구", None, "계"],
        ["전국", None, "총계", "3857636"],           # 전국 총계 — 건너뛰어야 함
        ["서울특별시", None, "총계", "289718"],        # 시도 소계 — 건너뛰어야 함
        ["서울특별시", "종로구", "집중강우중점관리구역 내", "480"],
        ["서울특별시", "종로구", "그외", "9646"],
        ["광주광역시", "북구", "집중강우중점관리구역 내", "2437"],
        ["광주광역시", "북구", "그외", "18124"],
    ]
    folded = parse_rows(rows)
    check("시군구 2곳만 접힘 (전국·시도 소계 제외)", len(folded) == 2, str(list(folded)))
    check(
        "종로구 구역 내 480",
        folded[("서울특별시", "종로구")][ZONE_INSIDE] == 480,
    )

    print("\n집계")
    master = [
        {"sgg_code": "11110", "full_name": "서울특별시 종로구"},
        {"sgg_code": "29170", "full_name": "광주광역시 북구"},
        {"sgg_code": "29200", "full_name": "광주광역시 광산구"},
    ]
    districts, stats = build(rows, master)
    by_code = {row["sgg_code"]: row for row in districts}
    check("시군구 2개", len(districts) == 2, str(len(districts)))
    check("종로 총계 = 480+9646", by_code["11110"]["manhole_total"] == 10126)
    check(
        "종로 비율 = 480/10126",
        by_code["11110"]["heavy_rain_zone_ratio"] == round(480 / 10126, 6),
        str(by_code["11110"]),
    )
    check("북구 구역 내 2437", by_code["29170"]["heavy_rain_zone_manholes"] == 2437)
    check("자료 없는 시군구는 아예 없음", "29200" not in by_code)
    check("구역 지정 시군구 2개", stats["with_zone"] == 2, str(stats["with_zone"]))

    print("\n총계 열이 있으면 그것을 신뢰")
    with_total = [
        ["", ], ["", ], ["", ],
        ["광주광역시", "동구", "총계", "15854"],
        ["광주광역시", "동구", "집중강우중점관리구역 내", "80"],
        ["광주광역시", "동구", "그외", "15774"],
    ]
    got, _ = build(with_total, [{"sgg_code": "29110", "full_name": "광주광역시 동구"}])
    check("총계 15854 사용", got[0]["manhole_total"] == 15854, str(got[0]))

    print("\n일반구는 기초자치단체로 합침")
    general = [
        ["", ], ["", ], ["", ],
        ["경기도", "성남시 수정구", "집중강우중점관리구역 내", "100"],
        ["경기도", "성남시 수정구", "그외", "900"],
        ["경기도", "성남시 중원구", "집중강우중점관리구역 내", "50"],
        ["경기도", "성남시 중원구", "그외", "950"],
    ]
    got, _ = build(general, [{"sgg_code": "41130", "full_name": "경기도 성남시"}])
    check("성남시로 합산", len(got) == 1 and got[0]["manhole_total"] == 2000, str(got))
    check("구역 내도 합산", got[0]["heavy_rain_zone_manholes"] == 150)

    print("\n구역이 0개인 곳")
    zero = [
        ["", ], ["", ], ["", ],
        ["광주광역시", "광산구", "집중강우중점관리구역 내", "0"],
        ["광주광역시", "광산구", "그외", "27843"],
    ]
    got, stats = build(zero, [{"sgg_code": "29200", "full_name": "광주광역시 광산구"}])
    check("비율 0.0 (None 아님: 관측된 0)", got[0]["heavy_rain_zone_ratio"] == 0.0,
          str(got[0]))
    check("구역 지정 시군구 0개", stats["with_zone"] == 0)

    print("\n못 붙는 지역")
    unknown = [["", ], ["", ], ["", ], ["없는도", "없는시", "총계", "10"]]
    got, stats = build(unknown, master)
    check("미해결로 기록", stats["unresolved"] == [("없는도", "없는시")], str(stats))
    check("결과에는 안 들어감", got == [])

    print("\n소계 라벨은 지역이 아님")
    subtotal = [
        ["", ], ["", ], ["", ],
        ["경기도", "시부", "총계", "500000"],
        ["경기도", "군부", "총계", "50000"],
        ["경기도", "수원시", "집중강우중점관리구역 내", "100"],
        ["경기도", "수원시", "그외", "900"],
    ]
    got, stats = build(subtotal, [{"sgg_code": "41110", "full_name": "경기도 수원시"}])
    check("시부·군부 제외", len(got) == 1 and got[0]["manhole_total"] == 1000, str(got))
    check("미해결로도 안 잡힘", stats["unresolved"] == [], str(stats["unresolved"]))

    print("\n시군구 행이 없는 단층 자치단체(세종)는 시도 행으로 채움")
    sejong = [
        ["", ], ["", ], ["", ],
        ["전국", None, "총계", "999999"],
        ["세종특별자치시", None, "총계", "40000"],
        ["세종특별자치시", None, "집중강우중점관리구역 내", "1200"],
        ["세종특별자치시", None, "그외", "38800"],
    ]
    got, _ = build(sejong, [{"sgg_code": "36110", "full_name": "세종특별자치시 세종시"}])
    check("세종 채워짐", len(got) == 1 and got[0]["sgg_code"] == "36110", str(got))
    check("총계 40000", got[0]["manhole_total"] == 40000, str(got[0]))
    check("구역 내 1200", got[0]["heavy_rain_zone_manholes"] == 1200)

    print("\n자치구가 있는 시도는 시도 행을 쓰지 않음 (이중 계상 방지)")
    seoul = [
        ["", ], ["", ], ["", ],
        ["서울특별시", None, "총계", "289718"],
        ["서울특별시", "종로구", "집중강우중점관리구역 내", "480"],
        ["서울특별시", "종로구", "그외", "9646"],
    ]
    got, _ = build(seoul, [{"sgg_code": "11110", "full_name": "서울특별시 종로구"}])
    check("종로구만 남고 시도 행 무시", len(got) == 1 and got[0]["manhole_total"] == 10126,
          str(got))

    print("\n빈 입력")
    got, stats = build([], [])
    check("빈 결과", got == [])
    check("합계 0", stats["manhole_total"] == 0)

    print()
    print(f"결과: {'실패 ' + str(failures) + '건' if failures else '전부 통과'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        raise SystemExit(_self_test())
