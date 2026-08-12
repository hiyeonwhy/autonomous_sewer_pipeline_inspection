from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AgentCommandType(StrEnum):
    START = "START"
    STOP = "STOP"


class AgentCommandStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentCommandCreate(BaseModel):
    command_type: AgentCommandType = AgentCommandType.START
    agent_id: Literal["laptop-4070"] = "laptop-4070"
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    session_id: int = Field(gt=0)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    area: str = Field(min_length=1, max_length=200)
    expires_in_s: int = Field(default=30, ge=5, le=60)


class AgentCommandAck(BaseModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    claim_token: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: AgentCommandStatus
    message: str = Field(default="", max_length=500)


class AgentCommandResponse(BaseModel):
    command_id: str
    command_type: AgentCommandType
    agent_id: str
    request_id: str
    session_id: int
    operation_id: str
    area: str
    status: AgentCommandStatus
    created_at: datetime
    expires_at: datetime
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    message: str = ""


class AgentCommandClaimResponse(AgentCommandResponse):
    claim_token: str
