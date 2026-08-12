from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.agent_auth import require_agent_publish_key
from app.core.config import get_settings
from app.domain.media.schema import CaptureUploadResponse

router = APIRouter(prefix="/api/media", tags=["media"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@router.post(
    "/captures",
    response_model=CaptureUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_agent_publish_key)],
)
async def upload_capture(
    file: UploadFile = File(...),
) -> CaptureUploadResponse:
    extension = ALLOWED_IMAGE_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="JPEG, PNG, WebP 이미지만 업로드할 수 있습니다.",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="이미지는 10MB 이하여야 합니다.",
        )

    output_directory = Path(get_settings().media_root) / "defects"
    output_directory.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}{extension}"
    output_path = output_directory / filename
    output_path.write_bytes(content)

    return CaptureUploadResponse(
        snapshot_path=f"/media/defects/{filename}",
    )
