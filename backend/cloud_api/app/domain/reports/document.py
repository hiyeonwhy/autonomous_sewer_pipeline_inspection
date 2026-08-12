"""보고서 양식 렌더링(HTML / PDF).

같은 템플릿 하나로 두 결과를 만든다.
- `render_report_document`: 미리보기·디버깅용 HTML.
- `render_report_pdf`: WeasyPrint로 A4 PDF. 프론트의 「PDF 내려받기」가 쓰는 경로다.

A4 그대로 나오도록 인쇄 우선 CSS를 쓴다. PDF에 한글이 빈 사각형으로 나오지 않도록
컨테이너에 한글 폰트(fonts-nanum)가 설치돼 있어야 한다(Dockerfile 참고).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from app.core.config import get_settings
from app.domain.reports.schema import DEFECTS_PER_PAGE

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"

DEFECT_TYPE_LABELS: dict[str, str] = {
    "CRACK": "균열",
    "DEBRIS": "퇴적물",
    "ROOT": "뿌리 침투",
    "BLOCKAGE": "막힘",
    "SURFACE_DAMAGE": "표면 손상",
    "OTHER": "기타 결함",
}

SEVERITY_LABELS: dict[str, str] = {
    "CRITICAL": "심각",
    "WARNING": "주의",
    "NORMAL": "경미",
}

# 템플릿의 .sev-* 클래스와 짝. 심각=빨강, 주의=앰버, 경미=본문색.
SEVERITY_CLASSES: dict[str, str] = {
    "CRITICAL": "critical",
    "WARNING": "warning",
    "NORMAL": "normal",
}

FAILURE_LABELS: dict[str, str] = {
    "NO_SNAPSHOT": "결함 사진 미등록",
    "MODEL_UNAVAILABLE": "판독 모델 미로드",
    "BUDGET_EXCEEDED": "사진 판독 건수 제한 초과",
    "IMAGE_FETCH_FAILED": "결함 사진 내려받기 실패",
    "VALIDATION_REJECTED": "판독 결과 검증 실패",
    "INFERENCE_FAILED": "판독 모델 실행 오류",
}

_environment = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _cell(value: object, suffix: str = "") -> Markup:
    """관로 제원 칸. 값이 없으면 '미입력'으로 표시한다.

    HTML을 직접 만들므로 Markup으로 감싸야 autoescape가 태그를 문자로 바꾸지 않는다.
    값은 `Markup.format`이 이스케이프하므로 DB 문자열이 그대로 주입되지 않는다.
    """
    if value is None or value == "":
        return Markup('<td class="empty">미입력</td>')
    rendered = f"{value:g}" if isinstance(value, float) else str(value)
    return Markup('<td class="num">{}{}</td>').format(rendered, suffix)


_environment.globals["cell"] = _cell


def _percent(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    ratio = float(value)
    if ratio <= 1.0:
        ratio *= 100
    return f"{ratio:.0f}%"


def build_rows(
    observations: list[dict[str, Any]], defects: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """양식에 찍을 결함 행을 만든다.

    판독 소견(observations)이 저장돼 있으면 그것을 쓰고, 아직 요약을 돌리지 않았으면
    결함 레코드만으로 기본 행을 만든다. 즉 llm_report 없이도 양식이 나온다.
    """
    by_id = {defect.get("id"): defect for defect in defects}
    rows: list[dict[str, Any]] = []

    sources: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if observations:
        for observation in observations:
            defect = by_id.get(observation.get("defect_id")) or {}
            sources.append((observation, defect))
    else:
        for defect in sorted(defects, key=lambda item: item.get("approx_position_m") or 0):
            sources.append(({}, defect))

    for observation, defect in sources:
        position = observation.get("position_m")
        if position is None:
            position = defect.get("approx_position_m") or 0.0
        defect_type = observation.get("defect_type") or defect.get("defect_type") or "OTHER"
        severity = observation.get("severity") or defect.get("severity") or "NORMAL"
        snapshot_path = defect.get("snapshot_path")

        failure_code = observation.get("failure_code")
        failure_note = None
        if failure_code:
            failure_note = f"{failure_code} · {FAILURE_LABELS.get(failure_code, '판독 미수행')}"

        # 로봇에 주행계가 없어 위치가 늘 0m 로 온다. 0m 를 그대로 실으면 「입구에서
        # 발견」이라는 없는 사실이 기록되므로, 실측값이 있을 때만 위치를 쓴다.
        has_position = float(position) > 0

        description = observation.get("description")
        if not description:
            where = f"{position:g}m 지점에서 " if has_position else ""
            description = (
                f"{where}"
                f"{DEFECT_TYPE_LABELS.get(defect_type, '기타 결함')} 탐지됨. "
                "판독 소견 미생성."
            )

        # 사진 묘사는 탐지 결과를 참고 자료로만 준 뒤 사진에서 확인하게 한 문장이라
        # 위 서술과 어긋날 수 있다 — 탐지된 결함이 사진에서 확인되지 않는 경우다.
        # 어긋나는 것 자체가 검수 신호이므로 합치지 않고 따로 싣는다.
        photo_description = observation.get("photo_description")
        if not photo_description:
            photo_description = failure_note and "사진 판독 미수행." or "사진 묘사 미생성."

        rows.append(
            {
                "position_label": f"{float(position):g}m" if has_position else "",
                "type_label": DEFECT_TYPE_LABELS.get(defect_type, "기타 결함"),
                "severity_label": SEVERITY_LABELS.get(severity, "경미"),
                "severity_class": SEVERITY_CLASSES.get(severity, "normal"),
                "confidence_label": _percent(defect.get("confidence")),
                # 상대 경로를 쓰면 호스트가 바뀌어도 사진이 깨지지 않는다.
                "image_path": snapshot_path,
                "description": description,
                "photo_description": photo_description,
                "source": observation.get("source") or "metadata",
                "failure_code": failure_note,
            }
        )

    return rows


def resolve_media_file(web_path: object) -> Path | None:
    """`/media/...` 웹 경로를 실제 디스크 경로로 바꾼다. 못 찾으면 None.

    media_root 밖을 가리키는 경로(`..`, 퍼센트 인코딩된 `%2e%2e` 포함)는 거부한다.
    """
    if not isinstance(web_path, str) or not web_path:
        return None

    # 절대 URL로 올 수도 있으니 스킴을 떼고 경로만 본다.
    path = unquote(urlsplit(web_path).path if "://" in web_path else web_path)
    if not path.startswith("/media/"):
        return None

    media_root = Path(get_settings().media_root).resolve()
    candidate = (media_root / path[len("/media/") :]).resolve()
    if not candidate.is_relative_to(media_root) or not candidate.is_file():
        return None
    return candidate


def render_report_document(
    report: dict[str, Any],
    defects: list[dict[str, Any]],
    *,
    local_media: bool = False,
) -> str:
    """양식 HTML을 만든다.

    `local_media=True`(PDF 렌더용)면 사진 경로를 실제 파일의 `file://` URL로 바꾼다.
    WeasyPrint는 `file:` URL을 만나면 url_fetcher가 준 데이터를 버리고 URL에서 딴
    경로로 나중에 다시 읽는다(`urls.py`의 `Response.path` → `LazyLocalImage`).
    그래서 fetcher로 가로채는 방식은 통하지 않고, URL 자체가 실제 경로여야 한다.
    """
    rows = build_rows(report.get("observations") or [], defects)
    if local_media:
        for row in rows:
            resolved = resolve_media_file(row["image_path"])
            # 못 찾으면 None으로 둬야 템플릿이 「결함 사진 없음」을 낸다.
            # 웹 경로를 그대로 남기면 빈 사각형에 alt 텍스트만 찍혀 오해를 부른다.
            row["image_path"] = resolved.as_uri() if resolved else None

    photo_pages = max(1, math.ceil(len(rows) / DEFECTS_PER_PAGE))

    template = _environment.get_template("report_document.html")
    return template.render(
        report=report,
        rows=rows,
        defects_per_page=DEFECTS_PER_PAGE,
        page_no=1,
        page_total=1 + photo_pages,
        vision_count=sum(1 for row in rows if row["source"] == "vision"),
    )


def _offline_url_fetcher(url: str) -> dict[str, Any]:
    """네트워크 접근을 막는다. 로컬 파일만 허용한다.

    HTTP로 무언가를 가져오면 안 된다. uvicorn 워커가 1개라 요청 처리 중에 자기
    서버로 동기 요청을 보내면 그 요청을 받아줄 워커가 없어 교착에 빠진다. 사진은
    `render_report_document(local_media=True)`가 이미 실제 파일 경로로 바꿔 놓으므로
    여기서 원격 자원을 받아올 일은 없다.
    """
    from weasyprint.urls import default_url_fetcher

    if urlsplit(url).scheme in ("http", "https"):
        raise ValueError(f"보고서 렌더 중 원격 자원 요청은 허용되지 않습니다: {url}")
    return default_url_fetcher(url)


def render_report_pdf(report: dict[str, Any], defects: list[dict[str, Any]]) -> bytes:
    """양식 HTML을 A4 PDF로 변환한다.

    WeasyPrint 호출은 blocking이므로 라우터에서 스레드풀로 넘겨야 한다.
    """
    from weasyprint import HTML

    html = render_report_document(report, defects, local_media=True)
    return HTML(
        string=html,
        base_url=Path(get_settings().media_root).resolve().as_uri() + "/",
        url_fetcher=_offline_url_fetcher,
    ).write_pdf()
