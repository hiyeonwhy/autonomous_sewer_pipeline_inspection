import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pymongo import ReturnDocument
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.sequence import get_next_sequence
from app.domain.defects.schema import ReviewStatus
from app.domain.reports.document import render_report_document, render_report_pdf
from app.domain.reports.schema import (
    DEFAULT_PIPE_FIELDS,
    DEFAULT_SURVEY_PURPOSE,
    ReportResponse,
    ReportSummaryUpdateRequest,
)
from app.domain.reports.summary import generate_report_summary, to_update_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["reports"])

# 저장은 UTC로 하되, 사람이 읽는 날짜(점검일자)만 한국 시각으로 환산한다.
KST = timezone(timedelta(hours=9))


def convert_report(document: dict) -> ReportResponse:
    document.pop("_id", None)

    # report_no 도입 이전에 만들어진 문서도 읽을 수 있어야 한다. 저장값을 바꾸지 않고
    # 응답에서만 생성 일자와 id로 번호를 유추한다.
    if not document.get("report_no"):
        generated_at = document.get("generated_at")
        day = generated_at.strftime("%Y%m%d") if isinstance(generated_at, datetime) else "00000000"
        document["report_no"] = f"{day}-{int(document.get('id', 0)):03d}"

    # `R-` 접두사를 쓰던 시절의 보고서는 저장값에 그대로 남아 있다. 응답에서 떼어
    # 표시를 한 가지로 만든다 — 저장값은 건드리지 않는다(발번 이력이므로).
    document["report_no"] = str(document["report_no"]).removeprefix("R-")

    # 세션에 관로 제원이 없어 비어 있는 칸만 메운다. 저장된 값이 있으면 손대지 않는다.
    for field, default in DEFAULT_PIPE_FIELDS.items():
        if document.get(field) is None:
            document[field] = default

    return ReportResponse.model_validate(document)


def format_duration(started_at: object, ended_at: object) -> str:
    """주행 시간 HH:MM:SS. 종료 전이거나 시각이 없으면 00:00:00."""
    if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
        return "00:00:00"

    elapsed = int((ended_at - started_at).total_seconds())
    if elapsed < 0:
        return "00:00:00"

    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def inspection_date_of(session_document: dict, *, fallback: datetime) -> str:
    """점검일자 — **조사를 한 날**. 보고서를 만든 날이 아니다.

    예전에는 보고서 생성 시각을 적었다. 그러면 8월 3일에 돈 세션을 8월 7일에 뽑았을 때
    「점검일자 2026-08-07」이 되어 사실과 어긋난다. 보고서 탭 달력도 이 값으로 날짜를
    묶으므로 보고서가 엉뚱한 날에 걸린다.

    한국 날짜로 적는다. DB는 UTC로 저장하는데, 오전에 점검하면(예: 08시 KST =
    전날 23시 UTC) UTC 날짜가 하루 밀린다. 사람이 「점검일자」에서 기대하는 것은
    현장에 나간 날이므로 KST로 환산한 뒤 날짜만 떼어낸다.
    """
    started_at = session_document.get("started_at") or fallback
    if started_at.tzinfo is None:
        # pymongo는 UTC 시각을 tz 없는 datetime으로 돌려준다.
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at.astimezone(KST).strftime("%Y-%m-%d")


async def issue_report_no(database, *, issued_at: datetime) -> str:
    """YYYYMMDD-NNN. 일자별 카운터라 사람이 보고 언제 것인지 바로 안다.

    `counters._id`가 문자열이므로 일자를 시퀀스 이름에 넣어 기존 발번기를 그대로
    재사용한다. `id`(정수 PK)는 그대로 두고 표시용 번호만 따로 발급한다.

    예전에는 `R-` 접두사를 붙였는데 뺐다. 화면 세 곳에서 이미 정규식으로 떼고 있어
    저장값과 표시값이 달랐다 — 표시가 정답이므로 발번 자체를 그렇게 바꿨다.
    """
    # **한국 날짜**로 발번한다. `issued_at` 은 UTC 라 그대로 쓰면 KST 자정~오전 9시에
    # 만든 보고서가 전날 번호를 받는다 — 실제로 08-10 새벽 1시 보고서에 20260809 가
    # 붙어, 같은 문서 안에서 점검일자(08-10)와 번호가 어긋났다.
    moment = issued_at if issued_at.tzinfo else issued_at.replace(tzinfo=timezone.utc)
    day = moment.astimezone(KST).strftime("%Y%m%d")
    sequence = await get_next_sequence(database=database, sequence_name=f"reports:{day}")
    return f"{day}-{sequence:03d}"


@router.get("/reports", response_model=list[ReportResponse])
async def get_reports(request: Request) -> list[ReportResponse]:
    database = request.app.state.database
    documents = await database.reports.find({}).sort("generated_at", -1).to_list(length=100)
    return [convert_report(document) for document in documents]


@router.get("/reports/{report_id}", response_model=ReportResponse)
async def get_report(report_id: int, request: Request) -> ReportResponse:
    database = request.app.state.database
    document = await database.reports.find_one({"id": report_id})
    if document is None:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    return convert_report(document)


@router.patch("/reports/{report_id}", response_model=ReportResponse)
async def update_report_summary(
    report_id: int,
    payload: ReportSummaryUpdateRequest,
    request: Request,
) -> ReportResponse:
    """llm_report가 만든 서술형 요약을 보고서에 붙인다."""
    database = request.app.state.database

    update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="수정할 데이터가 없습니다.")

    document = await database.reports.find_one_and_update(
        {"id": report_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    return convert_report(document)


async def load_report_context(
    database, report_id: int
) -> tuple[dict, list[dict]]:
    """보고서와 그 세션의 결함을 함께 읽는다."""
    document = await database.reports.find_one({"id": report_id})
    if document is None:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    report = convert_report(document).model_dump()
    defects = (
        await database.defects.find(
            {"session_id": report["session_id"], "review_status": {"$ne": ReviewStatus.REJECTED.value}}
        )
        .sort("approx_position_m", 1)
        .to_list(length=200)
    )
    for defect in defects:
        defect.pop("_id", None)
    return report, defects


# 같은 보고서에 대한 요약 생성이 겹치지 않게 한다. 프론트에서 「보고서 출력」을
# 두 번 누르면 LLM 호출이 두 벌 나가고 나중 것이 앞의 것을 덮어쓴다.
_summary_locks: dict[int, asyncio.Lock] = {}


def summary_lock(report_id: int) -> asyncio.Lock:
    return _summary_locks.setdefault(report_id, asyncio.Lock())


async def ensure_report_summary(
    database, report_id: int, report: dict, defects: list[dict], *, refresh: bool = False
) -> dict:
    """서술형 요약이 없으면 지금 만들어 저장하고, 채워진 보고서를 돌려준다.

    기본은 한 번만 만들고 그 뒤로는 저장된 값을 쓴다. 다시 만들려면 `?refresh=true`를
    붙인다 — 프론트의 「보고서 생성」 버튼이 이 경로를 쓴다.

    예전에는 `LLM_ALWAYS_REFRESH` 설정으로도 갱신할 수 있었는데 없앴다. 설정으로 켜면
    HTML 미리보기까지 매번 LLM을 태워 0.07초에서 35초가 된다 — 두 엔드포인트가 이
    함수를 공유하기 때문이다. 갱신 경로를 쿼리 파라미터 하나로 두는 편이 낫다.

    생성이 실패해도 원래 보고서를 그대로 돌려준다 — 요약이 없다고 PDF까지 막을
    이유는 없고, 양식은 요약 없이도 렌더된다(`document.build_rows`).
    """
    if report.get("assessment") and not refresh:
        return report

    async with summary_lock(report_id):
        # 락을 기다리는 동안 다른 요청이 채웠을 수 있다.
        if not refresh:
            current = await database.reports.find_one({"id": report_id})
            if current is not None and current.get("assessment"):
                return convert_report(current).model_dump()

        try:
            # LLM 호출은 블로킹이라 이벤트 루프에서 직접 돌리면 서버가 멈춘다.
            summary = await run_in_threadpool(generate_report_summary, report, defects)
        except Exception:
            logger.exception("report %s: 서술형 요약 생성 실패", report_id)
            return report

        # 사진 판독에 실패한 결함은 보고서 사진 면에서 이미 빠졌다. DB 에도 같은
        # 표시를 남겨야 결함 목록·집계와 어긋나지 않는다. 지우지는 않는다 — 원본
        # 사진과 탐지 기록은 남아야 하고, 판정이 틀렸으면 되살릴 수 있어야 한다.
        if summary.dropped_defect_ids:
            await database.defects.update_many(
                {"id": {"$in": summary.dropped_defect_ids}},
                {"$set": {"review_status": ReviewStatus.REJECTED.value}},
            )

        # 결함 수는 보고서를 만들 때 세어 둔 값이다. 그 뒤로 사진 판독이 결함을 빼면
        # 「전체 결함 10건」인데 목록에는 9건뿐인 상태가 된다. 요약을 만들 때마다 다시
        # 센다 — 뺀 게 없어도 세는 편이 낫다. 점검 중 판독이 뺀 것도 여기서 맞춰진다.
        remaining = await database.defects.count_documents(
            {
                "session_id": report["session_id"],
                "review_status": {"$ne": ReviewStatus.REJECTED.value},
            }
        )
        await database.reports.update_one(
            {"id": report_id}, {"$set": {"total_defect_count": remaining}}
        )

        document = await database.reports.find_one_and_update(
            {"id": report_id},
            {"$set": to_update_payload(summary)},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return report

        logger.info(
            "report %s: 요약 생성 완료 (%s, %s, 사진 판독 %d건, %.0fms)",
            report_id,
            summary.status,
            summary.source_model,
            summary.vision_described_count,
            summary.latency_ms,
        )
        return convert_report(document).model_dump()


@router.get("/reports/{report_id}/document", response_class=HTMLResponse)
async def get_report_document(
    report_id: int,
    request: Request,
    refresh: bool = Query(default=False, description="요약을 다시 생성한다"),
) -> HTMLResponse:
    """양식 HTML. 미리보기·디버깅용이며 PDF는 아래 엔드포인트를 쓴다."""
    database = request.app.state.database
    report, defects = await load_report_context(database, report_id)
    report = await ensure_report_summary(database, report_id, report, defects, refresh=refresh)
    return HTMLResponse(render_report_document(report, defects))


@router.get("/reports/{report_id}/document.pdf")
async def get_report_pdf(
    report_id: int,
    request: Request,
    refresh: bool = Query(default=False, description="요약을 다시 생성한다"),
) -> Response:
    """양식을 A4 PDF로 내려준다. 프론트의 「보고서 출력」 버튼이 이걸 호출한다.

    요약이 아직 없으면 여기서 LLM을 돌려 채운 뒤 렌더한다. 그래서 첫 다운로드는
    모델 응답만큼 느리고, 같은 보고서의 두 번째 다운로드부터는 저장된 요약을 쓴다.
    """
    database = request.app.state.database
    report, defects = await load_report_context(database, report_id)
    report = await ensure_report_summary(database, report_id, report, defects, refresh=refresh)

    # WeasyPrint는 blocking이라 이벤트 루프에서 직접 돌리면 서버가 멈춘다.
    try:
        pdf = await run_in_threadpool(render_report_pdf, report, defects)
    except Exception as error:  # 렌더 실패 원인을 응답에 그대로 담는다
        logger.exception("report %s: PDF 렌더 실패", report_id)
        raise HTTPException(
            status_code=500, detail=f"보고서 PDF 생성에 실패했습니다: {error}"
        ) from error

    filename = f"{report['report_no']}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/sessions/{session_id}/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(session_id: int, request: Request) -> ReportResponse:
    database = request.app.state.database
    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    defects = await database.defects.find(
        {"session_id": session_id, "review_status": {"$ne": ReviewStatus.REJECTED.value}}
    ).to_list(length=100)
    report_id = await get_next_sequence(database=database, sequence_name="reports")
    robot_document = await database.robots.find_one({"id": session_document.get("robot_id")})
    operator_document = await database.operators.find_one(
        {"id": session_document.get("approved_by")}
    )

    generated_at = datetime.now(timezone.utc)
    pipe = session_document.get("pipe") or {}
    survey = session_document.get("survey") or {}

    document = {
        "id": report_id,
        "report_no": await issue_report_no(database, issued_at=generated_at),
        "session_id": session_id,
        # `region` 은 양식에 직접 실리지 않고 서술형 요약의 주어로만 쓰인다
        # ("{region}의 이번 탐사에서 결함 3건 확인됨"). 점검 위치가 있으면 그걸 쓴다 —
        # 「점검 구역 1」보다 「광주광역시 광산구 하남동」이 읽는 사람에게 쓸모 있다.
        "region": survey.get("survey_location") or f"점검 구역 {session_document.get('district_id', 0)}",
        "survey_location": survey.get("survey_location"),
        "inspection_section": survey.get("inspection_section"),
        "survey_purpose": DEFAULT_SURVEY_PURPOSE,
        "robot_name": robot_document.get("name", "UNKNOWN") if robot_document else "UNKNOWN",
        "robot_model": robot_document.get("model", "UNKNOWN") if robot_document else "UNKNOWN",
        "operator_name": (
            operator_document.get("name", "UNKNOWN") if operator_document else "UNKNOWN"
        ),
        "operator_role": (
            operator_document.get("role", "OPERATOR") if operator_document else "OPERATOR"
        ),
        "inspection_date": inspection_date_of(session_document, fallback=generated_at),
        "duration": format_duration(
            session_document.get("started_at"), session_document.get("ended_at")
        ),
        "pipe_no": pipe.get("pipe_no"),
        "pipe_category": pipe.get("pipe_category"),
        "pipe_type": pipe.get("pipe_type"),
        "diameter_mm": pipe.get("diameter_mm"),
        "drainage_type": pipe.get("drainage_type"),
        "pipe_length_m": pipe.get("pipe_length_m"),
        "total_distance_m": survey.get("total_distance_m"),
        "undriven_distance_m": survey.get("undriven_distance_m"),
        "total_defect_count": len(defects),
        "critical_defect_count": sum(
            1 for defect in defects if defect.get("severity") == "CRITICAL"
        ),
        "summary": ", ".join(
            [
                f"{defect.get('defect_type', 'UNKNOWN')} {defect.get('severity', 'UNKNOWN')}"
                for defect in defects[:3]
            ]
        )
        or "정상",
        "generated_at": generated_at,
    }

    await database.reports.insert_one(document)
    return convert_report(document)
