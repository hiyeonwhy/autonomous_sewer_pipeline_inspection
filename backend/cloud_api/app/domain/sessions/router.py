import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pymongo import DESCENDING, ReturnDocument

from app.core.operator_auth import OperatorCodeError, verify_operator_passcode
from app.db.sequence import get_next_sequence
from app.domain.sessions.schema import (
    DefectSummary,
    PipeSpec,
    SessionCreateRequest,
    SessionResponse,
    SessionStatus,
    SessionUpdateRequest,
    SurveyRun,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sessions",
    tags=["sessions"],
)


def convert_session(document: dict) -> SessionResponse:
    """MongoDB 내부 _id를 제거하고 프론트 응답 형식으로 변환합니다."""

    document.pop("_id", None)
    return SessionResponse.model_validate(document)


# 클라이언트가 보낸 시작 시각을 어디까지 믿을지. 브라우저와 서버 시계가 몇 초
# 어긋나는 것은 흔하므로 넉넉히 두되, 분 단위로 틀어진 값은 받지 않는다.
MAX_CLIENT_CLOCK_SKEW = timedelta(minutes=5)

# 로봇 대장에 실려 있어도 이 상태일 때만 점검에 투입할 수 있다.
ROBOT_READY_STATUS = "READY"

# 점검이 정상 종료되지 않으면 세션이 RUNNING 인 채로 남는다. 브라우저를 닫거나
# Agent 가 죽으면 아무도 COMPLETED 로 바꿔 주지 않기 때문이다. 실제로 125시간
# 묵은 것까지 36건이 쌓여 있었고, 「점검 중인 로봇」 검사가 그걸 살아 있는
# 점검으로 보아 새 점검을 영영 막았다.
#
# 이 시간이 지나도록 RUNNING 이면 버려진 것으로 본다. 한 번의 점검이 이보다 길어질
# 일은 없다(관측된 최장이 몇 분이다). 막는 대신 ABORTED 로 닫아 스스로 낫게 한다.
STALE_RUNNING_SESSION_AFTER = timedelta(minutes=30)


def resolve_started_at(requested: datetime | None) -> datetime:
    """운영자가 「점검 시작」을 누른 시각. 못 믿을 값이면 서버 시계로 갈음한다.

    화면이 보여 준 시각과 기록이 같아야 해서 클라이언트 값을 받지만, 그대로 믿으면
    시계가 틀린 기기 하나가 점검일자와 보고서 발번을 통째로 어긋나게 만든다.
    서버 시계와 5분 넘게 차이 나거나 미래면 버린다.
    """
    now = datetime.now(timezone.utc)
    if requested is None:
        return now
    moment = requested if requested.tzinfo else requested.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if abs(moment - now) > MAX_CLIENT_CLOCK_SKEW:
        logger.warning(
            "세션 시작 시각이 서버 시계와 %s 어긋나 무시함: %s",
            abs(moment - now),
            moment.isoformat(),
        )
        return now
    return moment


async def guard_inspection_start(
    database,
    *,
    operator_id: int | None,
    operator_code: str | None,
    robot_id: int,
) -> None:
    """점검을 시작해도 되는지 서버가 판단한다. 안 되면 403 으로 끊는다.

    같은 검사를 화면에서도 하지만 그건 즉시 알려 주기 위한 것이고, **판단의 근거는
    여기다.** 화면 검사만 있으면 개발자 도구로 이 API 를 직접 불러 지나갈 수 있다.

    세 가지를 본다.

      1. 승인 운영자의 확인 코드 — 시연 중 남이 임의로 점검을 거는 것을 막는다.
      2. 로봇이 쓸 수 있는 상태인가 — 대장에 있어도 정비 중이면 못 쓴다.
      3. 그 로봇이 이미 점검 중인가 — 한 로봇에 세션이 둘이면 텔레메트리와 결함이
         어느 점검 것인지 갈리지 않는다.
    """
    try:
        await verify_operator_passcode(
            database, operator_id=operator_id, code=operator_code
        )
    except OperatorCodeError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(error)
        ) from error

    robot = await database.robots.find_one({"id": robot_id})
    if robot is None or str(robot.get("status") or "").upper() != ROBOT_READY_STATUS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="로봇과 연결되지 않았습니다.",
        )

    # 버려진 세션부터 닫는다. 이걸 안 하면 예전에 죽은 점검 하나가 그 로봇을
    # 영구히 잠가 버린다.
    stale_before = datetime.now(timezone.utc) - STALE_RUNNING_SESSION_AFTER
    closed = await database.sessions.update_many(
        {
            "robot_id": robot_id,
            "status": SessionStatus.RUNNING.value,
            "started_at": {"$lt": stale_before},
        },
        {"$set": {"status": SessionStatus.ABORTED.value, "ended_at": datetime.now(timezone.utc)}},
    )
    if closed.modified_count:
        logger.warning(
            "로봇 %s 의 버려진 점검 세션 %d건을 닫았습니다 (%s 이전 시작).",
            robot_id,
            closed.modified_count,
            stale_before.isoformat(),
        )

    running = await database.sessions.find_one(
        {"robot_id": robot_id, "status": SessionStatus.RUNNING.value}
    )
    if running is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="지금 점검중인 로봇입니다.",
        )


def derive_undriven_distance(pipe: dict, survey: dict) -> dict:
    """미주행거리 = 연장 − 총주행거리.

    호출자가 직접 넣어 준 값은 건드리지 않고, 연장과 총주행거리가 모두 있을 때만
    비어 있는 미주행거리를 채웁니다.
    """
    if survey.get("undriven_distance_m") is not None:
        return survey

    length = pipe.get("pipe_length_m")
    driven = survey.get("total_distance_m")
    if length is None or driven is None:
        return survey

    return {**survey, "undriven_distance_m": round(max(0.0, length - driven), 3)}


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: SessionCreateRequest,
    request: Request,
) -> SessionResponse:
    database = request.app.state.database

    # 번호를 뽑기 전에 막는다. 통과 못 할 요청에 발번을 써 버리면 세션 번호에 구멍이 난다.
    await guard_inspection_start(
        database,
        operator_id=payload.approved_by,
        operator_code=payload.operator_code,
        robot_id=payload.robot_id,
    )

    session_id = await get_next_sequence(
        database=database,
        sequence_name="sessions",
    )

    pipe = payload.pipe or PipeSpec()
    survey = payload.survey or SurveyRun()
    started_at = resolve_started_at(payload.started_at)

    document = {
        "id": session_id,
        "district_id": payload.district_id,
        "priority_score_id": payload.priority_score_id,
        "robot_id": payload.robot_id,
        "approved_by": payload.approved_by,
        "mode": payload.mode.value,
        "status": SessionStatus.RUNNING.value,
        "started_at": started_at,
        "ended_at": None,
        "defect_summary": DefectSummary().model_dump(),
        "pipe": pipe.model_dump(),
        "survey": derive_undriven_distance(pipe.model_dump(), survey.model_dump()),
    }

    await database.sessions.insert_one(document)

    return convert_session(document)


@router.get(
    "",
    response_model=list[SessionResponse],
)
async def get_sessions(
    request: Request,
) -> list[SessionResponse]:
    database = request.app.state.database

    cursor = (
        database.sessions
        .find({})
        .sort("started_at", DESCENDING)
        .limit(100)
    )

    documents = await cursor.to_list(length=100)

    return [
        convert_session(document)
        for document in documents
    ]


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
)
async def get_session(
    session_id: int,
    request: Request,
) -> SessionResponse:
    database = request.app.state.database

    document = await database.sessions.find_one(
        {"id": session_id}
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="점검 세션을 찾을 수 없습니다.",
        )

    return convert_session(document)


@router.patch(
    "/{session_id}",
    response_model=SessionResponse,
)
async def update_session(
    session_id: int,
    payload: SessionUpdateRequest,
    request: Request,
) -> SessionResponse:
    database = request.app.state.database

    update_data = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    if payload.status is not None:
        update_data["status"] = payload.status.value

    if payload.defect_summary is not None:
        update_data["defect_summary"] = (
            payload.defect_summary.model_dump()
        )

    # pipe/survey는 점 표기로 펼쳐 부분 수정이 나머지 필드를 지우지 않게 한다.
    for group in ("pipe", "survey"):
        nested = update_data.pop(group, None)
        if not nested:
            continue
        for key, value in nested.items():
            update_data[f"{group}.{key}"] = value

    if (
        update_data.get("status")
        in {
            SessionStatus.COMPLETED.value,
            SessionStatus.ABORTED.value,
        }
        and "ended_at" not in update_data
    ):
        update_data["ended_at"] = datetime.now(timezone.utc)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="수정할 데이터가 없습니다.",
        )

    document = await database.sessions.find_one_and_update(
        {"id": session_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="점검 세션을 찾을 수 없습니다.",
        )

    # 연장과 총주행거리가 서로 다른 요청으로 채워질 수 있어 병합 뒤에 계산한다.
    survey = derive_undriven_distance(
        document.get("pipe") or {}, document.get("survey") or {}
    )
    if survey != (document.get("survey") or {}):
        await database.sessions.update_one(
            {"id": session_id},
            {"$set": {"survey.undriven_distance_m": survey["undriven_distance_m"]}},
        )
        document["survey"] = survey

    return convert_session(document)