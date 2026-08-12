from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

import paths

# 기상청 지상(종관, ASOS) 일자료 조회서비스 — 공공데이터포털.
#   https://www.data.go.kr/data/15059093/openapi.do (활용신청 필요)
# 오퍼레이션: getWthrDataList, 필드 sumRn = 일강수량(mm).
#
# 왜 관측소 → 시군구 매핑이 필요한가: ASOS 는 전국 약 100개 지점뿐이라
# 시군구(229)마다 관측소가 있지 않습니다. 각 시군구의 대표점(경계 bbox 중심)에서
# 가장 가까운 관측소의 값을 씁니다. 산악 국지성 강우는 못 잡지만, 트리거
# (최근 강수 상위 지역 표시) 용도로는 이 해상도로 충분합니다.
# https 여야 합니다. http:// 는 연결 후 응답이 오지 않아 타임아웃으로 죽습니다
# (2026-08-03 실키로 확인. 지하안전정보도 같은 증상이었습니다).
ASOS_BASE = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"

WINDOW_DAYS = 30
PAGE_ROWS = 900  # 30일 × 관측소 100개 = 3,000행. 관측소 묶음당 넉넉히.
TIMEOUT = 30

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # 대원거리. 시군구 대표점 ↔ 관측소 거리 비교에만 쓰므로 근사면 충분하지만,
    # 위도차·경도차 단순 유클리드는 위도에 따라 왜곡되어 순서가 바뀔 수 있습니다.
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def district_centroids(boundaries: dict) -> dict[str, tuple[float, float]]:
    # bbox 중심. 도형 무게중심이 아니라도 '가장 가까운 관측소' 선택에는 충분하고,
    # 다도해 시군(신안 등)에서도 관측소 순서가 뒤집힐 만큼 어긋나지 않습니다.
    out: dict[str, tuple[float, float]] = {}
    for feature in boundaries.get("features", []):
        code = feature["properties"]["code"]
        min_lon = min_lat = math.inf
        max_lon = max_lat = -math.inf
        geometry = feature["geometry"]
        polygons = (
            geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else [geometry["coordinates"]]
        )
        for polygon in polygons:
            for ring in polygon:
                for lon, lat in ring:
                    min_lon = min(min_lon, lon)
                    max_lon = max(max_lon, lon)
                    min_lat = min(min_lat, lat)
                    max_lat = max(max_lat, lat)
        out[code] = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
    return out


def nearest_station(
    point: tuple[float, float], stations: list[dict]
) -> tuple[dict, float]:
    best = None
    best_km = math.inf
    for station in stations:
        km = haversine_km(point[0], point[1], station["lat"], station["lon"])
        if km < best_km:
            best, best_km = station, km
    if best is None:
        raise ValueError("관측소 목록이 비어 있습니다.")
    return best, best_km


def sum_rainfall(daily_rows: list[dict]) -> dict[str, dict]:
    # 관측소별 30일 합. sumRn 이 빈 문자열이면 '강수 없음'(0)이 아니라 결측일로
    # 세어 둡니다 — 관측 공백이 많은 지점의 합계는 과소이므로 표시해야 합니다.
    out: dict[str, dict] = {}
    for row in daily_rows:
        stn = str(row.get("stnId", "")).strip()
        if not stn:
            continue
        slot = out.setdefault(stn, {"sum_mm": 0.0, "days": 0, "missing_days": 0})
        raw = str(row.get("sumRn", "")).strip()
        try:
            value = float(raw)
        except ValueError:
            # 빈 문자열·쓰레기 값 모두 결측일입니다. float() 예외가 그대로
            # 올라가면 값 하나 때문에 수집 전체가 죽습니다.
            slot["missing_days"] += 1
            continue
        slot["sum_mm"] = round(slot["sum_mm"] + value, 1)
        slot["days"] += 1
    return out


def fetch_station(service_key: str, stn_id: str, start: str, end: str) -> list[dict]:
    """지점 하나의 일자료를 받습니다.

    `stnIds` 는 **한 번에 하나만** 받습니다. 쉼표로 묶으면 `DB_ERROR` 가 오고
    행이 0개가 됩니다 (2026-08-03 실키로 확인). 그래서 지점별로 호출합니다.
    """
    rows: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "dataCd": "ASOS",
                "dateCd": "DAY",
                "startDt": start,
                "endDt": end,
                "stnIds": stn_id,
                "numOfRows": PAGE_ROWS,
                "pageNo": page,
                "dataType": "JSON",
            }
        )
        # serviceKey 는 이미 인코딩된 형태로 발급되므로 직접 이어 붙입니다
        # (urlencode 를 거치면 이중 인코딩되어 401 이 납니다 — 침하 수집기와 동일).
        url = f"{ASOS_BASE}?serviceKey={service_key}&{query}"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        response_body = payload.get("response", {})
        header = response_body.get("header", {})
        body = response_body.get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        if header.get("resultCode") not in ("00", "0") and not items:
            raise RuntimeError(
                f"지점 {stn_id}: {header.get('resultMsg', '알 수 없는 오류')}"
            )
        rows.extend(items)
        total = int(body.get("totalCount") or 0)
        if page * PAGE_ROWS >= total or not items:
            return rows
        page += 1


def fetch_daily(service_key: str, stn_ids: list[str], start: str, end: str) -> list[dict]:
    rows: list[dict] = []
    failures: list[str] = []
    for index, stn_id in enumerate(stn_ids, 1):
        try:
            rows.extend(fetch_station(service_key, stn_id, start, end))
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            # 지점 하나가 실패해도 나머지는 살립니다. 빠진 지점은 그 지점을
            # 최근접으로 쓰는 시군구가 결측이 되므로 반드시 보고합니다.
            failures.append(f"{stn_id}({error})")
        if index % 20 == 0:
            print(f"    {index}/{len(stn_ids)} 지점")
    if failures:
        print(f"  [!] 실패 지점 {len(failures)}개: {', '.join(failures[:5])}")
    return rows


def build(
    stations: list[dict],
    daily_rows: list[dict],
    boundaries: dict,
    master: list[dict],
    window: tuple[str, str],
) -> list[dict]:
    centroids = district_centroids(boundaries)
    by_station = sum_rainfall(daily_rows)

    out = []
    for row in master:
        code = row["sgg_code"]
        point = centroids.get(code)
        if point is None:
            continue
        station, distance_km = nearest_station(point, stations)
        record = by_station.get(str(station["stn_id"]), None)
        out.append(
            {
                "sgg_code": code,
                "full_name": row["full_name"],
                "rainfall_30d_mm": None if record is None else record["sum_mm"],
                "observed_days": 0 if record is None else record["days"],
                # 창 길이에서 관측일을 뺍니다. 행 단위 결측 카운트만 쓰면 API 가
                # 특정 일자의 행 자체를 빠뜨렸을 때 결측이 과소 보고됩니다.
                "missing_days": WINDOW_DAYS
                - (0 if record is None else record["days"]),
                "station_id": str(station["stn_id"]),
                "station_name": station.get("name", ""),
                "station_distance_km": round(distance_km, 1),
                "window_start": window[0],
                "window_end": window[1],
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="기상청 ASOS 일자료로 시군구별 최근 30일 강수량을 수집합니다."
    )
    parser.add_argument("--key", required=True, help="공공데이터포털 서비스 키 (Encoding)")
    parser.add_argument(
        "--stations",
        type=Path,
        required=True,
        help="관측소 목록 JSON: [{stn_id, name, lat, lon}] — 기상청 지점정보에서 작성",
    )
    parser.add_argument("--data", type=Path, default=paths.DATA)
    parser.add_argument("--end", default=None, help="집계 종료일 YYYYMMDD (기본: 어제)")
    args = parser.parse_args()

    stations = json.loads(args.stations.read_text(encoding="utf-8"))
    boundaries = json.loads(
        (args.data / paths.MUNICIPALITY_BOUNDARIES_FILE).read_text(encoding="utf-8")
    )
    master = json.loads(
        (args.data / paths.DISTRICTS_MASTER_FILE).read_text(encoding="utf-8")
    )

    end_day = (
        date.today() - timedelta(days=1)
        if args.end is None
        else date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:]))
    )
    start_day = end_day - timedelta(days=WINDOW_DAYS - 1)
    window = (start_day.strftime("%Y%m%d"), end_day.strftime("%Y%m%d"))

    print(f"ASOS 일자료 수집: {window[0]} ~ {window[1]}, 관측소 {len(stations)}개")
    stn_ids = [str(station["stn_id"]) for station in stations]
    daily = fetch_daily(args.key, stn_ids, window[0], window[1])
    print(f"  일자료 {len(daily)}행")

    rows = build(stations, daily, boundaries, master, window)
    out_path = args.data / paths.RAINFALL_BY_SGG_FILE
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)
    observed = sum(1 for row in rows if row["rainfall_30d_mm"] is not None)
    print(f"→ {out_path} ({len(rows)}개 시군구, 관측 {observed}개)")


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

    print("거리")
    seoul_busan = haversine_km(37.5665, 126.9780, 35.1796, 129.0756)
    check("서울-부산 약 325km", 320 <= seoul_busan <= 330, f"{seoul_busan:.1f}")
    check("같은 점은 0", haversine_km(36.0, 127.0, 36.0, 127.0) == 0.0)

    print()
    print("최근접 관측소")
    stations = [
        {"stn_id": 108, "name": "서울", "lat": 37.5714, "lon": 126.9658},
        {"stn_id": 159, "name": "부산", "lat": 35.1047, "lon": 129.0320},
        {"stn_id": 156, "name": "광주", "lat": 35.1729, "lon": 126.8916},
    ]
    station, km = nearest_station((37.50, 127.00), stations)
    check("서울 근처는 서울 지점", station["stn_id"] == 108, str(station))
    check("거리도 함께 반환", 0 < km < 20, f"{km:.1f}")
    station, _ = nearest_station((35.15, 126.90), stations)
    check("광주 근처는 광주 지점", station["stn_id"] == 156, str(station))

    print()
    print("30일 합계")
    daily = [
        {"stnId": "108", "sumRn": "12.5"},
        {"stnId": "108", "sumRn": ""},        # 결측일 — 0으로 세면 안 됨
        {"stnId": "108", "sumRn": "0.5"},
        {"stnId": "108", "sumRn": "점검중"},   # 쓰레기 값 — 예외로 죽으면 안 됨
        {"stnId": "156", "sumRn": "3.0"},
    ]
    sums = sum_rainfall(daily)
    check("합계 13.0", sums["108"]["sum_mm"] == 13.0, str(sums["108"]))
    check("관측 2일", sums["108"]["days"] == 2)
    check("빈 값·쓰레기 값은 결측일", sums["108"]["missing_days"] == 2, str(sums["108"]))
    check("지점별 분리", sums["156"]["sum_mm"] == 3.0)

    print()
    print("시군구 매핑")
    boundaries = {
        "type": "FeatureCollection",
        "features": [
            {
                "properties": {"code": "11110"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[126.95, 37.55], [127.0, 37.55], [127.0, 37.60], [126.95, 37.60], [126.95, 37.55]]],
                },
            },
            {
                "properties": {"code": "29110"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[126.88, 35.12], [126.94, 35.12], [126.94, 35.18], [126.88, 35.18], [126.88, 35.12]]],
                },
            },
        ],
    }
    master = [
        {"sgg_code": "11110", "full_name": "서울특별시 종로구"},
        {"sgg_code": "29110", "full_name": "광주광역시 동구"},
    ]
    rows = build(stations, daily, boundaries, master, ("20260703", "20260801"))
    check("시군구 2개 산출", len(rows) == 2, str(len(rows)))
    by_code = {row["sgg_code"]: row for row in rows}
    check("종로 → 서울 지점 13.0mm", by_code["11110"]["rainfall_30d_mm"] == 13.0,
          str(by_code["11110"]))
    check("동구 → 광주 지점 3.0mm", by_code["29110"]["rainfall_30d_mm"] == 3.0,
          str(by_code["29110"]))
    # 관측 2일이면 결측은 30-2=28일입니다. 행이 아예 안 온 날도 결측으로
    # 세야 하므로 행 단위 카운트(빈 값 1 + 쓰레기 1)가 아니라 창 기준입니다.
    check(
        "결측일 = 창 길이 - 관측일",
        by_code["11110"]["missing_days"] == WINDOW_DAYS - 2,
        str(by_code["11110"]["missing_days"]),
    )
    check("관측소 거리 실림", by_code["11110"]["station_distance_km"] < 20,
          str(by_code["11110"]["station_distance_km"]))

    print()
    print("자료 없는 관측소")
    rows = build(
        [{"stn_id": 999, "name": "무자료", "lat": 37.57, "lon": 126.97}],
        [], boundaries, master, ("20260703", "20260801"),
    )
    check("값은 None (0 아님)", rows[0]["rainfall_30d_mm"] is None, str(rows[0]))
    check("결측 30일", rows[0]["missing_days"] == WINDOW_DAYS)

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
