from pydantic import BaseModel


class CaptureUploadResponse(BaseModel):
    snapshot_path: str
