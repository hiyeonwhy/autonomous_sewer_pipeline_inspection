from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentTelemetryUpdate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=100)
    session_id: int | None = None
    operation_id: str | None = Field(default=None, max_length=200)
    hermes: dict[str, Any] = Field(default_factory=dict)
    mission: dict[str, Any] = Field(default_factory=dict)
    qwen_summary: dict[str, Any] = Field(default_factory=dict)
    drive: dict[str, Any] = Field(default_factory=dict)


class AgentTelemetryResponse(AgentTelemetryUpdate):
    updated_at: datetime
