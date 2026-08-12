from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.domain.agent_telemetry.command_schema import (
    AgentCommandAck,
    AgentCommandClaimResponse,
    AgentCommandCreate,
    AgentCommandResponse,
    AgentCommandStatus,
)

router = APIRouter(prefix="/api/agent/commands", tags=["agent-commands"])
CLAIM_LEASE_SECONDS = 15


def _convert(document: dict) -> AgentCommandResponse:
    clean = dict(document)
    clean.pop("_id", None)
    clean.pop("claimed_until", None)
    clean.pop("claim_token", None)
    return AgentCommandResponse.model_validate(clean)


def _convert_claim(document: dict) -> AgentCommandClaimResponse:
    clean = dict(document)
    clean.pop("_id", None)
    clean.pop("claimed_until", None)
    return AgentCommandClaimResponse.model_validate(clean)


def _same_request(document: dict, payload: AgentCommandCreate) -> bool:
    return all(
        (
            document.get("command_type") == payload.command_type.value,
            document.get("agent_id") == payload.agent_id,
            document.get("request_id") == payload.request_id,
            document.get("session_id") == payload.session_id,
            document.get("operation_id") == payload.operation_id,
            document.get("area") == payload.area,
        )
    )


def _return_existing(document: dict, payload: AgentCommandCreate) -> AgentCommandResponse:
    if not _same_request(document, payload):
        raise HTTPException(status_code=409, detail="request_id가 다른 command payload에 사용되었습니다.")
    return _convert(document)


@router.post(
    "",
    response_model=AgentCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_agent_command(
    payload: AgentCommandCreate,
    request: Request,
) -> AgentCommandResponse:
    database = request.app.state.database
    session = await database.sessions.find_one({"id": payload.session_id})
    if session is None:
        raise HTTPException(status_code=404, detail="점검 세션을 찾을 수 없습니다.")
    if session.get("status") != "RUNNING":
        raise HTTPException(status_code=409, detail="RUNNING 점검 세션만 시작할 수 있습니다.")

    existing = await database.agent_commands.find_one(
        {
            "agent_id": payload.agent_id,
            "request_id": payload.request_id,
        }
    )
    if existing is not None:
        return _return_existing(existing, payload)

    now = datetime.now(timezone.utc)
    document = {
        "command_id": uuid4().hex,
        "command_type": payload.command_type.value,
        "agent_id": payload.agent_id,
        "request_id": payload.request_id,
        "session_id": payload.session_id,
        "operation_id": payload.operation_id,
        "area": payload.area,
        "status": AgentCommandStatus.PENDING.value,
        "created_at": now,
        "expires_at": now + timedelta(seconds=payload.expires_in_s),
        "claimed_at": None,
        "claimed_until": None,
        "completed_at": None,
        "message": "",
    }
    try:
        await database.agent_commands.insert_one(document)
    except DuplicateKeyError:
        existing = await database.agent_commands.find_one(
            {
                "agent_id": payload.agent_id,
                "request_id": payload.request_id,
            }
        )
        if existing is None:
            raise
        return _return_existing(existing, payload)
    return _convert(document)


@router.post(
    "/claim",
    response_model=AgentCommandClaimResponse,
)
async def claim_agent_command(
    request: Request,
    agent_id: str,
) -> AgentCommandClaimResponse | Response:
    now = datetime.now(timezone.utc)
    claimed = await request.app.state.database.agent_commands.find_one_and_update(
        {
            "agent_id": agent_id,
            "expires_at": {"$gt": now},
            "$or": [
                {"status": AgentCommandStatus.PENDING.value},
                {
                    "status": AgentCommandStatus.CLAIMED.value,
                    "claimed_until": {"$lte": now},
                },
            ],
        },
        {
            "$set": {
                "status": AgentCommandStatus.CLAIMED.value,
                "claimed_at": now,
                "claimed_until": now + timedelta(seconds=CLAIM_LEASE_SECONDS),
                "claim_token": uuid4().hex,
            }
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _convert_claim(claimed)


@router.post(
    "/{command_id}/ack",
    response_model=AgentCommandResponse,
)
async def acknowledge_agent_command(
    command_id: str,
    payload: AgentCommandAck,
    request: Request,
) -> AgentCommandResponse:
    if payload.status not in {AgentCommandStatus.SUCCEEDED, AgentCommandStatus.FAILED}:
        raise HTTPException(status_code=422, detail="ACK status는 SUCCEEDED 또는 FAILED여야 합니다.")
    completed = await request.app.state.database.agent_commands.find_one_and_update(
        {
            "command_id": command_id,
            "agent_id": payload.agent_id,
            "claim_token": payload.claim_token,
            "status": AgentCommandStatus.CLAIMED.value,
        },
        {
            "$set": {
                "status": payload.status.value,
                "message": payload.message,
                "completed_at": datetime.now(timezone.utc),
                "claimed_until": None,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if completed is None:
        existing = await request.app.state.database.agent_commands.find_one(
            {
                "command_id": command_id,
                "agent_id": payload.agent_id,
                "claim_token": payload.claim_token,
            }
        )
        if (
            existing is not None
            and existing.get("status") == payload.status.value
            and existing.get("message", "") == payload.message
        ):
            return _convert(existing)
        raise HTTPException(status_code=409, detail="claim된 command를 찾을 수 없습니다.")
    return _convert(completed)


@router.get(
    "/{command_id}",
    response_model=AgentCommandResponse,
)
async def get_agent_command(command_id: str, request: Request) -> AgentCommandResponse:
    document = await request.app.state.database.agent_commands.find_one(
        {"command_id": command_id}
    )
    if document is None:
        raise HTTPException(status_code=404, detail="command를 찾을 수 없습니다.")
    return _convert(document)
