from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paths  # noqa: E402
from collectors import subsidence_spec as spec  # noqa: E402
from mapping.sgg_normalize import SggResolver, diff_codes  # noqa: E402

TIMEOUT = 20
RETRIES = 3
PAGE_SIZE = 100

# 조회 시작 연도. 지하안전정보 침하 이력은 2018년부터 집계됩니다.
DEFAULT_FROM_YEAR = 2018
DEFAULT_TO_YEAR = 2025

# 연속 호출 간 최소 간격(초). 30 tps 제한이 있으나 여유를 둡니다.
THROTTLE = 0.05


def pick(record: dict, keys: tuple[str, ...]) -> str:
    """후보 키를 순서대로 보고 처음 값이 있는 것을 돌려줍니다.

    응답 표기가 가이드와 다르고(siDo/sido) 리스트와 상세도 다릅니다
    (sagoReason/sagoDetail). 어느 쪽이 와도 읽히게 하려는 것입니다.
    """
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


# ---------------------------------------------------------------- 원인 판정

CAUSE_SEWER = "sewer"
CAUSE_WATER = "water"
CAUSE_BOTH = "both"
CAUSE_UNCONFIRMED = "unconfirmed"
CAUSE_OTHER = "other"


def classify_cause(text: str | None) -> str:
    if not text:
        return CAUSE_UNCONFIRMED

    cleaned = str(text).strip()
    if not cleaned:
        return CAUSE_UNCONFIRMED

    if any(marker in cleaned for marker in spec.UNCONFIRMED_MARKERS):
        return CAUSE_UNCONFIRMED

    has_sewer = any(word in cleaned for word in spec.SEWER_CAUSE_KEYWORDS)
    has_water = any(word in cleaned for word in spec.WATER_CAUSE_KEYWORDS)

    if has_sewer and has_water:
        return CAUSE_BOTH
    if has_sewer:
        return CAUSE_SEWER
    if has_water:
        return CAUSE_WATER
    return CAUSE_OTHER


# ---------------------------------------------------------------- 기간 분할


def split_periods(from_year: int, to_year: int) -> list[tuple[str, str]]:
    if from_year > to_year:
        raise ValueError(f"기간이 거꾸로입니다: {from_year} > {to_year}")
    return [
        (f"{year}0101", f"{year}1231") for year in range(from_year, to_year + 1)
    ]


# ---------------------------------------------------------------- HTTP


class Client:
    def __init__(self, key: str, throttle: float = THROTTLE) -> None:
        self.key = key
        self.throttle = throttle
        self.base: str | None = None
        self.encoded: bool | None = None
        self.format: dict | None = None
        self.calls = 0

    def _request(self, url: str, params: dict, encoded: bool) -> tuple[int, str]:
        query = urllib.parse.urlencode(params)
        # Encoding 키에는 이미 %2B 등이 들어 있어 urlencode를 거치면 %252B가 됩니다.
        # 그래서 serviceKey는 항상 직접 이어 붙입니다.
        service_key = self.key if encoded else urllib.parse.quote(self.key, safe="")
        full = f"{url}?serviceKey={service_key}&{query}"

        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(full, timeout=TIMEOUT) as response:
                    self.calls += 1
                    if self.throttle:
                        time.sleep(self.throttle)
                    return response.status, response.read().decode(
                        "utf-8", errors="replace"
                    )
            except urllib.error.HTTPError as error:
                return error.code, error.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == RETRIES - 1:
                    return 0, f"URLError: {error}"
                time.sleep(1.0 * (attempt + 1))
        return 0, "재시도 소진"

    def discover(self, operation: str, required: dict) -> None:
        if self.base is not None:
            return

        bases = [spec.BASE_URL]
        formats = [{"type": "json"}, {"_type": "json"}, {"dataType": "JSON"}, {}]

        for base in bases:
            for fmt in formats:
                for encoded in (True, False):
                    params = {**required, **fmt, "pageNo": 1, "numOfRows": 3}
                    status, body = self._request(
                        f"{base}/{operation}", params, encoded
                    )
                    if status != 200 or failure_reason(body):
                        continue
                    if not parse_items(body):
                        continue
                    self.base, self.encoded, self.format = base, encoded, fmt
                    org = base.split("/")[3]
                    kind = "Encoding" if encoded else "Decoding"
                    print(
                        f"  [OK] 동작 조합: 기관 {org} · {kind} 키 · "
                        f"형식 {fmt or 'XML'}"
                    )
                    return

        raise SystemExit(
            "동작하는 조합을 찾지 못했습니다.\n"
            "  활용신청 승인 여부(data.go.kr 마이페이지)를 먼저 보세요.\n"
            "  키는 Encoding 형식을 쓰고, 스킴은 https 여야 합니다."
        )

    def fetch_page(self, operation: str, params: dict) -> tuple[list[dict], int]:
        merged = {**params, **(self.format or {})}
        status, body = self._request(
            f"{self.base}/{operation}", merged, bool(self.encoded)
        )
        reason = failure_reason(body)
        if status != 200 or reason:
            raise SystemExit(
                f"{operation} 실패: HTTP {status} {reason or body[:200]}\n"
                f"  파라미터: {merged}"
            )
        return parse_items(body), parse_total(body)

    def fetch_all(self, operation: str, params: dict) -> list[dict]:
        page = 1
        collected: list[dict] = []
        while True:
            items, total = self.fetch_page(
                operation, {**params, "pageNo": page, "numOfRows": PAGE_SIZE}
            )
            collected.extend(items)
            if not items or len(collected) >= total or len(items) < PAGE_SIZE:
                return collected
            page += 1
            if page > 200:  # 무한 루프 방지
                print(f"    [!] {operation} 페이지 200 초과: 중단")
                return collected


# ---------------------------------------------------------------- 응답 파싱


def failure_reason(body: str) -> str | None:
    markers = {
        "SERVICE_KEY_IS_NOT_REGISTERED": "인증키 미등록: 활용신청 승인 대기 또는 키 형식 불일치",
        "SERVICE KEY IS NOT REGISTERED": "인증키 미등록",
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS": "일일 트래픽 초과",
        "APPLICATION_ERROR": "서비스 내부 오류",
        "NO_OPENAPI_SERVICE_ERROR": "해당 오퍼레이션 없음",
        "HTTP ROUTING ERROR": "엔드포인트 경로 오류",
        "API not found": "엔드포인트 없음",
        "Unauthorized": "인증 실패",
        "Unexpected errors": "서버 오류",
    }
    for marker, meaning in markers.items():
        if marker in body:
            return meaning
    return None


def parse_items(body: str) -> list[dict]:
    text = body.strip()
    if not text:
        return []

    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        return _json_items(payload)

    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        items = []
        for node in root.iter():
            if not node.tag.endswith("item"):
                continue
            record = {child.tag: (child.text or "").strip() for child in node}
            if record:
                items.append(record)
        return items

    return []


def _json_items(payload, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "item", "body", "response", "data", "result"):
            if key in payload:
                found = _json_items(payload[key], depth + 1)
                if found:
                    return found
        # 스칼라만 있는 dict면 그 자체가 레코드입니다.
        if any(not isinstance(value, (dict, list)) for value in payload.values()):
            return [payload]
    return []


def parse_total(body: str) -> int:
    text = body.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return 0
        return _find_total(payload)
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return 0
        for node in root.iter():
            if node.tag.endswith("totalCount") and node.text:
                try:
                    return int(node.text.strip())
                except ValueError:
                    return 0
    return 0


def _find_total(payload, depth: int = 0) -> int:
    if depth > 8 or not isinstance(payload, dict):
        return 0
    for key, value in payload.items():
        if key == "totalCount":
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    for value in payload.values():
        if isinstance(value, dict):
            found = _find_total(value, depth + 1)
            if found:
                return found
    return 0


# ---------------------------------------------------------------- 집계


def aggregate(
    records: list[dict],
    resolver: SggResolver,
    lengths: dict[str, float] | None = None,
) -> tuple[list[dict], Counter]:
    counters: Counter = Counter()
    buckets: dict[str, dict] = {}

    for record in records:
        sido = pick(record, spec.SIDO_KEYS)
        sgg = pick(record, spec.SIGUNGU_KEYS)
        cause = classify_cause(pick(record, spec.CAUSE_KEYS))
        counters[f"cause_{cause}"] += 1

        code = resolver.resolve(sido, sgg) if sido else None
        if code is None:
            counters["unresolved"] += 1
            continue
        counters["resolved"] += 1

        bucket = buckets.setdefault(
            code,
            {
                "sgg_code": code,
                "full_name": resolver.name_of(code) or f"{sido} {sgg}".strip(),
                "subsidence_count": 0,
                "sewer_cause_count": 0,
                "water_cause_count": 0,
                "unconfirmed_count": 0,
                "other_cause_count": 0,
                "both_cause_count": 0,
                "death_count": 0,
                "injury_count": 0,
                "dongs": {},
                "years": {},
            },
        )
        bucket["subsidence_count"] += 1
        bucket[
            {
                CAUSE_SEWER: "sewer_cause_count",
                CAUSE_WATER: "water_cause_count",
                CAUSE_UNCONFIRMED: "unconfirmed_count",
                CAUSE_BOTH: "both_cause_count",
                CAUSE_OTHER: "other_cause_count",
            }[cause]
        ] += 1

        bucket["death_count"] += int(to_int(record.get("deathCnt")))
        bucket["injury_count"] += int(to_int(record.get("injuryCnt")))

        dong = (record.get("dong") or "").strip()
        if dong and cause == CAUSE_SEWER:
            bucket["dongs"][dong] = bucket["dongs"].get(dong, 0) + 1

        date = (record.get("sagoDate") or "").strip()
        if len(date) >= 4 and date[:4].isdigit():
            year = date[:4]
            bucket["years"][year] = bucket["years"].get(year, 0) + 1

    rows = []
    for code, bucket in sorted(buckets.items()):
        length = (lengths or {}).get(code)
        per_km = None
        if length and length > 0:
            per_km = round(bucket["sewer_cause_count"] / (length / 1000.0), 8)

        # 확인중이 많으면 하수 건수를 과소평가한 것입니다. 그 사실을 드러냅니다.
        total = bucket["subsidence_count"]
        bucket["unconfirmed_share"] = (
            round(bucket["unconfirmed_count"] / total, 4) if total else None
        )
        # 참고용입니다. 산정에 쓰는 km당 발생률(subsidence_per_km)은 dataset.py
        # 가 병합 시점의 연장으로 다시 계산합니다 — 여기 값은 수집 시점 연장
        # 기준이라 두 파일이 어긋날 수 있고, 정본은 dataset 쪽입니다.
        bucket["sewer_subsidence_per_km"] = per_km
        bucket["pipe_total_length_m"] = length
        rows.append(bucket)

    return rows, counters


def squash_dong(name: str) -> str:
    """행정동 이름의 구분자를 지웁니다.

    통합 행정동을 카카오는 '운천.신봉동', 경계 원본은 '운천·신봉동' 으로 적어
    글자 그대로 비교하면 어긋납니다. 청주 등 27건이 이 차이로 안 붙었습니다.
    """
    return re.sub(r"[.·・,\s]+", "", name or "")


def match_adm_code(dong: str, candidates: list[tuple[str, str]]) -> str | None:
    """법정동 이름을 행정동 코드에 잇습니다. 확실할 때만 잇습니다.

    침하 상세의 dong 은 법정동(삼성동)이고 admin_dongs 는 행정동이라 이름이
    어긋납니다(삼성1동·삼성2동). 정확 일치가 있으면 그것을 쓰고, 없으면
    '<어간><숫자>동' 꼴 후보가 **유일할 때만** 잇습니다. 후보가 여럿이면 어느
    행정동인지 알 수 없으므로 잇지 않습니다 — 틀린 동에 사고를 붙이는 것보다
    시군구까지만 아는 편이 낫습니다.
    """
    if not dong:
        return None

    exact = [code for name, code in candidates if name == dong]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    stem = dong[:-1] if dong.endswith("동") else dong
    if not stem:
        return None
    variants = [
        code
        for name, code in candidates
        if name.startswith(stem)
        and name.endswith("동")
        and name[len(stem):-1].isdigit()
    ]
    return variants[0] if len(variants) == 1 else None


def incident_rows(
    records: list[dict],
    resolver: SggResolver,
    dong_master: list[dict] | None = None,
    geocoded: dict[str, dict] | None = None,
) -> list[dict]:
    """API 적재용 건별 레코드.

    집계(aggregate)와 같은 원인 분류·시군구 매칭을 쓰되 건별로 남깁니다.
    cloud_api 가 읍면동 뷰의 침하 카운트를 이 파일에서 적재합니다 — 분류
    로직을 API 쪽에 중복 구현하지 않기 위한 산출물입니다.

    행정동 판정은 두 단계입니다:
      1. 지오코딩 결과(geocoded, collectors/geocode.py)가 있으면 그 행정동
         이름을 우리 마스터에서 찾습니다. 지번 기반이라 가장 정확합니다.
      2. 없으면 법정동 이름 매칭(match_adm_code)으로 폴백합니다 — 여러
         행정동에 걸치면 잇지 않는 보수적 방식입니다.
    시군구는 항상 원본의 시도·시군구 이름으로 정합니다. 지오코딩이 다른
    시군구의 동을 돌려줘도(경계 오차) 그 동이 이 시군구 후보에 없으면
    잇지 않으므로, 사고가 다른 구로 새지 않습니다.
    """
    dongs_by_sgg: dict[str, list[tuple[str, str]]] = {}
    for row in dong_master or []:
        sgg = row.get("sgg_code")
        name = row.get("adm_name")
        code = row.get("adm_code")
        if sgg and name and code:
            # adm_name 은 "서울특별시 종로구 청운효자동" 전체 경로명입니다.
            # 침하 상세의 dong 은 동 이름만 오므로 마지막 토큰으로 맞춥니다.
            dongs_by_sgg.setdefault(sgg, []).append((name.split()[-1], code))

    rows = []
    for record in records:
        sido = pick(record, spec.SIDO_KEYS)
        sgg = pick(record, spec.SIGUNGU_KEYS)
        code = resolver.resolve(sido, sgg) if sido else None
        if code is None:
            continue

        cause = classify_cause(pick(record, spec.CAUSE_KEYS))
        dong = (record.get("dong") or "").strip()
        date = (record.get("sagoDate") or "").strip()
        sago_no = pick(record, ("sagoNo", "no"))
        candidates = dongs_by_sgg.get(code, [])

        geo = (geocoded or {}).get(sago_no) or {}
        adm_code = None
        if geo.get("h_name"):
            target = squash_dong(geo["h_name"])
            exact = [c for name, c in candidates if squash_dong(name) == target]
            adm_code = exact[0] if len(exact) == 1 else None
        if adm_code is None:
            adm_code = match_adm_code(dong, candidates)

        rows.append(
            {
                "sago_no": sago_no,
                "sgg_code": code,
                "adm_code": adm_code,
                "dong": dong or None,
                "cause": cause,
                "cause_is_sewer": cause == CAUSE_SEWER,
                "sago_date": date or None,
                # 지오코딩 좌표 — 지도에 사고 지점을 찍을 때 씁니다.
                "lat": geo.get("lat"),
                "lng": geo.get("lng"),
            }
        )
    return rows


def to_int(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


# ---------------------------------------------------------------- 수집 실행


def collect(
    client: Client,
    from_year: int,
    to_year: int,
    with_detail: bool,
    cache: dict,
) -> list[dict]:
    periods = split_periods(from_year, to_year)
    client.discover(
        spec.SUBSIDENCE_LIST,
        {"sagoDateFrom": periods[0][0], "sagoDateTo": periods[-1][1]},
    )

    listed: list[dict] = []
    for start, end in periods:
        items = client.fetch_all(
            spec.SUBSIDENCE_LIST, {"sagoDateFrom": start, "sagoDateTo": end}
        )
        print(f"    {start[:4]}년  {len(items):4d}건")
        listed.extend(items)

    if not with_detail:
        return listed

    # 상세 조회는 N+1입니다. 읍면동과 정확한 원인이 여기에만 있습니다.
    print()
    print(f"  상세 조회 {len(listed)}건 (캐시 {len(cache)}건 보유)")
    detailed: list[dict] = []
    fetched = 0

    for index, row in enumerate(listed, start=1):
        sago_no = str(row.get("sagoNo") or "").strip()
        if not sago_no:
            detailed.append(row)
            continue

        if sago_no in cache:
            detailed.append({**row, **cache[sago_no]})
            continue

        items = client.fetch_all(spec.SUBSIDENCE_INFO, {"sagoNo": sago_no})
        detail = items[0] if items else {}
        cache[sago_no] = detail
        detailed.append({**row, **detail})
        fetched += 1

        if index % 100 == 0:
            print(f"    {index}/{len(listed)}  (신규 {fetched}건)")

    print(f"  상세 완료: 신규 {fetched}건, 캐시 재사용 {len(listed) - fetched}건")
    return detailed


def fetch_priority_targets(client: Client, from_year: int, to_year: int) -> list[dict]:
    try:
        client.discover(
            spec.PRIORITY_TARGET_LIST,
            {"heavyYmdFrom": f"{from_year}0101", "heavyYmdto": f"{to_year}1231"},
        )
        return client.fetch_all(
            spec.PRIORITY_TARGET_LIST,
            {"heavyYmdFrom": f"{from_year}0101", "heavyYmdto": f"{to_year}1231"},
        )
    except SystemExit as error:
        print(f"  [!] 중점관리대상 실패: 건너뜁니다: {error}")
        return []


def match_targets(
    targets: list[dict], resolver: SggResolver, master: list[dict]
) -> set[str]:
    by_name: dict[str, str] = {}
    for row in master:
        full = row.get("full_name") or ""
        parts = full.split()
        if len(parts) >= 2:
            # '광주광역시 서구' → '서구'는 동명이 많아 단독으로는 못 씁니다.
            by_name[full] = row["sgg_code"]

    matched: set[str] = set()
    for target in targets:
        name = pick(target, spec.FACILITY_NAME_KEYS)
        for full, code in by_name.items():
            sido, sgg = full.split()[0], full.split()[-1]
            if sido[:2] in name and sgg in name:
                matched.add(code)
                break
    return matched


# ---------------------------------------------------------------- 보고


def report(rows: list[dict], counters: Counter, master: set[str]) -> None:
    print()
    print("=" * 72)
    print("원인 분류")
    print("=" * 72)
    total = sum(value for key, value in counters.items() if key.startswith("cause_"))
    labels = {
        f"cause_{CAUSE_SEWER}": "하수관 원인          ← 우리 지표",
        f"cause_{CAUSE_WATER}": "상수관 원인          (대상 아님)",
        f"cause_{CAUSE_BOTH}": "하수·상수 둘 다 언급  (하수로 세지 않음)",
        f"cause_{CAUSE_UNCONFIRMED}": "확인중·미기재        (결측)",
        f"cause_{CAUSE_OTHER}": "그 외",
    }
    for key, label in labels.items():
        count = counters.get(key, 0)
        share = count / total if total else 0.0
        print(f"  {label:34} {count:5d}건 ({share:5.1%})")

    unconfirmed = counters.get(f"cause_{CAUSE_UNCONFIRMED}", 0)
    if total and unconfirmed / total > 0.2:
        print()
        print(f"  [!] 확인중이 {unconfirmed / total:.0%}입니다. 하수관 건수는 하한이며,")
        print("     조사가 느린 지자체가 자동으로 안전해 보이는 편향이 있습니다.")

    print()
    print("=" * 72)
    print("시군구 조인")
    print("=" * 72)
    print(f"  해석 성공 {counters.get('resolved', 0):5d}건")
    print(f"  해석 실패 {counters.get('unresolved', 0):5d}건")
    codes = {row["sgg_code"] for row in rows}
    only_data, only_map = diff_codes(codes, master)
    if only_data:
        print(f"  [!] 마스터에 없는 코드 {len(only_data)}개: {sorted(only_data)[:10]}")
    print(f"  침하 이력이 있는 시군구 {len(codes)}개 / 전국 {len(master)}개")
    print(f"  (이력이 없는 {len(only_map)}개는 발생 0건으로 처리됩니다)")

    with_sewer = [row for row in rows if row["sewer_cause_count"] > 0]
    if not with_sewer:
        print()
        print("  [!] 하수관 원인으로 분류된 사고가 없습니다. 원인 키워드를 확인하세요.")
        return

    print()
    print("=" * 72)
    print("하수관 원인 침하 상위 15곳")
    print("=" * 72)
    print(f"  {'건수':>4} {'km당':>9}  구역")
    for row in sorted(with_sewer, key=lambda r: -r["sewer_cause_count"])[:15]:
        per_km = row["sewer_subsidence_per_km"]
        shown = f"{per_km:9.5f}" if per_km is not None else "      N/A"
        print(f"  {row['sewer_cause_count']:4d} {shown}  {row['full_name']}")

    print()
    print("  ※ 점수에 쓰는 값은 km당 발생률입니다. 원값을 쓰면 점수가 총연장의")
    print("     복사본이 되어 '관로 긴 곳 순위'가 됩니다.")


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    passed = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal passed
        print(f"  [{'OK  ' if condition else 'FAIL'}] {label}"
              + (f"  {detail}" if detail and not condition else ""))
        if condition:
            passed += 1
        else:
            raise AssertionError(label)

    print("원인 분류")
    check("하수관 → sewer", classify_cause("하수관 손상") == CAUSE_SEWER)
    check("오수관 → sewer", classify_cause("오수관 파손으로 인한 공동") == CAUSE_SEWER)
    check("우수관 → sewer", classify_cause("우수관 이음부 이탈") == CAUSE_SEWER)
    check("상수도 파열 → water (가이드 예시값)", classify_cause("상수도 파열") == CAUSE_WATER)
    check("급수관 → water", classify_cause("급수관 누수") == CAUSE_WATER)
    check(
        "둘 다 언급 → both (하수로 세지 않음)",
        classify_cause("상수도 및 하수관 파손") == CAUSE_BOTH,
    )
    check("확인중 → unconfirmed", classify_cause("확인중") == CAUSE_UNCONFIRMED)
    check(
        "확인중이 하수 키워드보다 우선",
        classify_cause("하수관 손상 확인중") == CAUSE_UNCONFIRMED,
    )
    check("빈 문자열 → unconfirmed", classify_cause("") == CAUSE_UNCONFIRMED)
    check("None → unconfirmed", classify_cause(None) == CAUSE_UNCONFIRMED)
    check("공백만 → unconfirmed", classify_cause("   ") == CAUSE_UNCONFIRMED)
    check("굴착 부실 → other", classify_cause("굴착공사 부실") == CAUSE_OTHER)

    print()
    print("기간 분할")
    periods = split_periods(2018, 2025)
    check("8개 연도", len(periods) == 8)
    check("첫 구간이 2018-01-01", periods[0] == ("20180101", "20181231"))
    check("끝 구간이 2025-12-31", periods[-1] == ("20250101", "20251231"))
    check("단일 연도", split_periods(2024, 2024) == [("20240101", "20241231")])
    try:
        split_periods(2025, 2018)
        check("거꾸로면 예외", False)
    except ValueError:
        check("거꾸로면 예외", True)

    print()
    print("응답 파싱")
    xml_body = """<response><header><resultCode>0</resultCode></header>
    <body><totalCount>2</totalCount><items>
    <item><sagoNo>20190001</sagoNo><siDo>서울특별시</siDo><siGunGu>강남구</siGunGu>
    <sagoDetail>하수관 손상</sagoDetail><sagoDate>20190117</sagoDate></item>
    <item><sagoNo>20190002</sagoNo><siDo>서울특별시</siDo><siGunGu>서초구</siGunGu>
    <sagoDetail>상수도 파열</sagoDetail><sagoDate>20190118</sagoDate></item>
    </items></body></response>"""
    items = parse_items(xml_body)
    check("XML 레코드 2건", len(items) == 2)
    check("XML 필드 파싱", items[0]["siGunGu"] == "강남구")
    check("XML totalCount", parse_total(xml_body) == 2)

    json_body = json.dumps(
        {
            "response": {
                "body": {
                    "totalCount": 1,
                    "items": [
                        {
                            "sagoNo": "20240001",
                            "siDo": "광주광역시",
                            "siGunGu": "서구",
                            "sagoDetail": "하수관 손상",
                        }
                    ],
                }
            }
        }
    )
    items = parse_items(json_body)
    check("JSON 레코드 1건", len(items) == 1)
    check("JSON totalCount", parse_total(json_body) == 1)
    check("빈 응답이면 빈 목록", parse_items("") == [])
    check("깨진 JSON이면 빈 목록", parse_items("{oops") == [])

    print()
    print("오류 판별")
    check(
        "인증키 미등록 감지",
        failure_reason("<resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg>")
        is not None,
    )
    check("정상 응답은 None", failure_reason("<resultCode>0</resultCode>") is None)

    print()
    print("집계")
    resolver = SggResolver.from_master(paths.DISTRICTS_MASTER)
    records = [
        {"siDo": "서울특별시", "siGunGu": "강남구", "sagoDetail": "하수관 손상",
         "sagoDate": "20190117", "dong": "역삼동", "deathCnt": "0"},
        {"siDo": "서울특별시", "siGunGu": "강남구", "sagoDetail": "하수관 파손",
         "sagoDate": "20200201", "dong": "역삼동", "deathCnt": "1", "injuryCnt": "2"},
        {"siDo": "서울특별시", "siGunGu": "강남구", "sagoDetail": "상수도 파열",
         "sagoDate": "20210301"},
        {"siDo": "광주광역시", "siGunGu": "서구", "sagoDetail": "확인중",
         "sagoDate": "20220401"},
        {"siDo": "없는시도", "siGunGu": "없는구", "sagoDetail": "하수관 손상"},
    ]
    lengths = {"11680": 1_000_000.0, "29140": 500_000.0}
    rows, counters = aggregate(records, resolver, lengths)
    by_code = {row["sgg_code"]: row for row in rows}

    check("해석 실패 1건 기록", counters["unresolved"] == 1)
    check("강남구 집계됨", "11680" in by_code)
    gangnam = by_code["11680"]
    check("강남구 전체 3건", gangnam["subsidence_count"] == 3)
    check("강남구 하수 원인 2건", gangnam["sewer_cause_count"] == 2)
    check("강남구 상수 원인 1건", gangnam["water_cause_count"] == 1)
    check(
        "km당 = 2 / 1000km = 0.002",
        abs(gangnam["sewer_subsidence_per_km"] - 0.002) < 1e-9,
    )
    check("사망 1명 합산", gangnam["death_count"] == 1)
    check("부상 2명 합산", gangnam["injury_count"] == 2)
    check("읍면동 집계 (하수 원인만)", gangnam["dongs"] == {"역삼동": 2})
    check("연도 집계", set(gangnam["years"]) == {"2019", "2020", "2021"})

    gwangju = by_code["29140"]
    check("확인중은 하수로 세지 않음", gwangju["sewer_cause_count"] == 0)
    check("확인중 건수 기록", gwangju["unconfirmed_count"] == 1)
    check("확인중 비율 1.0", gwangju["unconfirmed_share"] == 1.0)
    check(
        "하수 0건이면 km당 0.0 (None 아님: 관측된 0)",
        gwangju["sewer_subsidence_per_km"] == 0.0,
    )

    no_length, _ = aggregate(records[:1], resolver, {})
    check("연장이 없으면 km당은 None", no_length[0]["sewer_subsidence_per_km"] is None)
    check("빈 입력이면 빈 결과", aggregate([], resolver, {})[0] == [])

    print()
    print("건별 레코드 (API 적재용)")
    dong_master = [
        {"sgg_code": "11680", "adm_code": "1168052000", "adm_name": "역삼1동"},
        {"sgg_code": "11680", "adm_code": "1168053000", "adm_name": "역삼2동"},
        {"sgg_code": "11680", "adm_code": "1168056000", "adm_name": "대치동"},
        {"sgg_code": "29140", "adm_code": "2914059000", "adm_name": "치평동"},
        # 통합 행정동 — 경계 원본은 중점(·), 카카오는 마침표(.)를 씁니다.
        {"sgg_code": "11680", "adm_code": "1168057000", "adm_name": "운천·신봉동"},
    ]
    incidents = incident_rows(records, resolver, dong_master)
    check("해석 실패 건은 제외", len(incidents) == 4, str(len(incidents)))
    check(
        "하수 원인 플래그",
        sum(1 for row in incidents if row["cause_is_sewer"]) == 2,
        str([(row["dong"], row["cause"]) for row in incidents]),
    )
    check(
        "법정동이 행정동 여러 개에 걸리면 잇지 않음 (역삼동 → 역삼1·2동)",
        all(row["adm_code"] is None for row in incidents if row["dong"] == "역삼동"),
        str([row["adm_code"] for row in incidents if row["dong"] == "역삼동"]),
    )

    exact = incident_rows(
        [
            {
                "siDo": "서울특별시",
                "siGunGu": "강남구",
                "sagoDetail": "하수관 손상",
                "dong": "대치동",
            }
        ],
        resolver,
        dong_master,
    )
    check("이름이 정확히 일치하면 잇는다", exact[0]["adm_code"] == "1168056000", str(exact[0]))

    check(
        "match: 어간+숫자 후보가 유일하면 연결",
        match_adm_code("치평동", [("치평1동", "A")]) == "A",
    )
    check(
        "match: 후보 여럿이면 None",
        match_adm_code("삼성동", [("삼성1동", "A"), ("삼성2동", "B")]) is None,
    )
    check("match: 무관한 동은 안 붙음", match_adm_code("중동", [("중앙동", "A")]) is None)
    check("match: 빈 이름은 None", match_adm_code("", [("삼성1동", "A")]) is None)

    # 지오코딩 결과가 있으면 이름 매칭보다 우선합니다. 법정동 역삼동은 역삼1·2동
    # 둘에 걸려 이름만으로는 못 잇지만, 지번을 지오코딩하면 확정됩니다.
    geo_records = [
        {
            "sagoNo": "T1",
            "siDo": "서울특별시",
            "siGunGu": "강남구",
            "sagoDetail": "하수관 손상",
            "dong": "역삼동",
        }
    ]
    with_geo = incident_rows(
        geo_records, resolver, dong_master,
        geocoded={"T1": {"h_name": "역삼2동", "lat": 37.49, "lng": 127.04}},
    )
    check("지오코딩이 이름 매칭보다 우선", with_geo[0]["adm_code"] == "1168053000", str(with_geo[0]))
    check("지오코딩 좌표가 레코드에 실림", with_geo[0]["lat"] == 37.49, str(with_geo[0]))
    wrong_sgg = incident_rows(
        geo_records, resolver, dong_master,
        geocoded={"T1": {"h_name": "치평동"}},  # 다른 시군구(광주)의 동
    )
    check(
        "다른 시군구의 동이 오면 잇지 않고 폴백 (사고가 다른 구로 안 샘)",
        wrong_sgg[0]["adm_code"] is None,
        str(wrong_sgg[0]["adm_code"]),
    )
    no_geo = incident_rows(geo_records, resolver, dong_master)
    check("지오코딩 없으면 기존 폴백 그대로", no_geo[0]["adm_code"] is None and no_geo[0]["lat"] is None)

    # 구분자 정규화: 카카오 '운천.신봉동' 과 원본 '운천·신봉동' 이 같은 동입니다.
    check("squash: 중점 제거", squash_dong("운천·신봉동") == "운천신봉동", squash_dong("운천·신봉동"))
    check("squash: 마침표 제거", squash_dong("운천.신봉동") == "운천신봉동", squash_dong("운천.신봉동"))
    check("squash: 빈 값 안전", squash_dong("") == "" and squash_dong(None) == "")
    dotted = incident_rows(
        geo_records, resolver, dong_master, geocoded={"T1": {"h_name": "운천.신봉동"}},
    )
    check(
        "구분자만 다른 통합 행정동이 붙는다",
        dotted[0]["adm_code"] == "1168057000",
        str(dotted[0]["adm_code"]),
    )
    check(
        "동 정보가 없는 건도 시군구는 남는다",
        incident_rows(
            [{"siDo": "광주광역시", "siGunGu": "서구", "sagoDetail": "하수관 손상"}],
            resolver,
            dong_master,
        )[0]["sgg_code"]
        == "29140",
    )

    print()
    print(f"결과: {passed}개 통과")
    return passed


# ---------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description="지반침하사고 이력을 수집해 시군구로 집계합니다.")
    parser.add_argument("--key", help="공공데이터포털 인증키 (없으면 자체 검증)")
    parser.add_argument("--from-year", type=int, default=DEFAULT_FROM_YEAR)
    parser.add_argument("--to-year", type=int, default=DEFAULT_TO_YEAR)
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="상세 조회 생략. 읍면동이 빠지지만 호출이 1/100로 줄어듭니다",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=paths.SUBSIDENCE_CACHE,
        help="상세 응답 캐시. 재실행 시 재사용합니다",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="API를 호출하지 않고 캐시만으로 재집계",
    )
    parser.add_argument("--master", type=Path, default=paths.DISTRICTS_MASTER)
    parser.add_argument("--sewer", type=Path, default=paths.SEWER_BY_SGG)
    parser.add_argument("--out", type=Path, default=paths.SUBSIDENCE_BY_SGG)
    parser.add_argument(
        "--throttle", type=float, default=THROTTLE, help="호출 간 간격(초)"
    )
    args = parser.parse_args()

    if not args.key and not args.cache_only:
        # 예전에는 `_self_test() and 0` 이라 반환값과 무관하게 항상 0 으로
        # 끝났습니다. 실패는 check 가 AssertionError 로 알리므로 여기 오면 통과입니다.
        _self_test()
        raise SystemExit(0)

    resolver = SggResolver.from_master(args.master)
    master_rows = json.loads(args.master.read_text(encoding="utf-8"))
    master = {row["sgg_code"] for row in master_rows}

    # 노출량 정규화용 총연장
    lengths: dict[str, float] = {}
    if args.sewer.exists():
        for row in json.loads(args.sewer.read_text(encoding="utf-8")):
            length = row.get("pipe_total_length")
            if length:
                lengths[row["sgg_code"]] = float(length)
    else:
        print(f"  [!] {args.sewer} 가 없어 km당 발생률을 계산할 수 없습니다.")
        print("     python3 -m collectors.sewer --csv 를 먼저 실행하세요.")

    cache: dict = {}
    if args.cache.exists():
        cache = json.loads(args.cache.read_text(encoding="utf-8"))

    if args.cache_only:
        if not cache:
            raise SystemExit(f"{args.cache} 에 캐시가 없습니다.")
        print(f"캐시 전용 모드: {len(cache)}건")
        records = [{"sagoNo": key, **value} for key, value in cache.items()]
        # API 를 부르지 않으므로 중점관리대상을 새로 받을 수 없습니다. 기존
        # 산출물의 플래그를 보존합니다 — 예전에는 전부 False 로 덮어써서
        # 재집계할 때마다 지정 정보가 조용히 사라졌습니다.
        targets: set[str] = set()
        if args.out.exists():
            previous = json.loads(args.out.read_text(encoding="utf-8"))
            targets = {
                row["sgg_code"] for row in previous if row.get("is_priority_target")
            }
            print(f"  중점관리대상 {len(targets)}개: 기존 산출물에서 보존")
        else:
            print("  [!] 기존 산출물이 없어 중점관리대상 플래그를 비웁니다.")
    else:
        print("=" * 72)
        print(f"지반침하사고 수집 {args.from_year}~{args.to_year}")
        print("=" * 72)
        client = Client(args.key, args.throttle)
        records = collect(
            client, args.from_year, args.to_year, not args.no_detail, cache
        )

        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  캐시 저장 → {args.cache} ({len(cache)}건)")

        raw_targets = fetch_priority_targets(client, args.from_year, args.to_year)
        targets = match_targets(raw_targets, resolver, master_rows)
        print(f"  중점관리대상 {len(raw_targets)}건 → 시군구 매칭 {len(targets)}개")
        print(f"  총 API 호출 {client.calls}회")

    rows, counters = aggregate(records, resolver, lengths)
    for row in rows:
        row["is_priority_target"] = row["sgg_code"] in targets

    report(rows, counters, master)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print()
    print(f"→ {args.out} ({len(rows)}개 시군구)")

    # 건별 레코드 — cloud_api 가 읍면동 뷰의 침하 카운트를 여기서 적재합니다.
    dong_master = (
        json.loads(paths.ADMIN_DONGS_MASTER.read_text(encoding="utf-8"))
        if paths.ADMIN_DONGS_MASTER.exists()
        else None
    )
    # 지오코딩 결과가 있으면 행정동 정확도가 32% → 90%대로 올라갑니다.
    # 없어도 동작합니다 (이름 매칭 폴백) — 갱신: collectors/geocode.py --build
    geocoded = (
        json.loads(paths.SUBSIDENCE_GEOCODED.read_text(encoding="utf-8"))
        if paths.SUBSIDENCE_GEOCODED.exists()
        else None
    )
    incidents = incident_rows(records, resolver, dong_master, geocoded)
    records_path = args.out.parent / paths.SUBSIDENCE_RECORDS_FILE
    records_path.write_text(
        json.dumps(incidents, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    matched = sum(1 for row in incidents if row["adm_code"])
    print(f"→ {records_path} (건별 {len(incidents)}건, 행정동 매칭 {matched}건)")
    print()
    print("  다음: python3 run.py --supervised   ← 가중치를 데이터가 정합니다")


if __name__ == "__main__":
    main()
