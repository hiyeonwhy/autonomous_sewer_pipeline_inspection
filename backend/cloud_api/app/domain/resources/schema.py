from pydantic import BaseModel, Field


class RobotResponse(BaseModel):
    id: int
    name: str
    model: str
    status: str


class OperatorResponse(BaseModel):
    id: int
    name: str
    role: str


class OperatorCodeRequest(BaseModel):
    code: str = Field(max_length=128)
