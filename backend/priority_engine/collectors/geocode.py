"""침하 사고의 지번 주소를 카카오 Local 주소검색으로 행정동에 잇습니다.

    python3 -m collectors.geocode                    # 자체 검증 (네트워크 불필요)
    KAKAO_REST_KEY=<REST키> python3 -m collectors.geocode --build

인증키는 --key 인자나 KAKAO_REST_KEY 환경 변수로만 — 코드·캐시·산출물 어디에도
저장하지 않습니다. 결과 캐시는 키 없는 파생 데이터라 커밋합니다.
왜 지오코딩인지, h_code 대신 이름 매칭인지는 docs/priority/02-데이터.md 참고.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402

ENDPOINTS = {
    "address": "https://dapi.kakao.com/v2/local/search/address.json",
    "keyword": "https://dapi.kakao.com/v2/local/search/keyword.json",
    "region": "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
}
TIMEOUT = 10
# 무료 한도가 일 10만 건이라 여유가 크지만, 예의상 초당 ~30건으로 던집니다.
THROTTLE = 0.03

# addr 맨 앞의 지번 (예: '147-2앞 (봉은사로 86길)' → '147-2')
JIBUN_PATTERN = re.compile(r"\s*(\d+(?:-\d+)?)")
# addr 괄호 안의 도로명 (예: '(백제고분로 272)')
PAREN_PATTERN = re.compile(r"\(([^)]*)\)")

# 키워드 검색을 막는 군더더기. '석정로 간석역북광장 사거리' 처럼 위치 서술이
# 붙으면 장소명 검색이 0건이 되는데, 떼면 '간석역북광장' 이 잡힙니다.
NOISE_PATTERN = re.compile(
    r"(일원|번지|인근|부근|사거리|교차로|입구|지점|앞쪽|앞|옆|내)"
)
TRAILING_JIBUN_PATTERN = re.compile(r"\d+(-\d+)?\s*$")


def build_queries(record: dict) -> list[str]:
    """한 사고 레코드에서 시도할 검색어를 성공 확률 순으로 만듭니다.

    주소가 지저분합니다 — '147-2앞 (봉은사로 86길)', ' 732-1 7(보라매로 21)'.
    원문 그대로 → 지번만 추려서 → 괄호 안 도로명 → 동 이름만 순서로
    시도하면 대부분 어느 단계에서 잡힙니다 (송파구 실측 94%).
    """
    sido = (record.get("sido") or "").strip()
    sgg = (record.get("sigungu") or "").strip()
    dong = (record.get("dong") or "").strip()
    addr = (record.get("addr") or "").strip()
    base = f"{sido} {sgg}".strip()
    if not base:
        return []

    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split())
        if cleaned and cleaned != base and cleaned not in queries:
            queries.append(cleaned)

    if dong and addr:
        add(f"{base} {dong} {addr}")

    jibun = JIBUN_PATTERN.match(addr)
    if dong and jibun:
        add(f"{base} {dong} {jibun.group(1)}")

    paren = PAREN_PATTERN.search(addr)
    if paren:
        add(f"{base} {paren.group(1)}")

    if not dong and addr:
        add(f"{base} {addr}")

    # 동 이름만으로도 대표 지점이 나옵니다. 정밀도는 낮지만 없는 것보다 낫고,
    # 마지막 수단이므로 맨 뒤에 둡니다.
    if dong:
        add(f"{base} {dong}")

    return queries


def strip_noise(addr: str) -> str:
    """장소명 검색용으로 주소에서 위치 서술과 지번을 떼어냅니다."""
    text = re.sub(r"\([^)]*\)", " ", addr)
    text = NOISE_PATTERN.sub(" ", text)
    text = TRAILING_JIBUN_PATTERN.sub(" ", text)
    return " ".join(text.split())


def build_keyword_queries(record: dict) -> list[str]:
    """장소명(랜드마크) 검색 후보. 주소검색이 전부 실패한 뒤에만 씁니다.

    '까치울사거리역', '수원시청역 사거리 교차로 내' 처럼 주소가 아니라 장소를
    가리키는 건이 있어, 이름으로 찾아 좌표를 얻습니다.
    """
    sido = (record.get("sido") or "").strip()
    sgg = (record.get("sigungu") or "").strip()
    landmark = strip_noise((record.get("addr") or "").strip())
    if len(landmark) < 2:
        return []

    queries: list[str] = []
    for prefix in (f"{sido} {sgg}", sgg, ""):
        query = " ".join(f"{prefix} {landmark}".split())
        if query and query not in queries:
            queries.append(query)
    return queries


def parse_documents(payload: dict) -> dict | None:
    """검색 응답에서 행정동 이름과 좌표를 뽑습니다. 문서가 없으면 None.

    행정동 이름은 지번 주소(`address`)에만 실려 옵니다. 도로명주소나 장소로
    잡히면 `h_name` 은 None 이지만 좌표는 오므로, 호출자가 좌표로 행정동을
    다시 물어볼 수 있게 좌표만이라도 돌려줍니다.
    """
    for document in payload.get("documents", []):
        address = document.get("address") or {}
        h_name = (address.get("region_3depth_h_name") or "").strip()
        lng = address.get("x") or document.get("x")
        lat = address.get("y") or document.get("y")
        if h_name or (lng and lat):
            return {
                "h_name": h_name or None,
                "lng": float(lng) if lng else None,
                "lat": float(lat) if lat else None,
            }
    return None


def parse_region(payload: dict) -> str | None:
    """좌표→행정구역 응답에서 **행정동**(region_type 'H') 이름을 뽑습니다.

    같은 응답에 법정동('B')도 함께 오므로 타입을 반드시 봐야 합니다 — 안 보면
    법정동을 행정동으로 착각해 매칭이 다시 어긋납니다.
    """
    for document in payload.get("documents", []):
        if document.get("region_type") == "H":
            name = (document.get("region_3depth_name") or "").strip()
            if name:
                return name
    return None


def kakao_fetch(key: str, kind: str, **params) -> dict:
    url = ENDPOINTS[kind] + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:200]
        if error.code in (401, 403):
            raise SystemExit(
                f"카카오 인증 실패 (HTTP {error.code}): {body}\n"
                "  REST API 키가 맞는지, 콘솔에서 카카오맵 활성화가 켜져 있는지 확인하세요."
            ) from error
        return {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}


def geocode_record(record: dict, fetch) -> dict | None:
    """행정동이 잡힐 때까지 세 경로를 순서대로 시도합니다.

    ① 주소검색 → 지번이 잡히면 행정동이 응답에 들어 있습니다
    ② 주소검색이 도로명만 잡으면 그 좌표로 행정동을 조회
    ③ 그래도 없으면 장소명 검색 → 좌표 → 행정동

    fetch 는 `fetch(kind, **params)` 로, kind 는 'address'·'keyword'·'region'
    입니다. 실전에서는 kakao_fetch 를 부분적용하고, 자체 검증에서는 가짜를
    넘깁니다 — 네트워크 없이 검증됩니다.
    """

    def resolve(kind: str, queries: list[str]) -> dict | None:
        for query in queries:
            found = parse_documents(fetch(kind, query=query))
            if not found:
                continue
            if found["h_name"]:
                return dict(found, query=query, via=kind)
            if found["lng"] is not None and found["lat"] is not None:
                h_name = parse_region(
                    fetch("region", x=found["lng"], y=found["lat"])
                )
                if h_name:
                    return dict(found, h_name=h_name, query=query, via=f"{kind}+좌표")
        return None

    return resolve("address", build_queries(record)) or resolve(
        "keyword", build_keyword_queries(record)
    )


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="침하 사고 주소를 지오코딩해 행정동을 확정합니다."
    )
    parser.add_argument("--build", action="store_true", help="실제 지오코딩 실행")
    parser.add_argument(
        "--key",
        default=os.environ.get("KAKAO_REST_KEY", ""),
        help="카카오 REST API 키 (기본: KAKAO_REST_KEY 환경 변수)",
    )
    parser.add_argument("--cache", type=Path, default=paths.SUBSIDENCE_GEOCODED)
    parser.add_argument("--source", type=Path, default=paths.SUBSIDENCE_CACHE)
    parser.add_argument("--throttle", type=float, default=THROTTLE)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="캐시에 실패로 남은 건을 다시 시도합니다 (검색 경로를 늘렸을 때)",
    )
    args = parser.parse_args()

    if not args.build:
        parser.error("실제 지오코딩은 --build 를 명시해야 합니다 (인자 없이 실행 = 자체 검증)")
    if not args.key:
        raise SystemExit(
            "인증키가 없습니다. KAKAO_REST_KEY 환경 변수나 --key 로 넘기세요.\n"
            "  받는 방법: ~/docs/priority/02-데이터.md"
        )
    if not args.source.exists():
        raise SystemExit(f"침하 상세 캐시가 없습니다: {args.source}")

    source = json.loads(args.source.read_text(encoding="utf-8"))
    cache = load_cache(args.cache)
    print(f"사고 {len(source)}건 · 기존 지오코딩 캐시 {len(cache)}건")

    def fetch(kind: str, **params) -> dict:
        return kakao_fetch(args.key, kind, **params)

    done = failed = 0
    try:
        for sago_no, record in source.items():
            cached = cache.get(sago_no)
            if cached is not None and not (
                args.retry_failed and not cached.get("h_name")
            ):
                continue
            found = geocode_record({"sagoNo": sago_no, **record}, fetch)
            # 실패도 기록합니다 — 안 남기면 재실행마다 같은 주소를 다시 던집니다.
            cache[sago_no] = found or {"h_name": None}
            done += 1
            if found is None:
                failed += 1
            if done % 100 == 0:
                save_cache(args.cache, cache)
                print(f"  {done}건 처리 (실패 {failed})")
            time.sleep(args.throttle)
    finally:
        save_cache(args.cache, cache)

    matched = sum(1 for value in cache.values() if value.get("h_name"))
    print(f"→ {args.cache} (행정동 확정 {matched}/{len(cache)}건, {matched / len(cache):.0%})")
    print()
    print("  다음: python3 -m collectors.subsidence --cache-only   ← 건별 레코드 재생성")


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

    print("검색어 생성")
    record = {
        "sido": "서울특별시",
        "sigungu": "강남구",
        "dong": "삼성동",
        "addr": "147-2앞 (봉은사로 86길)",
    }
    queries = build_queries(record)
    check("원문 그대로가 첫 후보", queries[0] == "서울특별시 강남구 삼성동 147-2앞 (봉은사로 86길)", str(queries))
    check("지번만 추린 후보 포함", "서울특별시 강남구 삼성동 147-2" in queries, str(queries))
    check("괄호 안 도로명 후보 포함", "서울특별시 강남구 봉은사로 86길" in queries, str(queries))
    check("동 이름만 후보가 마지막", queries[-1] == "서울특별시 강남구 삼성동", str(queries))

    no_dong = build_queries({"sido": "서울특별시", "sigungu": "중구", "dong": "", "addr": "퇴계로 196"})
    check("동이 없으면 주소로 검색", "서울특별시 중구 퇴계로 196" in no_dong, str(no_dong))
    check("빈 레코드는 빈 목록", build_queries({}) == [])
    check(
        "중복 후보 제거",
        len(build_queries({"sido": "서울", "sigungu": "중구", "dong": "명동", "addr": "명동"}))
        == len(set(build_queries({"sido": "서울", "sigungu": "중구", "dong": "명동", "addr": "명동"}))),
    )

    print()
    print("장소명 검색어 생성")
    landmark = {"sido": "인천광역시", "sigungu": "남동구", "dong": "", "addr": "석정로 간석역북광장 사거리"}
    check("위치 서술 제거", strip_noise("석정로 간석역북광장 사거리") == "석정로 간석역북광장",
          strip_noise("석정로 간석역북광장 사거리"))
    check("괄호와 끝 지번 제거", strip_noise("450-3번지 (동탄택지)") == "", strip_noise("450-3번지 (동탄택지)"))
    kq = build_keyword_queries(landmark)
    check("시군구만 붙인 후보 포함", "남동구 석정로 간석역북광장" in kq, str(kq))
    check("접두어 없는 후보 포함", "석정로 간석역북광장" in kq, str(kq))
    check("장소명이 남지 않으면 빈 목록", build_keyword_queries({"addr": "12-3번지"}) == [])

    print()
    print("응답 파싱")
    payload = {
        "documents": [
            {"address": {"region_3depth_h_name": "풍납1동", "x": "127.11", "y": "37.53"}}
        ]
    }
    parsed = parse_documents(payload)
    check("행정동 이름", parsed["h_name"] == "풍납1동", str(parsed))
    check("좌표 float 변환", parsed["lng"] == 127.11 and parsed["lat"] == 37.53, str(parsed))
    check("결과 없으면 None", parse_documents({"documents": []}) is None)
    # 도로명·장소만 잡히면 행정동은 없지만 좌표는 옵니다 — 좌표라도 살려야
    # 다음 단계(좌표→행정동)로 넘어갈 수 있습니다.
    road_only = parse_documents({"documents": [{"address": None, "x": "129.07", "y": "35.18"}]})
    check("도로명만 잡히면 좌표만 반환", road_only == {"h_name": None, "lng": 129.07, "lat": 35.18},
          str(road_only))
    check(
        "행정동·좌표 모두 없으면 None",
        parse_documents({"documents": [{"address": {"region_3depth_h_name": ""}}]}) is None,
    )

    print()
    print("좌표 → 행정구역 파싱")
    region_payload = {
        "documents": [
            {"region_type": "B", "region_3depth_name": "거제동"},
            {"region_type": "H", "region_3depth_name": "거제2동"},
        ]
    }
    check("법정동(B) 아닌 행정동(H) 선택", parse_region(region_payload) == "거제2동",
          str(parse_region(region_payload)))
    check("H 가 없으면 None", parse_region({"documents": [{"region_type": "B",
                                                          "region_3depth_name": "거제동"}]}) is None)
    check("빈 응답은 None", parse_region({}) is None)

    print()
    print("지오코딩 (가짜 fetch — 네트워크 없음)")
    calls: list[tuple[str, str]] = []

    def fake_fetch(kind: str, **params) -> dict:
        calls.append((kind, params.get("query") or f"{params.get('x')},{params.get('y')}"))
        # 지번만 추린 두 번째 후보에서 성공하는 시나리오
        if kind == "address" and params.get("query") == "서울특별시 강남구 삼성동 147-2":
            return payload
        return {"documents": []}

    found = geocode_record(record, fake_fetch)
    check("후보를 순서대로 시도해 성공", found and found["h_name"] == "풍납1동", str(found))
    check("성공한 검색어 기록", found["query"] == "서울특별시 강남구 삼성동 147-2", str(found))
    check("성공 즉시 중단 (불필요한 호출 없음)", calls[-1][1] == found["query"], str(calls))
    check("경로 기록", found["via"] == "address", str(found))
    check("전부 실패하면 None", geocode_record(record, lambda kind, **p: {}) is None)

    # ② 주소검색이 도로명만 잡는 경로: 좌표로 행정동을 다시 물어봐야 합니다.
    def road_fetch(kind: str, **params) -> dict:
        if kind == "address":
            return {"documents": [{"address": None, "x": "129.07", "y": "35.18"}]}
        if kind == "region":
            return region_payload
        return {"documents": []}

    road = geocode_record(record, road_fetch)
    check("도로명 → 좌표 → 행정동", road and road["h_name"] == "거제2동", str(road))
    check("좌표 경로임이 기록됨", road["via"] == "address+좌표", str(road))

    # ③ 주소검색이 전부 실패하면 장소명 검색으로 넘어갑니다.
    def keyword_fetch(kind: str, **params) -> dict:
        if kind == "keyword":
            return {"documents": [{"x": "126.69", "y": "37.46"}]}
        if kind == "region":
            return {"documents": [{"region_type": "H", "region_3depth_name": "간석4동"}]}
        return {"documents": []}

    kw = geocode_record(landmark, keyword_fetch)
    check("장소명 → 좌표 → 행정동", kw and kw["h_name"] == "간석4동", str(kw))
    check("장소명 경로임이 기록됨", kw["via"] == "keyword+좌표", str(kw))

    # 주소검색으로 잡히면 장소명 검색은 부르지 않아야 합니다 (호출 낭비 방지).
    kinds: list[str] = []

    def counting_fetch(kind: str, **params) -> dict:
        kinds.append(kind)
        if kind == "address" and params.get("query") == "서울특별시 강남구 삼성동 147-2":
            return payload
        return {"documents": []}

    geocode_record(record, counting_fetch)
    check("주소로 잡히면 장소명 검색 안 함", "keyword" not in kinds, str(kinds))

    print()
    print("캐시 저장·복원")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cache.json"
        save_cache(path, {"20180001": {"h_name": "풍납1동"}, "20180002": {"h_name": None}})
        restored = load_cache(path)
        check("라운드트립 보존", restored["20180001"]["h_name"] == "풍납1동", str(restored))
        check("실패 기록도 보존 (재시도 방지)", restored["20180002"]["h_name"] is None)
        check("없는 파일은 빈 캐시", load_cache(Path(tmp) / "none.json") == {})

    dumped = json.dumps({"a": {"h_name": "x"}})
    check("캐시에 인증키가 들어갈 자리가 없음", "KakaoAK" not in dumped and "key" not in dumped)

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
