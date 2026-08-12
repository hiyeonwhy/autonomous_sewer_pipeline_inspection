from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domain.agent_telemetry import command_router
from app.domain.agent_telemetry.command_schema import (
    AgentCommandAck,
    AgentCommandCreate,
    AgentCommandStatus,
    AgentCommandType,
)


class FakeSessions:
    async def find_one(self, query):
        return {"id": query["id"], "status": "RUNNING", "robot_id": 1}


class FakeCommands:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return deepcopy(document)
        return None

    async def insert_one(self, document):
        self.documents.append(deepcopy(document))

    async def find_one_and_update(self, query, update, **_kwargs):
        now = datetime.now(timezone.utc)
        candidates = []
        for document in self.documents:
            if document.get("agent_id") != query.get("agent_id"):
                continue
            if "command_id" in query and document.get("command_id") != query["command_id"]:
                continue
            if "status" in query and document.get("status") != query["status"]:
                continue
            if "claim_token" in query and document.get("claim_token") != query["claim_token"]:
                continue
            if "expires_at" in query and document["expires_at"] <= now:
                continue
            if "$or" in query:
                pending = document["status"] == "PENDING"
                lease_expired = (
                    document["status"] == "CLAIMED"
                    and document.get("claimed_until") is not None
                    and document["claimed_until"] <= now
                )
                if not (pending or lease_expired):
                    continue
            candidates.append(document)
        if not candidates:
            return None
        document = sorted(candidates, key=lambda item: item["created_at"])[0]
        document.update(update["$set"])
        return deepcopy(document)


def request_with(collection: FakeCommands | None = None):
    database = SimpleNamespace(
        sessions=FakeSessions(),
        agent_commands=collection or FakeCommands(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))


def test_start_command_is_idempotent_claimed_once_and_acknowledged() -> None:
    commands = FakeCommands()
    request = request_with(commands)
    payload = AgentCommandCreate(
        request_id="request-start-1",
        session_id=152,
        operation_id="inspection-152",
        area="광주광역시 광산구 하남동",
    )

    created = asyncio.run(command_router.create_agent_command(payload, request))
    duplicate = asyncio.run(command_router.create_agent_command(payload, request))
    claimed = asyncio.run(command_router.claim_agent_command(request, "laptop-4070"))
    acked = asyncio.run(command_router.acknowledge_agent_command(
        created.command_id,
        AgentCommandAck(
            agent_id="laptop-4070",
            claim_token=claimed.claim_token,
            status=AgentCommandStatus.SUCCEEDED,
            message="local Agent accepted START",
        ),
        request,
    ))

    assert duplicate.command_id == created.command_id
    assert claimed.command_id == created.command_id
    assert claimed.status is AgentCommandStatus.CLAIMED
    assert acked.status is AgentCommandStatus.SUCCEEDED
    assert len(commands.documents) == 1

    repeated_ack = asyncio.run(command_router.acknowledge_agent_command(
        created.command_id,
        AgentCommandAck(
            agent_id="laptop-4070",
            claim_token=claimed.claim_token,
            status=AgentCommandStatus.SUCCEEDED,
            message="local Agent accepted START",
        ),
        request,
    ))
    assert repeated_ack.status is AgentCommandStatus.SUCCEEDED


def test_reused_request_id_with_conflicting_payload_is_rejected() -> None:
    commands = FakeCommands()
    request = request_with(commands)
    original = AgentCommandCreate(
        request_id="request-conflict",
        session_id=152,
        operation_id="inspection-152",
        area="하남동",
    )
    conflicting = original.model_copy(update={"area": "수완동"})

    asyncio.run(command_router.create_agent_command(original, request))
    with pytest.raises(HTTPException) as error:
        asyncio.run(command_router.create_agent_command(conflicting, request))

    assert error.value.status_code == 409


def test_stale_claim_token_cannot_ack_a_reclaimed_command() -> None:
    commands = FakeCommands()
    request = request_with(commands)
    created = asyncio.run(command_router.create_agent_command(
        AgentCommandCreate(
            request_id="request-stale-claim",
            session_id=152,
            operation_id="inspection-152",
            area="하남동",
        ),
        request,
    ))
    first_claim = asyncio.run(command_router.claim_agent_command(request, "laptop-4070"))
    commands.documents[0]["claimed_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    second_claim = asyncio.run(command_router.claim_agent_command(request, "laptop-4070"))

    assert first_claim.claim_token != second_claim.claim_token
    with pytest.raises(HTTPException) as error:
        asyncio.run(command_router.acknowledge_agent_command(
            created.command_id,
            AgentCommandAck(
                agent_id="laptop-4070",
                claim_token=first_claim.claim_token,
                status=AgentCommandStatus.SUCCEEDED,
                message="stale",
            ),
            request,
        ))
    assert error.value.status_code == 409

    accepted = asyncio.run(command_router.acknowledge_agent_command(
        created.command_id,
        AgentCommandAck(
            agent_id="laptop-4070",
            claim_token=second_claim.claim_token,
            status=AgentCommandStatus.FAILED,
            message="new lease",
        ),
        request,
    ))
    assert accepted.status is AgentCommandStatus.FAILED


def test_start_and_stop_are_distinct_idempotent_commands() -> None:
    commands = FakeCommands()
    request = request_with(commands)
    common = {
        "session_id": 152,
        "operation_id": "inspection-152",
        "area": "광주광역시 광산구 하남동",
    }

    started = asyncio.run(command_router.create_agent_command(
        AgentCommandCreate(
            command_type=AgentCommandType.START,
            request_id="request-start-2",
            **common,
        ),
        request,
    ))
    stopped = asyncio.run(command_router.create_agent_command(
        AgentCommandCreate(
            command_type=AgentCommandType.STOP,
            request_id="request-stop-1",
            **common,
        ),
        request,
    ))

    assert started.command_id != stopped.command_id
    assert len(commands.documents) == 2


def test_expired_command_is_not_claimed() -> None:
    commands = FakeCommands()
    commands.documents.append({
        "command_id": "expired",
        "command_type": "START",
        "agent_id": "laptop-4070",
        "request_id": "request-expired",
        "session_id": 1,
        "operation_id": "expired-op",
        "area": "expired",
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        "claimed_at": None,
        "claimed_until": None,
        "completed_at": None,
        "message": "",
    })

    result = asyncio.run(command_router.claim_agent_command(request_with(commands), "laptop-4070"))

    assert result.status_code == 204
