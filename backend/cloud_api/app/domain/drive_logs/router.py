from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo import DESCENDING, ReturnDocument

from app.core.agent_auth import require_agent_publish_key
from app.db.sequence import get_next_sequence
from app.domain.drive_logs.schema import DriveLogCreateRequest, DriveLogResponse

router = APIRouter(prefix="/api", tags=["drive-logs"])


def convert_drive_log(document: dict) -> DriveLogResponse:
    document.pop("_id", None)
    return DriveLogResponse.model_validate(document)


@router.get("/sessions/{session_id}/drive-logs", response_model=list[DriveLogResponse])
async def get_drive_logs(session_id: int, request: Request) -> list[DriveLogResponse]:
    database = request.app.state.database
    cursor = (
        database.drive_logs.find({"session_id": session_id})
        .sort("logged_at", DESCENDING)
        .limit(100)
    )
    documents = await cursor.to_list(length=100)
    return [convert_drive_log(document) for document in documents]


@router.post(
    "/sessions/{session_id}/drive-logs",
    response_model=DriveLogResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_agent_publish_key)],
)
async def create_drive_log(
    session_id: int,
    payload: DriveLogCreateRequest,
    request: Request,
) -> DriveLogResponse:
    database = request.app.state.database

    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    drive_log_id = await get_next_sequence(database=database, sequence_name="drive_logs")
    document = {
        "id": drive_log_id,
        "session_id": session_id,
        "ultrasonic_distance": payload.ultrasonic_distance,
        "imu_reading": payload.imu_reading,
        "command": payload.command,
        "command_source": payload.command_source,
        "agent_state": payload.agent_state,
        "note": payload.note,
        "logged_at": payload.logged_at or datetime.now(timezone.utc),
    }

    await database.drive_logs.insert_one(document)
    return convert_drive_log(document)
