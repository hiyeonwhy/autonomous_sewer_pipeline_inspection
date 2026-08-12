"""보고서 서술형 요약 생성.

프론트의 「보고서 출력」이 PDF를 요청할 때, 요약이 비어 있으면 이 모듈이 채운다.
`ai/llm_report`의 사실 정제·등급 산정·출력 검증을 그대로 옮기고 모델 호출만
원격 API로 바꾼 것이다.
"""

from .models import ReportSummary
from .service import ReportSummaryService, generate_report_summary, to_update_payload

__all__ = [
    "ReportSummary",
    "ReportSummaryService",
    "generate_report_summary",
    "to_update_payload",
]
