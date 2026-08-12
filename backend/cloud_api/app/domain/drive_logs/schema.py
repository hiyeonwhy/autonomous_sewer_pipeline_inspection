from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DriveLogCreateRequest(BaseModel):
    ultrasonic_distance: float | None = None
    imu_reading: dict[str, Any] | None = None
    command: str | None = None
    command_source: str | None = None
    agent_state: str | None = None
    note: str | None = None
    logged_at: datetime | None = None


class DriveLogResponse(BaseModel):
    id: int
    session_id: int
    ultrasonic_distance: float | None = None
    imu_reading: dict[str, Any] | None = None
    command: str | None = None
    command_source: str | None = None
    agent_state: str | None = None
    note: str | None = None
    logged_at: datetime
