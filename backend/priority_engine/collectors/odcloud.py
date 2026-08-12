from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

ODCLOUD_BASE = "https://api.odcloud.kr/api"

DEFAULT_PER_PAGE = 1000
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3


class OdcloudError(RuntimeError):
    pass


def fetch_page(
    endpoint: str,
    service_key: str,
    page: int,
    per_page: int = DEFAULT_PER_PAGE,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    query = urllib.parse.urlencode(
        {
            "page": page,
            "perPage": per_page,
            "returnType": "JSON",
            "serviceKey": service_key,
        }
    )
    url = f"{ODCLOUD_BASE}/{endpoint.lstrip('/')}?{query}"

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:300]
            # 인증 오류는 재시도해도 소용없으므로 즉시 중단합니다.
            if error.code in {401, 403}:
                raise OdcloudError(
                    f"인증 실패({error.code}). Decoding 키를 쓰고 있는지, "
                    f"활용신청이 승인됐는지 확인하세요.\n응답: {body}"
                ) from error
            last_error = OdcloudError(f"HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = OdcloudError(f"요청 실패: {error}")
        else:
            if "data" not in payload:
                raise OdcloudError(f"예상과 다른 응답 형식입니다: {str(payload)[:300]}")
            return payload

        if attempt < MAX_RETRIES:
            time.sleep(attempt * 1.5)

    raise OdcloudError(f"{MAX_RETRIES}회 재시도 후 실패: {last_error}")


def fetch_all(
    endpoint: str,
    service_key: str,
    per_page: int = DEFAULT_PER_PAGE,
    verbose: bool = True,
) -> list[dict]:
    rows: list[dict] = []
    page = 1
    total: int | None = None

    while True:
        payload = fetch_page(endpoint, service_key, page, per_page)

        if total is None:
            total = int(payload.get("totalCount") or 0)
            if verbose:
                print(f"  totalCount={total:,}")

        batch = payload.get("data") or []
        rows.extend(batch)

        if verbose:
            print(f"  page {page}: {len(batch)}건 (누적 {len(rows):,})")

        # 다음 페이지 조건: 이번 배치가 꽉 찼고 아직 총건수에 못 미쳤을 때
        if not batch or len(batch) < per_page:
            break
        if total and len(rows) >= total:
            break

        page += 1

        # 페이징 버그로 인한 무한 루프 방지
        if page > 1000:
            raise OdcloudError("페이지가 1000을 넘었습니다. 응답을 확인하세요.")

    if total and len(rows) != total:
        print(f"  [!] 수집 {len(rows):,}건 ≠ totalCount {total:,}건")

    return rows


# ---------------------------------------------------------------- 자체 검증


def _self_test() -> int:
    global fetch_page

    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    # 네트워크 없이 검사합니다. fetch_page 는 urlopen 을, fetch_all 은
    # fetch_page 를 각각 가짜로 바꿔서 응답 파싱과 페이지네이션만 봅니다.
    import io

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._body = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

    print("fetch_page: 응답 파싱")
    calls = {"count": 0}
    original_urlopen = urllib.request.urlopen
    original_sleep = time.sleep
    try:
        time.sleep = lambda seconds: None  # 재시도 대기 생략

        def urlopen_ok(request, timeout=None):
            calls["count"] += 1
            return _FakeResponse({"data": [{"a": 1}], "totalCount": 1})

        urllib.request.urlopen = urlopen_ok
        payload = fetch_page("15118453/v1/uddi:test", "KEY", page=1)
        check("정상 응답은 payload 그대로", payload["data"] == [{"a": 1}], str(payload))
        check("성공하면 1회만 호출 (불필요한 재시도 없음)", calls["count"] == 1,
              str(calls["count"]))

        def urlopen_no_data(request, timeout=None):
            return _FakeResponse({"currentCount": 0})

        urllib.request.urlopen = urlopen_no_data
        try:
            fetch_page("e", "KEY", page=1)
            check("data 키가 없으면 예외", False, "")
        except OdcloudError as error:
            check("data 키가 없으면 예외", "다른 응답" in str(error), str(error))

        def urlopen_401(request, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(
                "url", 401, "Unauthorized", None, io.BytesIO(b"denied")
            )

        calls["count"] = 0
        urllib.request.urlopen = urlopen_401
        try:
            fetch_page("e", "KEY", page=1)
            check("401 은 즉시 중단", False, "")
        except OdcloudError as error:
            check("401 은 즉시 중단 (재시도 없음)",
                  calls["count"] == 1 and "인증 실패" in str(error),
                  f"호출 {calls['count']}회: {error}")

        def urlopen_500(request, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(
                "url", 500, "Server Error", None, io.BytesIO(b"oops")
            )

        calls["count"] = 0
        urllib.request.urlopen = urlopen_500
        try:
            fetch_page("e", "KEY", page=1)
            check("500 은 재시도 후 예외", False, "")
        except OdcloudError as error:
            check("500 은 MAX_RETRIES 회 재시도 후 예외",
                  calls["count"] == MAX_RETRIES and "HTTP 500" in str(error),
                  f"호출 {calls['count']}회: {error}")
    finally:
        urllib.request.urlopen = original_urlopen
        time.sleep = original_sleep

    print()
    print("fetch_all: 페이지네이션")
    original_fetch_page = fetch_page
    try:
        pages_seen: list[int] = []

        def paged(total: int, batches: dict[int, int]):
            def fake(endpoint, service_key, page, per_page=DEFAULT_PER_PAGE,
                     timeout=DEFAULT_TIMEOUT):
                pages_seen.append(page)
                size = batches.get(page, 0)
                return {
                    "totalCount": total,
                    "data": [{"row": page * 10000 + i} for i in range(size)],
                }
            return fake

        # 2,500건 = 1000 + 1000 + 500. 마지막 페이지가 덜 차면 멈춰야 합니다.
        fetch_page = paged(2500, {1: 1000, 2: 1000, 3: 500})
        rows = fetch_all("e", "KEY", verbose=False)
        check("3페이지에서 2,500건 수집", len(rows) == 2500, str(len(rows)))
        check("페이지 1·2·3 순서대로", pages_seen == [1, 2, 3], str(pages_seen))
        check("중복 없이 이어 붙임", len({r["row"] for r in rows}) == 2500, "")

        # totalCount 가 페이지 크기의 배수: 꽉 찬 마지막 페이지 다음에 멈춰야 합니다.
        pages_seen.clear()
        fetch_page = paged(2000, {1: 1000, 2: 1000})
        rows = fetch_all("e", "KEY", verbose=False)
        check("총건수 도달 시 빈 페이지를 더 요청하지 않음",
              len(rows) == 2000 and pages_seen == [1, 2], str(pages_seen))

        # totalCount 가 거짓말을 해도 빈 배치에서 멈춰야 합니다.
        pages_seen.clear()
        fetch_page = paged(99999, {1: 1000, 2: 0})
        rows = fetch_all("e", "KEY", verbose=False)
        check("빈 배치에서 중단 (totalCount 불일치 허용)",
              len(rows) == 1000 and pages_seen == [1, 2], str(pages_seen))

        pages_seen.clear()
        fetch_page = paged(0, {})
        check("빈 데이터셋은 빈 목록", fetch_all("e", "KEY", verbose=False) == [], "")
        check("빈 데이터셋도 1페이지는 조회", pages_seen == [1], str(pages_seen))

        # 페이징이 고장 나 같은 응답만 반복되면 무한 루프 대신 예외.
        def broken(endpoint, service_key, page, per_page=DEFAULT_PER_PAGE,
                   timeout=DEFAULT_TIMEOUT):
            return {"totalCount": 10**9, "data": [{"i": 0}] * per_page}

        fetch_page = broken
        try:
            fetch_all("e", "KEY", per_page=2, verbose=False)
            check("페이지 폭주 시 예외", False, "")
        except OdcloudError as error:
            check("페이지 폭주 시 예외 (무한 루프 방지)", "1000" in str(error),
                  str(error))
    finally:
        fetch_page = original_fetch_page

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
