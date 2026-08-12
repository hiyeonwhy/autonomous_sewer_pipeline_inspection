"""LLM 기반 하수관로 점검 보고서 요약 모듈.

`ReportSummaryService`가 진입점이다. 결함 통계와 상태등급은 코드가 계산하고,
서술형 평가 문단만 로컬 Qwen이 작성한다. 모델을 쓸 수 없으면 규칙 기반 요약으로
자동 강등되므로 보고서 생성 자체가 막히지 않는다.
"""

from .cloud_client import HttpCloudClient, cloud_api_base_url
from .facts import build_session_facts, compute_grades
from .fallback import render_fallback_narrative
from .models import (
    DefectFact,
    DefectType,
    ReportNarrative,
    ReportSummary,
    SessionFacts,
    Severity,
    StateGrades,
)
from .qwen_provider import QwenReportProvider, parse_narrative_text
from .service import ReportSummaryService

__all__ = [
    "DefectFact",
    "DefectType",
    "HttpCloudClient",
    "QwenReportProvider",
    "ReportNarrative",
    "ReportSummary",
    "ReportSummaryService",
    "SessionFacts",
    "Severity",
    "StateGrades",
    "build_session_facts",
    "cloud_api_base_url",
    "compute_grades",
    "parse_narrative_text",
    "render_fallback_narrative",
]
