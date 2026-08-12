from datetime import datetime

from pydantic import BaseModel, Field


class ReconstructionCreateRequest(BaseModel):
    image_count: int = Field(default=0, ge=0)


class ReconstructionUpdateRequest(BaseModel):
    status: str
    stage: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    model_path: str | None = None
    thumbnail_path: str | None = None
    error_message: str | None = None


class ReconstructionResponse(BaseModel):
    id: int
    session_id: int
    status: str
    stage: str
    progress_percent: int
    image_count: int
    model_path: str | None = None
    thumbnail_path: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
