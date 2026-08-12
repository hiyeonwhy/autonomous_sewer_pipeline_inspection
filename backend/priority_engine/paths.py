from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

RAW = ROOT / "raw"
DATA = ROOT / "data"

# 시각화 산출물. analysis/ 를 돌리면 여기에 PNG 가 쌓입니다.
# PNG 는 git 델타가 안 먹어 재생성마다 전체 사본이 히스토리에 남습니다(1회 약
# 700 KB). 그래도 커밋합니다 — 분석 결과를 저장소에서 바로 볼 수 있어야 하고,
# 자주 바뀌는 파일이 아닙니다. 재생성이 잦아지면 .gitignore 로 옮기세요.
CHARTS = ROOT / "charts"

# ---- 원본 (raw/). 포털 파일명을 그대로 유지해야 출처를 추적할 수 있습니다.
# `_20241231` 같은 데이터 기준일이 파일명에만 들어 있습니다.
SEWER_CSV = RAW / "한국환경공단_하수관로시설별설치현황_20241231.csv"
FACILITIES_CSV = RAW / "한국환경공단_공공하수처리시설 현황_20241231.csv"
# 2026-07-31 공표된 2025판으로 교체. 시트가 이름으로 조회되므로(신규
# '2-5. 맨홀현황' 추가) 파서 수정 없이 그대로 읽힙니다. 노후율 사용 가능
# 구역이 155 → 171개로 늘었습니다(광주 동구 포함).
SEWER_STATS_XLSX = RAW / "2025 하수도통계.xlsx"
# 주민등록 인구·세대 (행정안전부, 활용신청 불필요, CP949). 읍면동 코드가
# 행안부 10자리라 admin_dongs_master.json 의 adm_code 와 바로 붙습니다.
POPULATION_CSV = RAW / "행정안전부_행정동별_주민등록인구_20260630.csv"
HOUSEHOLDS_CSV = RAW / "행정안전부_행정동별_주민등록세대_20260630.csv"

# ---- 산출물 파일명.
# 전체 경로가 아니라 파일명을 상수로 둡니다. 파이프라인·분석이 `--data` 로
# 디렉터리를 바꿀 수 있어야 하는데 전체 경로 상수로는 그것을 표현할 수 없어,
# 각 모듈이 같은 파일명을 다시 적고 있었습니다(한 이름이 최대 3곳에 중복).
# 이름을 바꿀 때 한 곳만 고치면 되도록 여기로 모았습니다.

# 마스터 — boundary/build_boundaries.py 가 생성 (빌드 타임, 1회)
DISTRICTS_MASTER_FILE = "districts_master.json"              # 기초자치단체 229
ADMIN_DONGS_MASTER_FILE = "admin_dongs_master.json"          # 광주 읍면동 96

# 경계 — 같은 빌드에서 생성. 프론트가 직접 읽습니다.
# 252 단위(일반구 분리) 산출물은 소비자가 없어 만들지 않습니다 — 필요해지면
# build_boundaries.py 에 단위 하나 추가해 5초 재생성.
MUNICIPALITY_BOUNDARIES_FILE = "municipality_boundaries.geojson"
SIDO_BOUNDARIES_FILE = "sido_boundaries.geojson"           # 시도 17 (경계선용)
ADMIN_DONGS_GEOJSON_FILE = "admin_dongs.geojson"           # 읍면동 (전국)

# 수집 결과 — collectors/ 가 생성
SEWER_BY_SGG_FILE = "sewer_by_sgg.json"
PIPE_AGE_BY_SGG_FILE = "pipe_age_by_sgg.json"
RAINFALL_BY_SGG_FILE = "rainfall_by_sgg.json"
POPULATION_BY_SGG_FILE = "population_by_sgg.json"
POPULATION_BY_DONG_FILE = "population_by_dong.json"
MANHOLE_BY_SGG_FILE = "manhole_by_sgg.json"   # 집중강우 중점관리구역 (2025판 신설 시트)
FACILITY_AGE_BY_SGG_FILE = "facility_age_by_sgg.json"
SUBSIDENCE_BY_SGG_FILE = "subsidence_by_sgg.json"
SUBSIDENCE_RECORDS_FILE = "subsidence_records.json"  # 건별 — API 가 읍면동 뷰에 적재
SUBSIDENCE_CACHE_FILE = "subsidence_detail_cache.json"       # N+1 상세 조회 캐시
SUBSIDENCE_GEOCODED_FILE = "subsidence_geocoded.json"  # 카카오 지오코딩 결과 (키 불포함)

# 산정·분석 결과
PRIORITY_SCORES_FILE = "priority_scores.json"
PRIORITY_BY_SIDO_FILE = "priority_by_sido.json"
DATA_PROFILE_FILE = "data_profile.json"                      # explore.py

# 기준값 — 사람이 정해 커밋한 값. 파이프라인이 대조에만 씁니다.
WEIGHTS_PREREGISTERED_FILE = "weights_preregistered.json"
RAW_CHECKSUMS_FILE = "raw_checksums.json"

# ---- 기본 디렉터리 기준 전체 경로. `--data` 를 받지 않는 곳에서 씁니다.
# 쓰는 곳이 있는 것만 둡니다 — 새 사용처가 생기면 `DATA / <이름>_FILE` 로
# 여기에 추가하세요.
DISTRICTS_MASTER = DATA / DISTRICTS_MASTER_FILE
ADMIN_DONGS_MASTER = DATA / ADMIN_DONGS_MASTER_FILE

MUNICIPALITY_BOUNDARIES = DATA / MUNICIPALITY_BOUNDARIES_FILE

SEWER_BY_SGG = DATA / SEWER_BY_SGG_FILE
PIPE_AGE_BY_SGG = DATA / PIPE_AGE_BY_SGG_FILE
FACILITY_AGE_BY_SGG = DATA / FACILITY_AGE_BY_SGG_FILE
SUBSIDENCE_BY_SGG = DATA / SUBSIDENCE_BY_SGG_FILE
SUBSIDENCE_CACHE = DATA / SUBSIDENCE_CACHE_FILE
SUBSIDENCE_GEOCODED = DATA / SUBSIDENCE_GEOCODED_FILE

RAW_CHECKSUMS = DATA / RAW_CHECKSUMS_FILE
