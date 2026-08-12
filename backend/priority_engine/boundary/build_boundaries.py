from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402

# 좌표 비교용 양자화 자릿수. 인접 읍면동은 공유 변의 정점이 동일하므로
# 반올림 후 정확히 일치해야 하며, 그래야 내부 경계가 상쇄됩니다.
QUANTIZE = 7

# 광역시가 아닌 시도 = 일반구를 가질 수 있는 도. 이 목록의 시도에서
# 이름이 '구'로 끝나는 시군구는 자치구가 아니라 일반구(행정구)입니다.
METROPOLITAN_SIDO_CODES = {"11", "26", "27", "28", "29", "30", "31"}


def is_general_district(sido_code: str, sgg_name: str) -> bool:
    return sido_code not in METROPOLITAN_SIDO_CODES and sgg_name.endswith("구")


def to_municipality(sgg_code: str, sido_code: str, sgg_name: str) -> tuple[str, str]:
    if not is_general_district(sido_code, sgg_name):
        return sgg_code, sgg_name

    parent_code = sgg_code[:4] + "0"

    cut = sgg_name.find("시")
    parent_name = sgg_name[: cut + 1] if cut != -1 else sgg_name

    return parent_code, parent_name


# ---------------------------------------------------------------- 링 유틸


def quantize_ring(ring: list) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for x, y in ring:
        point = (round(float(x), QUANTIZE), round(float(y), QUANTIZE))
        if not out or out[-1] != point:
            out.append(point)
    # 닫힌 링의 마지막 중복점 제거 (내부 처리는 열린 형태로 통일)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def iter_polygon_rings(geometry: dict):
    geometry_type = geometry.get("type")

    if geometry_type == "Polygon":
        for ring in geometry["coordinates"]:
            yield ring
    elif geometry_type == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            for ring in polygon:
                yield ring


def signed_area(ring: list[tuple[float, float]]) -> float:
    total = 0.0
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    count = len(ring)

    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]

        if (y1 > y) != (y2 > y):
            if y2 != y1:
                cross_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < cross_x:
                    inside = not inside

    return inside


# ---------------------------------------------------------------- dissolve


def dissolve_rings(rings: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    edge_counter: Counter = Counter()
    directed: dict[tuple, tuple] = {}

    for ring in rings:
        count = len(ring)
        if count < 3:
            continue
        for index in range(count):
            start = ring[index]
            end = ring[(index + 1) % count]
            if start == end:
                continue
            key = (start, end) if start <= end else (end, start)
            edge_counter[key] += 1
            directed[key] = (start, end)

    # 등장 1회 = 외곽 변
    outline: dict[tuple[float, float], list[tuple[float, float]]] = defaultdict(list)
    for key, times in edge_counter.items():
        if times != 1:
            continue
        start, end = directed[key]
        outline[start].append(end)

    # 이어 붙이기
    result: list[list[tuple[float, float]]] = []
    remaining = {start: list(ends) for start, ends in outline.items()}

    while remaining:
        start_point = next(iter(remaining))
        ring = [start_point]
        current = start_point

        while True:
            candidates = remaining.get(current)
            if not candidates:
                break

            next_point = candidates.pop()
            if not candidates:
                remaining.pop(current, None)

            if next_point == start_point:
                break

            ring.append(next_point)
            current = next_point

            # 비정상 데이터로 인한 무한 루프 방지
            if len(ring) > 2_000_000:
                break

        if len(ring) >= 3:
            result.append(ring)

    return result


def assign_holes(rings: list[list[tuple[float, float]]]) -> list[list[list[tuple[float, float]]]]:
    outers: list[list[tuple[float, float]]] = []
    holes: list[list[tuple[float, float]]] = []

    for ring in rings:
        if signed_area(ring) >= 0:
            outers.append(ring)
        else:
            holes.append(ring)

    # 큰 링부터 검사해 가장 작은 포함 외곽에 구멍을 붙입니다.
    outers.sort(key=lambda r: abs(signed_area(r)), reverse=True)
    polygons: list[list[list[tuple[float, float]]]] = [[outer] for outer in outers]

    for hole in holes:
        probe = hole[0]
        best_index = None
        best_area = None

        for index, polygon in enumerate(polygons):
            outer = polygon[0]
            if point_in_ring(probe, outer):
                area = abs(signed_area(outer))
                if best_area is None or area < best_area:
                    best_area = area
                    best_index = index

        if best_index is None:
            # 포함하는 외곽이 없으면 방향을 뒤집어 독립 폴리곤으로 취급
            polygons.append([list(reversed(hole))])
        else:
            polygons[best_index].append(hole)

    return polygons


# ---------------------------------------------------------------- 단순화


def simplify_ring(
    ring: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if tolerance <= 0 or len(ring) <= 4:
        return ring

    closed = ring + [ring[0]]
    keep = [False] * len(closed)
    keep[0] = True
    keep[-1] = True

    stack = [(0, len(closed) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        x1, y1 = closed[first]
        x2, y2 = closed[last]
        dx = x2 - x1
        dy = y2 - y1
        norm = math.hypot(dx, dy)

        max_distance = -1.0
        max_index = first

        for index in range(first + 1, last):
            px, py = closed[index]
            if norm == 0:
                distance = math.hypot(px - x1, py - y1)
            else:
                distance = abs(dy * (px - x1) - dx * (py - y1)) / norm
            if distance > max_distance:
                max_distance = distance
                max_index = index

        if max_distance > tolerance:
            keep[max_index] = True
            stack.append((first, max_index))
            stack.append((max_index, last))

    simplified = [point for point, flag in zip(closed, keep) if flag]

    # 닫힘 중복 제거
    if len(simplified) > 1 and simplified[0] == simplified[-1]:
        simplified.pop()

    # 면적이 사라질 정도로 붕괴하면 원본 유지 (소면적 자치구 소실 방지)
    if len(simplified) < 4:
        return ring

    return simplified


def simplify_polygons(polygons, tolerance: float):
    out = []
    for polygon in polygons:
        rings = []
        for index, ring in enumerate(polygon):
            simplified = simplify_ring(ring, tolerance)
            if index == 0 and len(simplified) < 4:
                simplified = ring
            if len(simplified) >= 4 or index == 0:
                rings.append(simplified)
        if rings:
            out.append(rings)
    return out


def to_geojson_coordinates(polygons) -> list:
    coordinates = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            closed = [list(point) for point in ring]
            closed.append(list(ring[0]))
            rings.append(closed)
        coordinates.append(rings)
    return coordinates


# ---------------------------------------------------------------- 메인


def build_level(
    groups: dict[str, list],
    meta: dict[str, dict],
    out_path: Path,
    tolerance: float,
    label: str,
) -> None:
    print(f"{label} 경계 dissolve 중... ({len(groups)}개)")

    features = []
    for index, (code, group) in enumerate(sorted(groups.items()), start=1):
        rings = []
        for feature in group:
            for raw_ring in iter_polygon_rings(feature["geometry"]):
                ring = quantize_ring(raw_ring)
                if len(ring) >= 3:
                    rings.append(ring)

        polygons = simplify_polygons(
            assign_holes(dissolve_rings(rings)),
            tolerance,
        )

        row = meta[code]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "code": code,
                    "sido_name": row["sido_name"],
                    "name": row["name"],
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": to_geojson_coordinates(polygons),
                },
            }
        )

        if index % 100 == 0:
            print(f"  {index}/{len(groups)}")

    repair_invalid(features)

    write_json(out_path, {"type": "FeatureCollection", "features": features})
    print(f"  → {out_path.name} ({len(features)}개, {out_path.stat().st_size:,} bytes)")


def repair_invalid(features: list[dict]) -> int:
    try:
        from shapely.geometry import mapping, shape
        from shapely.validation import make_valid
    except ImportError:
        print("  [!] shapely 없음: 지오메트리 유효성 검사를 건너뜁니다.")
        print("     pip install shapely 후 재실행하면 자기교차를 복구합니다.")
        return 0

    repaired = 0
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.is_valid:
            continue

        fixed = make_valid(geometry)
        # make_valid는 GeometryCollection을 돌려줄 수 있습니다. 면(Polygon)만 남깁니다.
        if fixed.geom_type == "GeometryCollection":
            parts = [part for part in fixed.geoms if part.geom_type.endswith("Polygon")]
            if not parts:
                continue
            fixed = parts[0] if len(parts) == 1 else _union(parts)

        if fixed.geom_type == "Polygon":
            coordinates = [mapping(fixed)["coordinates"]]
        else:
            coordinates = mapping(fixed)["coordinates"]

        feature["geometry"] = {"type": "MultiPolygon", "coordinates": coordinates}
        repaired += 1

    if repaired:
        print(f"  [OK] 무효 지오메트리 {repaired}개 복구 (자기교차·중첩 껍질)")
    return repaired


def _union(parts):
    from shapely.ops import unary_union

    return unary_union(parts)


def build(source: Path, out_dir: Path, tolerance: float, dong_tolerance: float) -> None:
    print(f"불러오기: {source}")
    with source.open(encoding="utf-8") as handle:
        data = json.load(handle)

    features = data["features"]
    print(f"  읍면동 피처 {len(features):,}개")

    # 252 단위(일반구 분리) 마스터·경계는 만들지 않습니다 — 실데이터가 전부
    # 기초자치단체(229) 단위로 확인돼 소비자가 없었습니다. 일반구 데이터가
    # 생기면 이 빌드에 단위 하나 추가하면 됩니다 (원본에서 5초 재생성).
    by_municipality: dict[str, list] = defaultdict(list)
    by_sido: dict[str, list] = defaultdict(list)
    muni_meta: dict[str, dict] = {}
    sido_meta: dict[str, dict] = {}
    all_dongs: list[dict] = []

    for feature in features:
        properties = feature["properties"]
        sgg_code = properties["sgg"]
        sido_code = properties["sido"]
        sgg_name = properties["sggnm"]

        muni_code, muni_name = to_municipality(sgg_code, sido_code, sgg_name)
        by_municipality[muni_code].append(feature)
        muni_meta.setdefault(
            muni_code,
            {
                "sgg_code": muni_code,
                "sido_code": sido_code,
                "sido_name": properties["sidonm"],
                "sgg_name": muni_name,
                "name": muni_name,
                "full_name": f"{properties['sidonm']} {muni_name}".strip(),
            },
        )

        by_sido[sido_code].append(feature)
        sido_meta.setdefault(
            sido_code,
            {"sido_name": properties["sidonm"], "name": properties["sidonm"]},
        )

        all_dongs.append(feature)

    print(f"  기초자치단체(일반구 통합) {len(by_municipality)}개")
    print(f"  읍면동 {len(all_dongs)}개 (전국)")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 마스터
    master = sorted(muni_meta.values(), key=lambda row: row["sgg_code"])
    for row in master:
        row["dong_count"] = len(by_municipality[row["sgg_code"]])
        row.pop("name", None)
    write_json(out_dir / paths.DISTRICTS_MASTER_FILE, master)
    print(f"  → {paths.DISTRICTS_MASTER_FILE} ({len(master)}개)")

    # ---- 광주 읍면동 마스터
    dong_master = []
    for feature in all_dongs:
        properties = feature["properties"]
        # 동의 sgg 는 252 단위(일반구 분리) 코드입니다. 점수·지도가 쓰는 단위는
        # 기초자치단체 229 이므로 여기서 통합해 둡니다 — 안 하면 수원시(41110)의
        # 동들이 장안구(41111) 밑에 있어 조회가 전부 빗나갑니다.
        muni_code, muni_name = to_municipality(
            properties["sgg"], properties["sgg"][:2], properties["sggnm"]
        )
        dong_master.append(
            {
                "adm_code": properties["adm_cd2"],
                "sgg_code": muni_code,
                "sgg_name": muni_name,
                "sgg_detail_code": properties["sgg"],
                "sgg_detail_name": properties["sggnm"],
                "adm_name": properties["adm_nm"],
            }
        )
    dong_master.sort(key=lambda row: row["adm_code"])
    write_json(out_dir / paths.ADMIN_DONGS_MASTER_FILE, dong_master)
    print(f"  → admin_dongs_master.json ({len(dong_master)}개)")

    # ---- 경계
    for code, row in muni_meta.items():
        row["name"] = row["sgg_name"]

    build_level(
        by_municipality,
        muni_meta,
        out_dir / paths.MUNICIPALITY_BOUNDARIES_FILE,
        tolerance,
        "기초자치단체(일반구 통합)",
    )
    # 시도 경계선용. 시군구 경계에서 시도 경계를 추출할 수 없는 이유:
    # 단순화가 시군구별로 독립이라 인접 시군구가 단순화 후 꼭짓점을 공유하지
    # 않고, 좌표 일치로 공유 변을 찾는 방식은 일치가 살아남은 조각만 건져
    # 점선이 됩니다. 단순화 **전에** 시도로 dissolve 해야 연속선이 나옵니다.
    build_level(
        by_sido,
        sido_meta,
        out_dir / paths.SIDO_BOUNDARIES_FILE,
        tolerance,
        "시도",
    )

    # ---- 읍면동 경계 (전국, dissolve 없이 단순화만).
    #      점수 단위는 시군구이므로 동은 경계·이름만 필요합니다.
    print("읍면동 경계 단순화 중 (전국)...")
    dong_features = []
    for feature in all_dongs:
        properties = feature["properties"]
        polygons = []
        for raw_ring in iter_polygon_rings(feature["geometry"]):
            ring = quantize_ring(raw_ring)
            if len(ring) >= 3:
                polygons.append([ring])
        polygons = simplify_polygons(polygons, dong_tolerance)

        # 마스터와 같은 이유로 기초자치단체 코드로 통합합니다.
        muni_code, muni_name = to_municipality(
            properties["sgg"], properties["sgg"][:2], properties["sggnm"]
        )
        dong_features.append(
            {
                "type": "Feature",
                "properties": {
                    "adm_code": properties["adm_cd2"],
                    "sgg_code": muni_code,
                    "sgg_name": muni_name,
                    "adm_name": properties["adm_nm"],
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": to_geojson_coordinates(polygons),
                },
            }
        )

    repair_invalid(dong_features)

    write_json(
        out_dir / paths.ADMIN_DONGS_GEOJSON_FILE,
        {"type": "FeatureCollection", "features": dong_features},
    )
    size = (out_dir / paths.ADMIN_DONGS_GEOJSON_FILE).stat().st_size
    print(f"  → {paths.ADMIN_DONGS_GEOJSON_FILE} ({len(dong_features)}개, {size:,} bytes)")


def write_json(path: Path, payload) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="읍면동 GeoJSON에서 시군구 마스터와 경계 폴리곤을 생성합니다.")
    parser.add_argument(
        "source",
        type=Path,
        help="HangJeongDong_ver20251231.geojson 경로",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=paths.DATA,
        help="출력 디렉터리 (기본: priority_engine/data)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0012,
        help="시군구 단순화 허용 오차(도). 0.0012도 ≈ 130m",
    )
    parser.add_argument(
        "--dong-tolerance",
        type=float,
        default=0.0003,
        help="읍면동 단순화 허용 오차(도). 0.0003도 ≈ 33m",
    )
    args = parser.parse_args()

    build(args.source, args.out, args.tolerance, args.dong_tolerance)


# ---------------------------------------------------------------- 자체 검증


def _square(x: float, y: float, size: float = 1.0) -> list[tuple[float, float]]:
    """반시계 방향 정사각형 (열린 링). GeoJSON 외곽 규약과 같습니다."""
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("일반구 → 기초자치단체")
    check("도의 '구'는 일반구", is_general_district("41", "수원시장안구"))
    check("광역시의 '구'는 자치구", not is_general_district("29", "서구"))
    check("군은 일반구 아님", not is_general_district("41", "양평군"))
    check("수원시 장안구 → 수원시",
          to_municipality("41111", "41", "수원시장안구") == ("41110", "수원시"))
    check("청주시 상당구 → 청주시",
          to_municipality("43111", "43", "청주시상당구") == ("43110", "청주시"))
    check("자치구는 그대로",
          to_municipality("29140", "29", "서구") == ("29140", "서구"))
    check("일반 시·군은 그대로",
          to_municipality("41830", "41", "양평군") == ("41830", "양평군"))

    print()
    print("링 정규화 (quantize_ring)")
    ring = quantize_ring([[0, 0], [0, 0], [1.00000004, 0], [1, 1], [0, 1], [0, 0]])
    check("연속 중복·닫힘 중복 제거, 좌표 양자화",
          ring == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], str(ring))
    check("소수 7자리 차이는 같은 정점 (인접 경계 상쇄의 전제)",
          quantize_ring([[126.90000004, 35.1]]) == [(126.9, 35.1)],
          str(quantize_ring([[126.90000004, 35.1]])))

    print()
    print("부호 면적 (signed_area)")
    check("반시계 사각형 = +1", signed_area(_square(0, 0)) == 1.0)
    check("시계 방향 = -1", signed_area(list(reversed(_square(0, 0)))) == -1.0)
    check("삼각형 = 0.5", signed_area([(0, 0), (1, 0), (0, 1)]) == 0.5)

    print()
    print("점-폴리곤 포함 (point_in_ring)")
    square = _square(0, 0)
    check("내부", point_in_ring((0.5, 0.5), square))
    check("외부", not point_in_ring((1.5, 0.5), square))
    check("경계 바로 안쪽", point_in_ring((0.9999999, 0.5), square))
    check("경계 바로 바깥쪽", not point_in_ring((1.0000001, 0.5), square))
    check("꼭짓점 위쪽 바깥", not point_in_ring((0.5, 1.0000001), square))
    triangle = [(0.0, 0.0), (2.0, 0.0), (1.0, 2.0)]
    check("삼각형 내부", point_in_ring((1.0, 0.5), triangle))
    check("삼각형 빗변 바깥", not point_in_ring((0.2, 1.5), triangle))

    print()
    print("dissolve: 공유 변 상쇄")
    merged = dissolve_rings([_square(0, 0), _square(1, 0)])
    check("인접 사각형 2개 → 링 1개", len(merged) == 1, str(len(merged)))
    check("외곽 정점 6개 (공유 변 제거)", len(merged[0]) == 6, str(merged))
    check("면적 보존 = 2", abs(signed_area(merged[0])) == 2.0,
          str(signed_area(merged[0])))

    grid = [_square(x, y) for x in (0, 1) for y in (0, 1)]
    merged = dissolve_rings(grid)
    check("2×2 격자 → 링 1개, 면적 4",
          len(merged) == 1 and abs(signed_area(merged[0])) == 4.0, str(merged))
    check("내부 정점 (1,1) 은 외곽에 없음", (1.0, 1.0) not in merged[0], str(merged[0]))

    check("떨어진 사각형 2개는 링 2개",
          len(dissolve_rings([_square(0, 0), _square(5, 0)])) == 2, "")
    check("퇴화 링(정점<3)은 무시", dissolve_rings([[(0, 0), (1, 1)]]) == [], "")

    print()
    print("dissolve + 구멍 배정: 도넛 (3×3 격자에서 가운데 제거)")
    donut = [
        _square(x, y) for x in (0, 1, 2) for y in (0, 1, 2) if (x, y) != (1, 1)
    ]
    polygons = assign_holes(dissolve_rings(donut))
    check("폴리곤 1개", len(polygons) == 1, str(len(polygons)))
    check("외곽 + 구멍 링 2개", len(polygons[0]) == 2,
          str([len(r) for r in polygons[0]]))
    outer, hole = polygons[0][0], polygons[0][1]
    check("외곽은 반시계(+9)", signed_area(outer) == 9.0, str(signed_area(outer)))
    check("구멍은 시계(-1)", signed_area(hole) == -1.0, str(signed_area(hole)))
    check("구멍이 외곽 내부에 있음", point_in_ring(hole[0], outer), "")

    print()
    print("구멍 배정 (assign_holes)")
    big = _square(0, 0, 10)
    small = _square(20, 0, 4)
    hole_in_small = list(reversed(_square(21, 1)))  # 시계 = 구멍
    polygons = assign_holes([big, small, hole_in_small])
    check("외곽 2개 → 폴리곤 2개", len(polygons) == 2, str(len(polygons)))
    with_hole = [p for p in polygons if len(p) == 2]
    check("구멍은 가장 작은 포함 외곽에 붙음",
          len(with_hole) == 1 and abs(signed_area(with_hole[0][0])) == 16.0,
          str([abs(signed_area(p[0])) for p in polygons]))
    orphan = assign_holes([big, list(reversed(_square(50, 50)))])
    check("포함 외곽이 없는 구멍은 방향을 뒤집어 독립 폴리곤",
          len(orphan) == 2 and all(signed_area(p[0]) > 0 for p in orphan),
          str([signed_area(p[0]) for p in orphan]))

    print()
    print("단순화 (simplify_ring)")
    dense = [
        (0, 0), (0.5, 0), (1, 0), (1, 0.5), (1, 1), (0.5, 1), (0, 1), (0, 0.5)
    ]
    simplified = simplify_ring(dense, 0.01)
    check("변 위의 점 제거", len(simplified) < len(dense),
          f"{len(dense)} → {len(simplified)}")
    check("꼭짓점 4개는 유지",
          all(corner in simplified for corner in _square(0, 0)), str(simplified))
    check("면적 보존", abs(signed_area(simplified)) == 1.0,
          str(signed_area(simplified)))
    check("열린 링 유지 (시작=끝 중복 없음)",
          simplified[0] != simplified[-1], str(simplified))

    spike = [(0, 0), (2, 0), (2, 2), (1, 0.5), (0, 2)]
    kept = simplify_ring(spike, 0.01)
    check("허용 오차보다 큰 요철은 유지", (1, 0.5) in kept, str(kept))

    check("허용 오차 0 은 원본 그대로", simplify_ring(dense, 0.0) is dense)
    check("정점 4개 이하는 원본 그대로",
          simplify_ring(_square(0, 0), 5.0) == _square(0, 0), "")
    thin = [(0, 0), (1, 0.001), (2, 0.002), (3, 0.001), (4, 0)]
    check("붕괴하면(정점<4) 원본 유지 — 소면적 자치구 소실 방지",
          simplify_ring(thin, 1.0) == thin, str(simplify_ring(thin, 1.0)))

    print()
    print("GeoJSON 좌표 변환")
    coordinates = to_geojson_coordinates([[_square(0, 0)]])
    check("링이 닫힘 (첫 점 = 끝 점)",
          coordinates[0][0][0] == coordinates[0][0][-1], str(coordinates))
    check("정점 수 = 열린 링 + 1", len(coordinates[0][0]) == 5, "")

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
