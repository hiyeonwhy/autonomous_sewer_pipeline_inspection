from __future__ import annotations

# 실키로 확정(2026-08-03): 기관코드는 1613000, 스킴은 https 입니다.
#   - 1611000 → NO_OPENAPI_SERVICE_ERROR(12). 더미 키 시절 500 이 온 것을
#     "라우팅됨"으로 읽었는데 실제로는 서비스가 없습니다
#   - http:// 는 응답이 없습니다(연결 후 무응답 → 타임아웃). https 만 씁니다
BASE_URL = "https://apis.data.go.kr/1613000/undergroundsafetyinfo01"

# ---- 오퍼레이션 (가이드 p.2 「상세기능 목록」 18종 전체)
OPERATIONS = {
    "getImpatEvalutionList01": "지하안전평가 리스트",       # 원문 오타 그대로 (Impat)
    "getImpatEvalutionInfo01": "지하안전평가 정보",
    "getSmallImpactEvalutionList01": "소규모지하안전평가 리스트",
    "getSmallImpactEvalutionInfo01": "소규모지하안전평가 정보",
    "getAfterUndergroundSafetyInspectionList01": "착공후지하안전조사 리스트",
    "getAfterUndergroundSafetyInspectionInfo01": "착공후지하안전조사 정보",
    "getSubsidenceEvalutionList01": "지반침하위험도평가 리스트",
    "getSubsidenceResult01": "지반침하위험도평가 안전조치내용",
    "getSubsidenceExpediency01": "지반침하위험도평가 응급조치내용",
    "getPriorityControlTargetList01": "중점관리대상 리스트",
    "getPriorityControlTargetInfo01": "중점관리대상 정보",
    "getSubsidenceList01": "지반침하사고 리스트",          # ← 우선순위의 정답 데이터
    "getSubsidenceInfo01": "지반침하사고 정보",            # ← 원인·읍면동
    "getProfessionalInstituionList01": "전문기관 리스트",   # 원문 오타 그대로 (Instituion)
    "getProfessionalInstituionInfo01": "전문기관 정보",
    "getProfessionalInstituionresultInfo01": "전문기관 실적",
    "getUndergroundDevelopementProjectList01": "지하개발사업 리스트",  # 원문 오타 (Developement)
    "getUndergroundDevelopementProjectInfo01": "지하개발사업 정보",
}

# 오퍼레이션명에 오타가 3개 있습니다 (Impat, Instituion, Developement).
# 서버가 이 오타 그대로 라우팅하므로 고쳐 쓰면 404가 납니다.
KNOWN_TYPOS = ("Impat", "Instituion", "Developement", "getSmallImpactEvalution")


# ---- (12) 지반침하사고 리스트: getSubsidenceList01 (가이드 p.26-27)
SUBSIDENCE_LIST = "getSubsidenceList01"
SUBSIDENCE_LIST_PARAMS = {
    "sagoDateFrom": "사고기간(시작) YYYYMMDD 필수",
    "sagoDateTo": "사고기간(종료) YYYYMMDD 필수",
    "numOfRows": "페이지당 목록 수",
    "pageNo": "페이지번호",
}
# [실응답 확정 2026-08-03] 가이드 표기와 실제 키가 다릅니다. 실제가 기준입니다.
#   siDo → sido · siGunGu → sigungu · sagoDetail → sagoReason
# 리스트에 원인이 **이미 들어 있습니다**(가이드는 상세에만 있다고 적혀 있음).
# 덕분에 시군구 집계는 상세 조회(N+1) 없이 리스트만으로 끝납니다.
SUBSIDENCE_LIST_FIELDS = {
    "sagoNo": "사고번호",
    "sido": "시도명: 문자열 '서울특별시'",
    "sigungu": "시군구명: 문자열 '강남구'",
    "sagoReason": "상세 발생원인: 자유 텍스트 (상세의 sagoDetail 과 같은 값)",
    "sagoDate": "발생일자 YYYYMMDD (선택 항목)",
    "no": "순번",
}

# ---- (13) 지반침하사고 정보: getSubsidenceInfo01 (가이드 p.29-30)
SUBSIDENCE_INFO = "getSubsidenceInfo01"
SUBSIDENCE_INFO_PARAMS = {
    "sagoNo": "사고번호 필수",
    "numOfRows": "페이지당 목록 수",
    "pageNo": "페이지번호",
}
SUBSIDENCE_INFO_FIELDS = {
    "sagoNo": "사고번호",
    "sido": "시도명",
    "sigungu": "시군구명",
    "dong": "읍면동명: 문자열 '역삼동'",   # ← Level 2 조인 키
    "addr": "상세주소 '147-2앞 (봉은사로 86길)'",
    # [실응답 확정 2026-08-03] 가이드에 없던 좌표 필드가 존재합니다. 다만 표본에서
    # '0E-10'(=0, 미기재)이 나오므로 값이 채워진 비율을 세어보고 쓸지 판단하세요.
    "sagoLat": "위도 (미기재 시 '0E-10')",
    "sagoLon": "경도 (미기재 시 '0E-10')",
    "trAmt": "복구비용 (가이드 미기재 필드)",
    "sagoDate": "발생일자",
    "sinkWidth": "발생규모 폭 (m)",
    "sinkExtend": "발생규모 연장 (m)",
    "sinkDepth": "발생규모 깊이 (m)",
    "grdKind": "발생지역 지질종류 (선택)",
    "sagoDetail": "상세 발생원인: 자유 텍스트",
    "deathCnt": "피해 사망자수",
    "injuryCnt": "피해 부상자수",
    "vehicleCnt": "피해 차량대수",
    "trStatus": "복구상태 '완전복구'",
    "trMethod": "복구방법 '되메움'",
    "trFnDate": "복구완료일자",
    "daStDate": "데이터 생성일자",
}

# ---- (10) 중점관리대상 리스트: getPriorityControlTargetList01 (가이드 p.21)
PRIORITY_TARGET_LIST = "getPriorityControlTargetList01"
PRIORITY_TARGET_LIST_PARAMS = {
    # 대소문자가 비대칭입니다: From은 대문자 F, to는 소문자 t. 원문 그대로입니다.
    "heavyYmdFrom": "지정기간(시작) YYYYMMDD 필수",
    "heavyYmdto": "지정기간(종료) YYYYMMDD 필수",
    "numOfRows": "페이지당 목록 수",
    "pageNo": "페이지번호",
}
# [실응답 확정 2026-08-03] 가이드의 faciINo·faciNm 은 실제로 facilNo·facilNm 입니다.
# 전국 등록 건수가 **5건**뿐이고 facilNm 이 지역명이 아니라 시설물명('오수관',
# '가스관(평택LT~부곡)')이라 시군구로 붙일 근거가 없습니다 → 05 문서 참조.
PRIORITY_TARGET_LIST_FIELDS = {
    "facilNo": "시설물번호 'STP2002-000024'",
    "facilNm": "시설물명 (지역명이 아님)",
    "heavyYmd": "중점관리대상 지정일",
    "heavyReason": "중점관리대상 사유",
    "mngNotifyYmd": "관리통보일",
    "no": "순번",
}

# ---- 필드 별칭
# 가이드 표기와 실응답 표기가 다르고, 리스트(sagoReason)와 상세(sagoDetail)도
# 이름이 다릅니다. 어느 쪽이 와도 읽히도록 후보를 순서대로 둡니다 —
# 앞에 있는 것이 실응답 표기입니다.
SIDO_KEYS = ("sido", "siDo")
SIGUNGU_KEYS = ("sigungu", "siGunGu")
CAUSE_KEYS = ("sagoReason", "sagoDetail")
FACILITY_NAME_KEYS = ("facilNm", "faciNm")


# 하수관 원인 필터. 원인이 자유 텍스트 VARCHAR(2000)이라 문자열 매칭이
# 유일한 방법입니다. 상수도(급수)와 하수도(배수)를 반드시 구분하세요 -
# "상수도 파열"(가이드 예시)을 하수관으로 세면 우선순위가 오염됩니다.
SEWER_CAUSE_KEYWORDS = ("하수관", "하수도", "오수관", "우수관", "합류관", "배수관")
WATER_CAUSE_KEYWORDS = ("상수관", "상수도", "급수관", "송수관", "배수지")

# 관리청이 확인하기 전 자료는 원인이 "확인중"으로 들어옵니다 (가이드 p.27 각주).
# 결측(None)과 구분해야 합니다. 확인중은 "하수관이 아니다"가 아니라 "모른다"입니다.
UNCONFIRMED_MARKERS = ("확인중", "조사중", "미확인")
