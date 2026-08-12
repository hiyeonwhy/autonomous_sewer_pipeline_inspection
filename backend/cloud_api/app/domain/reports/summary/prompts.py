"""프롬프트 구성과 모델 출력 검증.

모델이 무엇을 볼 수 있는지(payload)와 무엇을 통과시킬지(parse)를 한곳에 둔다.
검증에 걸리면 `ValueError`를 던지고, 호출부가 그 조각만 규칙 기반으로 강등한다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain.reports.document import DEFECT_TYPE_LABELS, SEVERITY_LABELS

from .facts import allowed_distances
from .models import DefectFact, ReportNarrative, SessionFacts, StateGrades

# 개조식(명사형 종결) — 관공서 조사보고서 문체.
_STYLE_RULE = (
    "문장은 개조식으로 끝낸다. '확인됨', '산정됨', '관찰됨', '검토 필요'처럼 "
    "명사형으로 종결하고, '확인되었다', '~한다', '~습니다' 같은 평서형 종결은 쓰지 않는다. "
)

NARRATIVE_SYSTEM_PROMPT = (
    "너는 하수관로 점검 보고서의 종합 평가 문단 작성기다. "
    "입력 JSON에 있는 사실만 사용해 한국어로 작성한다. "
    # 한 덩어리 문단으로 받으면 등급·점수·권고가 한 줄에 뭉쳐 읽기 어렵다.
    # 조사보고서는 항목을 줄로 나눠 적으므로 줄바꿈까지 모델이 넣게 한다.
    # 마크다운 금지 규칙이 있으므로 글머리 기호(-, •)는 쓰지 않고 줄바꿈만 쓴다.
    "assessment는 관로 상태를 서술하는 3~5개 항목으로 쓰고, 항목마다 줄바꿈(\\n)으로 "
    "나눈다. 한 항목은 한 문장이며 글머리 기호나 번호를 붙이지 않는다. "
    "recommendations는 조치 권고를 1~4개 항목으로 각각 한 문장씩 쓴다. "
    + _STYLE_RULE
    + "입력에 없는 거리, 수치, 결함 종류, 관 규격, 맨홀 정보를 만들지 않는다. "
    # 로봇에 주행계가 없어 결함마다 위치가 안 붙는다. 그때 모델이 0을 끌어다 쓰면
    # 「12건이 0.0m 지점에서 집중 관찰되어」 같은 없는 사실이 보고서에 남는다.
    "결함에 위치(positionM)가 주어지지 않으면 위치를 아예 언급하지 않는다. "
    "'0m 지점', '같은 지점', '한 지점에 집중'처럼 위치를 지어내지 않고 "
    "결함의 종류와 심각도만으로 서술한다. "
    # 상태등급은 환경부 지침 절차대로 코드가 계산한 값이다. 모델이 다시 판정하면
    # 근거를 댈 수 없는 숫자가 보고서에 남는다.
    "상태등급은 환경부 지침에 따라 이미 산정되어 입력으로 주어지므로 "
    "다시 판정하거나 다른 값으로 바꾸지 않고 그대로 인용한다. "
    "조사구간 상태는 등급이 아니라 상태점수(소수 가능)이므로 점수로 인용하고, "
    "필요하면 함께 주어진 환산 등급과 상태 표현을 덧붙인다. "
    "보고서 문체를 유지하고 이모지나 마크다운 기호를 쓰지 않는다. "
    '반드시 {"assessment":"평가 문단","recommendations":["권고1","권고2"]} JSON만 출력한다.'
)

# 사진 묘사에는 탐지 결과를 **일부러 주지 않는다.**
#
# 라벨을 주면 모델이 사진과 무관하게 그 라벨을 정당화한다. 같은 사진에 '균열'을
# 주면 "관 상부 12시 방향에 균열 관찰됨", '뿌리 침투'를 주면 "관 하부 6시 방향에
# 뿌리 침투 관찰됨"이 나왔다. 라벨 없이 물으면 같은 사진에 "큰 결함은 보이지 않으며
# 결함이나 막힘은 없다"고 답한다.
#
# 그래서 종류와 심각도만 주고 수치는 주지 않으며, 「찾아보라」고만 지시한다
# (자세한 근거는 `build_photo_payload`). 두 섹션이 어긋날 수 있게 두는 것이
# 목적이므로 검증기가 동의를 요구하지 않는다.
PHOTO_SYSTEM_PROMPT = (
    "너는 하수관로 내부 CCTV 사진을 판독한다. "
    # 사진은 다른 결함이 있는 부분을 검게 덮어 보낸다. 이 설명이 없으면 모델이
    # 검은 영역을 관 내부의 어둠이나 개구부로 읽고 없는 소견을 만든다.
    "사진에서 검게 덮인 영역은 판독 대상이 아닌 부분을 가린 것이며 관 내부가 아니다. "
    "검은 영역은 묘사하지 않고, 덮이지 않은 영역만 보고 판단한다. "
    # 색칠은 탐지 결과를 덧입힌 것인데, 이 설명이 없으면 모델이 물체로 읽는다.
    # 실측(세션 216, 30건)에서 초록을 "녹색 테이프", "코팅 또는 라이닝", "이물질"로
    # 서술하고 **그 아래 손상은 가려서 확인 불가**로 처리한 건이 4건이었다. 정작
    # 초록으로 칠해진 그 자리가 표면 손상인데 반대로 읽은 셈이다.
    "사진의 붉은색과 초록색 반투명 영역은 탐지 모델이 결함 자리에 덧칠한 표시이며 "
    "관 안에 있는 물체가 아니다. 붉은색은 균열로 탐지된 자리, 초록색은 표면 손상으로 "
    "탐지된 자리다. 초록색 자리는 원래 관 내벽의 회색과 다른 갈색 변색부다. "
    "이 색을 테이프, 코팅, 라이닝, 표시물, 이물질로 부르지 않는다. "
    "색이 덧칠된 자리에 무엇이 있는지가 아니라, 그 자리의 결함이 어떤 형태와 "
    "범위로 보이는지를 기술한다. "
    # 탐지 결과를 참고로 준다. 문구를 정할 때 두 방향으로 다 틀릴 수 있다.
    #
    # 라벨을 결론으로 받아들이면 사진에 없는 근거를 만든다 — 화면 15%짜리 퇴적물에
    # 폐색률 72%를 갖다 붙인 전례. 반대로 의심을 앞세우면 실재하는 결함도 거부한다.
    # 실측에서 "참고 자료이며 확정된 사실이 아니다" + 부정 문장 예시를 줬을 때
    # 실재하는 균열을 8회 중 8회 부정했다(기존 문구는 12회 중 8회 확인). 모델은
    # 주어진 예시 문장을 그대로 베끼므로 **부정 예시를 주면 안 된다.**
    #
    # 그래서 지금은 「찾아보라」만 지시하고, 부정은 예시 없이 한 번만 허용한다.
    "입력으로 탐지 모델이 찾은 결함 종류를 함께 준다. 그 결함이 사진의 어디에 어떤 "
    "형태로 보이는지 찾아, 위치와 생김새를 근거로 기술한다. "
    "사진에서 아무리 봐도 그 결함을 찾을 수 없을 때에만 찾을 수 없다고 쓴다. "
    "탐지 결과에 없는 결함이 보이면 그것도 함께 쓴다. "
    "탐지 결과에 있는 수치를 사진 묘사의 근거로 쓰지 않는다. "
    "첨부된 사진에 실제로 보이는 것만 한국어 2~4문장으로 기술한다. "
    "관 내부 상태, 눈에 띄는 결함이나 이물질, 물의 상태를 본 대로 쓴다. "
    "결함이 보이면 위치를 '관 상부 12시 방향', '화면 하부 좌측'처럼 "
    "구체적으로 쓰고, 보이지 않으면 보이지 않는다고 쓴다. "
    "추정한 것은 '육안 추정'이라고 밝힌다. "
    + _STYLE_RULE
    + "'상당 부분', '다수' 같은 표현으로 결함의 양을 대신하지 않는다. "
    "사진에서 확인할 수 없는 관 규격, 관종, 맨홀 정보, 거리 수치를 만들지 않는다. "
    "보수 공법을 단정하지 않고 관찰 소견만 남긴다. "
    "이모지나 마크다운 기호를 쓰지 않는다. "
    # 「보였나」를 문장에서 되짚어 읽으면 틀린다. "물 고임이나 이물질은 보이지 않음"
    # 같은 상투 문장이 결함 부정으로 오독됐다(실측에서 30건 중 23건을 잘못 셌다).
    # 판정은 모델이 직접 내리게 하고, 화면은 그 값만 본다.
    # 막힘은 카메라가 장애물에 막혀 화면이 어둡거나 한 색으로 가득 찬다. 그걸
    # 「어두워서 판단 불가」로 읽으면 정작 가장 중요한 결함을 놓친다.
    "탐지 결과가 막힘일 때, 관 앞이 막혀 화면이 어둡거나 한 덩어리로 가득 차 보이는 "
    "것은 막힘의 모습이므로 그렇게 기술한다. 어둡다는 이유만으로 판단 불가로 쓰지 않는다. "
    "사진에서 그 결함을 실제로 확인했으면 confirmed 를 true, 아무리 봐도 찾을 수 "
    "없거나 관 내부가 찍히지 않아 판단할 수 없으면 false 로 적는다. "
    '반드시 {"description":"사진 묘사","confirmed":true} 형태의 JSON만 출력한다.'
)

_FORBIDDEN_PHRASES = ("하세요", "하십시오", "해주세요", "바랍니다")
_MARKDOWN_MARKERS = ("**", "##", "- ", "* ")
# "25.8m"는 잡고 "40%"는 넘긴다 — 퍼센트 뒤 m가 붙는 경우가 없으므로 안전.
_DISTANCE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*m\b")
# 평서형 종결("~다." / 문장 끝 "~다") 탐지 — 개조식 위반 신호.
# MULTILINE 이라야 줄마다 끝을 본다. 종합 평가가 여러 줄로 오므로, 없으면 문자열
# 맨 끝 줄만 검사하고 중간 줄의 평서형 종결("~이다\n")을 놓친다.
_DECLARATIVE_PATTERN = re.compile(r"다\s*[.!?]|다\s*$", re.MULTILINE)
# 판독 소견은 면적 비율을 수치로 남겨야 한다.
# 수치 대신 쓰이는 모호어. '약간'/'조금'은 결함이 안 보인다고 쓸 때 정직한 표현이라
# 뺐다 — 모델이 반대 의견을 낼 수 있어야 하는데 그 문장을 거부하면 안 된다.
# 결함의 **양**을 뭉뚱그리는 표현만 막는다.
#
# 「대부분」은 뺐다. 화면이 얼마나 가려졌는지를 말할 때 쓰는 낱말이라 결함을
# 뭉뚱그리는 것과 다르다. 실제로 막힘 사진에 "화면 대부분이 어둡게 보이며"라고
# 정확히 쓴 판독이 이 검사에 걸려 두 번 다 반려됐고, 그 결함이 보고서에서 빠졌다.
_VAGUE_QUANTIFIERS = ("상당 부분", "다수", "많은 양")

MIN_ASSESSMENT_CHARS = 30
MAX_ASSESSMENT_CHARS = 1200
MAX_RECOMMENDATION_CHARS = 200
MAX_RECOMMENDATIONS = 6
MIN_DESCRIPTION_CHARS = 15
MAX_DESCRIPTION_CHARS = 400


def _reject_shared_style_violations(text: str, allowed_positions: set[str]) -> None:
    """모델이 쓴 모든 문자열에 공통으로 거는 검사."""
    if any(phrase in text for phrase in _FORBIDDEN_PHRASES):
        raise ValueError("모델 출력에 지시형 표현이 있음")
    if any(marker in text for marker in _MARKDOWN_MARKERS):
        raise ValueError("모델 출력에 마크다운 기호가 있음")
    if _DECLARATIVE_PATTERN.search(text):
        raise ValueError("모델 출력이 개조식이 아닌 평서형으로 종결됨")

    cited = {match.group(1) for match in _DISTANCE_PATTERN.finditer(text)}
    invented = {value for value in cited if value not in allowed_positions}
    if invented:
        raise ValueError(f"모델이 존재하지 않는 위치를 인용함: {sorted(invented)}")


def first_json_object(text: str) -> dict[str, Any] | None:
    """응답 어디에 있든 첫 JSON 객체를 꺼낸다.

    코드펜스나 앞뒤 설명이 붙어 와도 파싱되게 하려는 것이다.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def build_narrative_payload(facts: SessionFacts, grades: StateGrades) -> dict[str, Any]:
    """모델에게 보여줄 세션 데이터. 여기 없는 값은 모델도 알 수 없다."""
    from .grading import grade_state, grade_urgency

    payload: dict[str, Any] = {
        "region": facts.region,
        "defectTotal": facts.defect_total,
        "omittedDefectCount": facts.dropped_defect_count,
        # 환경부 지침 제6장. 조사구간 값은 등급이 아니라 상태점수(단위구간 등급의 평균)다.
        "surveySection": {
            "segmentCount": grades.segment_count,
            "segmentLengthM": grades.segment_length_m,
            "structuralScore": grades.structural,
            "operationalScore": grades.operational,
        },
        "structuralGrade": grades.structural_grade,
        "structuralState": grade_state(grades.structural_grade),
        "structuralUrgency": grade_urgency(grades.structural_grade),
        "operationalGrade": grades.operational_grade,
        "operationalState": grade_state(grades.operational_grade),
        "operationalUrgency": grade_urgency(grades.operational_grade),
        # 위치는 **실측이 있을 때만** 준다.
        #
        # 로봇에 주행계가 없어 위치가 늘 0m 로 온다. 그걸 그대로 주면 모델이
        # 「총 12건이 0.0m 지점에서 집중 관찰되어」처럼 **없는 사실**을 쓴다.
        # 관 입구 한 자리에 결함이 몰려 있다는 뜻이 되는데 사실이 아니다.
        # 양식 쪽은 이미 같은 이유로 0m 를 안 싣는다(`document.build_rows`).
        "defects": [
            {
                **({"positionM": defect.position_m} if defect.position_m > 0 else {}),
                "type": DEFECT_TYPE_LABELS[defect.defect_type.value],
                "severity": SEVERITY_LABELS[defect.severity.value],
                "confidence": defect.confidence,
            }
            for defect in facts.defects
        ],
    }
    if facts.survey_location:
        payload["surveyLocation"] = facts.survey_location
    if facts.duration:
        payload["duration"] = facts.duration
    return payload


def build_photo_payload(defect: DefectFact) -> dict[str, Any]:
    """사진 묘사 호출에 딸려 보내는 입력 — 탐지 결과를 참고 자료로 준다.

    무엇을 주고 무엇을 빼는지가 중요하다.

    **주는 것**은 결함 종류와 심각도다. 모델이 사진에서 무엇을 찾아야 하는지 알면
    얇은 균열처럼 놓치기 쉬운 결함을 확인할 확률이 올라간다.

    **빼는 것**은 신뢰도 같은 수치다. 수치를 주면 모델이 그것을 사진에서 읽어낸 것처럼
    문장에 옮긴다 — 지금은 없어진 폐색률 필드를 줬을 때, 화면의 15%를 차지하는
    퇴적물에 폐색률 72%를 갖다 붙인 전례가 있다. 숫자는 코드가 「탐지 결과」 섹션에
    따로 적으므로 판독 소견이 다시 말할 이유가 없다.

    거리(`position_m`)도 빼둔다. 사진 한 장으로는 확인할 수 없는 값이고, 주면
    묘사에 섞여 나온다. 검증기가 거리 환각을 잡는 근거도 여기에 있다.
    """
    return {
        "탐지_결과": {
            "종류": DEFECT_TYPE_LABELS[defect.defect_type.value],
            "심각도": SEVERITY_LABELS[defect.severity.value],
        }
    }


def parse_narrative_text(
    text: str, *, facts: SessionFacts, grades: StateGrades | None = None
) -> ReportNarrative:
    """세션 단위 서술 JSON을 꺼내 검증한다.

    쓸 수 없는 출력은 전부 `ValueError`로 막아, 호출부가 잘못되거나 환각이 섞인
    요약을 저장하는 대신 규칙 기반으로 내려갈 수 있게 한다.
    """
    payload = first_json_object(text)
    if payload is None:
        raise ValueError("응답에서 JSON 서술을 찾지 못함")

    # 종합 평가는 여러 줄로 온다. 모델이 `\r\n`이나 빈 줄을 섞어 보내므로 줄 단위로
    # 다듬어 저장한다 — 양식이 이 줄바꿈을 그대로 항목 구분으로 쓴다.
    assessment = "\n".join(
        line.strip()
        for line in str(payload.get("assessment", "")).replace("\r\n", "\n").split("\n")
        if line.strip()
    )
    if len(assessment) < MIN_ASSESSMENT_CHARS:
        raise ValueError("종합 평가가 비었거나 너무 짧음")
    if len(assessment) > MAX_ASSESSMENT_CHARS:
        raise ValueError("종합 평가가 길이 제한을 넘음")

    raw_recommendations = payload.get("recommendations", [])
    if not isinstance(raw_recommendations, list):
        raise ValueError("recommendations는 배열이어야 함")
    recommendations = [str(item).strip() for item in raw_recommendations if str(item).strip()]
    if len(recommendations) > MAX_RECOMMENDATIONS:
        raise ValueError("조치 권고 항목이 너무 많음")
    if any(len(item) > MAX_RECOMMENDATION_CHARS for item in recommendations):
        raise ValueError("조치 권고 항목이 길이 제한을 넘음")

    # 줄바꿈으로 이어야 문체 검사가 줄마다 끝을 본다. 공백으로 이으면 여러 항목이
    # 한 줄이 되어 중간 항목의 평서형 종결을 놓친다.
    combined = "\n".join([assessment, *recommendations])
    _reject_shared_style_violations(combined, allowed_distances(facts, grades))

    return ReportNarrative(assessment=assessment, recommendations=recommendations)


def parse_photo_confirmed(text: str) -> bool:
    """모델이 「사진에서 결함을 확인했다」고 했는지.

    값이 없거나 형태가 이상하면 **확인한 것으로 본다.** 판정을 못 읽었다는 이유로
    실재하는 결함을 지우는 쪽이, 오탐 하나를 남기는 쪽보다 나쁘다.
    """
    payload = first_json_object(text)
    if payload is None:
        return True
    value = payload.get("confirmed")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "no", "0"}
    return True


def parse_photo_description_text(text: str, *, defect: DefectFact) -> str:
    """사진 묘사 1건을 꺼내 검증하고 문장만 돌려준다.

    **결함 종류를 언급하라는 검사는 일부러 없다.** 프롬프트가 탐지 결과를 참고로
    주므로 모델이 그 종류를 쓸 가능성은 높지만, 그걸 **요구하면** 안 된다. 예전에
    소견이 탐지된 결함 종류를 포함하지 않으면 거부했는데, 그러면 "사진에서 그 결함은
    확인되지 않음"이라는 정직한 답이 걷어차이고 규칙 기반으로 강등된다. 환각을 막으려던
    장치가 오히려 탐지기 오탐을 정당화하도록 강제하고 있었다. 탐지 결과를 주는 지금은
    이 검사가 없는 것이 더 중요하다 — 라벨을 주면서 동의까지 요구하면 반대 의견을
    낼 길이 아예 없어진다.

    면적 퍼센트 강제도 뺐다. 같은 사진에서 30%/15%/5%로 흔들려 신호가 아니라
    노이즈였다.

    남긴 검사는 문체와 **거리 환각**이다. `build_photo_payload` 가 거리를 주지 않으므로
    원칙적으로 거리가 나올 이유가 없지만, 나온다면 이 결함의 위치와 같을 때만 통과시킨다.
    """
    payload = first_json_object(text)
    if payload is None:
        raise ValueError("응답에서 JSON 묘사를 찾지 못함")

    description = str(payload.get("description", "")).strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise ValueError("사진 묘사가 비었거나 너무 짧음")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError("사진 묘사가 길이 제한을 넘음")

    vague = [word for word in _VAGUE_QUANTIFIERS if word in description]
    if vague:
        raise ValueError(f"사진 묘사에 모호한 표현이 있음: {vague}")

    permitted = {f"{defect.position_m:g}", f"{defect.position_m:.1f}", str(int(defect.position_m))}
    _reject_shared_style_violations(description, permitted)
    return description
