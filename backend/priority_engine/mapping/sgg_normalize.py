from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# 시도 약칭·별칭 → 정식 명칭. 키는 공백 제거·소문자화 이후 형태입니다.
SIDO_ALIASES: dict[str, str] = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "서울특별시": "서울특별시",
    "부산": "부산광역시",
    "부산시": "부산광역시",
    "부산광역시": "부산광역시",
    "대구": "대구광역시",
    "대구시": "대구광역시",
    "대구광역시": "대구광역시",
    "인천": "인천광역시",
    "인천시": "인천광역시",
    "인천광역시": "인천광역시",
    "광주": "광주광역시",
    "광주시": "광주광역시",
    "광주광역시": "광주광역시",
    "대전": "대전광역시",
    "대전시": "대전광역시",
    "대전광역시": "대전광역시",
    "울산": "울산광역시",
    "울산시": "울산광역시",
    "울산광역시": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "세종특별시": "세종특별자치시",
    "세종특별자치시": "세종특별자치시",
    "경기": "경기도",
    "경기도": "경기도",
    "강원": "강원특별자치도",
    "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "충북": "충청북도",
    "충청북도": "충청북도",
    "충남": "충청남도",
    "충청남도": "충청남도",
    "전북": "전북특별자치도",
    "전라북도": "전북특별자치도",
    "전북특별자치도": "전북특별자치도",
    "전남": "전라남도",
    "전라남도": "전라남도",
    "경북": "경상북도",
    "경상북도": "경상북도",
    "경남": "경상남도",
    "경상남도": "경상남도",
    "제주": "제주특별자치도",
    "제주도": "제주특별자치도",
    "제주특별자치도": "제주특별자치도",
}

# 2026-07-01 개편으로 신설된 명칭. 본 프로젝트는 개편 전 기준(2024년 데이터)이므로
#    이 명칭이 입력에 등장하면 조용히 매칭하지 않고 예외로 드러냅니다.
POST_2026_SIDO_NAMES = {"전남광주통합특별시"}


def squash(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return re.sub(r"[\s ·,()\-_]+", "", text)


def normalize_sido(value: str | None) -> str:
    key = squash(value)
    if not key:
        return ""
    if key in POST_2026_SIDO_NAMES:
        raise ValueError(
            f"2026-07-01 개편 후 시도명이 입력되었습니다: {value!r}. "
            "본 파이프라인은 개편 전(광주광역시=29, 전라남도=46) 기준입니다."
        )
    return SIDO_ALIASES.get(key, "")


@dataclass
class SggResolver:
    by_pair: dict[tuple[str, str], str] = field(default_factory=dict)
    sido_only: dict[str, list[str]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    unresolved: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_master(cls, master_path: str | Path) -> SggResolver:
        with Path(master_path).open(encoding="utf-8") as handle:
            rows = json.load(handle)
        return cls.from_rows(rows)

    @classmethod
    def from_rows(cls, rows: list[dict]) -> SggResolver:
        resolver = cls()

        for row in rows:
            code = row["sgg_code"]
            sido_name = row["sido_name"]
            sgg_name = row["sgg_name"]

            resolver.names[code] = row.get("full_name") or f"{sido_name} {sgg_name}"

            sido_key = squash(sido_name)
            resolver.by_pair[(sido_key, squash(sgg_name))] = code
            resolver.sido_only.setdefault(sido_key, []).append(code)

            # 약칭 시도 표기로도 찾을 수 있게 등록
            for alias, canonical in SIDO_ALIASES.items():
                if squash(canonical) == sido_key:
                    resolver.by_pair[(alias, squash(sgg_name))] = code
                    resolver.sido_only.setdefault(alias, []).append(code)

        return resolver

    def resolve(self, sido: str | None, sgg: str | None) -> str | None:
        sido_key = squash(sido)
        sgg_key = squash(sgg)

        # 시도 자체가 약칭이면 정식명 키로도 시도
        candidates = [sido_key]
        canonical = normalize_sido(sido)
        if canonical:
            candidates.append(squash(canonical))

        # 1. 정상 경로: (시도, 시군구) 쌍
        for key in candidates:
            code = self.by_pair.get((key, sgg_key))
            if code:
                return code

        # 2. 구군이 공백인 경우: 단층제 시도(세종)는 시군구가 하나뿐이므로 확정 가능
        if not sgg_key:
            for key in candidates:
                codes = self.sido_only.get(key)
                if codes and len(set(codes)) == 1:
                    return codes[0]

        # 3. 시군구 문자열에 시도가 함께 들어온 경우 ("광주광역시서구")
        if sgg_key:
            for key in candidates:
                if key and sgg_key.startswith(key):
                    trimmed = sgg_key[len(key) :]
                    code = self.by_pair.get((key, trimmed))
                    if code:
                        return code

        # 4. 일반구가 붙어 들어온 경우 ("수원시장안구" → 부모 시 "수원시")
        if sgg_key.endswith("구") and "시" in sgg_key:
            parent = sgg_key[: sgg_key.find("시") + 1]
            for key in candidates:
                code = self.by_pair.get((key, parent))
                if code:
                    return code

        self.unresolved.append((str(sido or ""), str(sgg or "")))
        return None

    def name_of(self, code: str) -> str:
        return self.names.get(code, code)

    def report_unresolved(self) -> str:
        if not self.unresolved:
            return "미해결 없음"
        seen = sorted(set(self.unresolved))
        lines = [f"미해결 {len(seen)}종:"]
        lines += [f"  시도={s!r} 구군={g!r}" for s, g in seen]
        return "\n".join(lines)


def diff_codes(data_codes: set[str], map_codes: set[str]) -> tuple[set[str], set[str]]:
    return data_codes - map_codes, map_codes - data_codes


# ---------------------------------------------------------------- 자체 검증


def _self_test(master_path: str) -> int:
    resolver = SggResolver.from_master(master_path)
    print(f"마스터 로드: {len(resolver.names)}개 시군구\n")

    cases: list[tuple[str, str, str | None]] = [
        # 정상 경로
        ("광주광역시", "서구", "29140"),
        ("광주광역시", "광산구", "29200"),
        ("서울특별시", "종로구", "11110"),
        # 동명 시군구: 시도로 구분되어야 함
        ("부산광역시", "중구", "26110"),
        ("대구광역시", "중구", "27110"),
        ("인천광역시", "동구", "28140"),
        ("광주광역시", "동구", "29110"),
        # 시도 약칭
        ("광주", "서구", "29140"),
        ("전남", "목포시", "46110"),
        ("강원", "춘천시", "51110"),
        ("전북", "전주시", "52110"),
        # 구군 공백 (단층제)
        ("세종특별자치시", "", "36110"),
        ("세종", None, "36110"),
        # 일반구가 붙어 들어온 경우 → 부모 시
        ("경기도", "수원시장안구", "41110"),
        ("경상남도", "창원시진해구", "48120"),
        # 시도가 시군구 칸에 함께 들어온 경우
        ("광주광역시", "광주광역시서구", "29140"),
        # 공백·구분자 혼입
        ("광주 광역시", " 서구 ", "29140"),
        # 실패해야 하는 입력
        ("경기도", "없는구", None),
    ]

    failures = 0
    for sido, sgg, expected in cases:
        actual = resolver.resolve(sido, sgg)
        ok = actual == expected
        if not ok:
            failures += 1
        mark = "OK  " if ok else "FAIL"
        got = f"{actual} ({resolver.name_of(actual)})" if actual else "None"
        print(f"  [{mark}] {sido!r:14} {str(sgg)!r:18} → {got}")
        if not ok:
            print(f"         기대: {expected}")

    # 2026 개편 명칭은 예외로 드러나야 함
    try:
        resolver.resolve("전남광주통합특별시", "동구")
        print("  [FAIL] 2026 개편 시도명이 예외 없이 통과했습니다")
        failures += 1
    except ValueError:
        print("  [OK  ] 2026 개편 시도명 입력 시 예외 발생")

    print()
    print(f"결과: {len(cases) + 1 - failures}/{len(cases) + 1} 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m mapping.sgg_normalize <districts_master.json>")
        raise SystemExit(2)
    raise SystemExit(_self_test(sys.argv[1]))
