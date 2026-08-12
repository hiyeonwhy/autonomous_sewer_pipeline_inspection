from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pymongo import ReturnDocument

from app.core.agent_auth import require_agent_publish_key
from app.core.config import get_settings
from app.domain.agent_telemetry.schema import AgentTelemetryResponse, AgentTelemetryUpdate

router = APIRouter(prefix="/api/agent", tags=["agent-telemetry"])
_OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
MAX_FRAME_AGE_SECONDS = 15.0


def _convert(document: dict) -> AgentTelemetryResponse:
    clean = dict(document)
    clean.pop("_id", None)
    return AgentTelemetryResponse.model_validate(clean)


def _validated_identity(session_id: str | None, operation_id: str | None) -> tuple[int, str]:
    if not session_id or not operation_id:
        raise HTTPException(status_code=422, detail="frame 검사 identity가 필요합니다.")
    try:
        numeric_session_id = int(session_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="frame session identity가 올바르지 않습니다.") from error
    if numeric_session_id <= 0 or not _OPERATION_ID.fullmatch(operation_id):
        raise HTTPException(status_code=422, detail="frame 검사 identity가 올바르지 않습니다.")
    return numeric_session_id, operation_id


def _frame_scope_dir(session_id: int, operation_id: str) -> Path:
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return Path(get_settings().media_root) / "agent" / "frames" / f"{session_id}-{digest}"


def _latest_pointer_path(scope: Path) -> Path:
    return scope / "latest.json"


def _live_frame_scope_dir() -> Path:
    return Path(get_settings().media_root) / "agent" / "live"


async def _read_jpeg_frame(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    max_frame_bytes = 8 * 1024 * 1024
    chunks: list[bytes] = []
    received_bytes = 0
    async for chunk in request.stream():
        received_bytes += len(chunk)
        if received_bytes > max_frame_bytes:
            raise HTTPException(status_code=413, detail="YOLO frame이 너무 큽니다.")
        chunks.append(chunk)
    frame = b"".join(chunks)
    if (
        content_type != "image/jpeg"
        or not frame.startswith(b"\xff\xd8")
        or not frame.endswith(b"\xff\xd9")
    ):
        raise HTTPException(status_code=415, detail="JPEG frame만 업로드할 수 있습니다.")
    return frame


def _store_latest_frame(scope: Path, frame: bytes) -> None:
    scope.mkdir(parents=True, exist_ok=True)
    version = f"{uuid4().hex}.jpg"
    destination = scope / version
    temporary = scope / f".{version}.tmp"
    pointer = _latest_pointer_path(scope)
    pointer_temporary = scope / f".{pointer.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(frame)
        temporary.replace(destination)
        pointer_temporary.write_text(
            json.dumps({
                "filename": version,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }),
            encoding="utf-8",
        )
        pointer_temporary.replace(pointer)
    finally:
        temporary.unlink(missing_ok=True)
        pointer_temporary.unlink(missing_ok=True)

    old_frames = sorted(
        (candidate for candidate in scope.glob("*.jpg") if candidate.name != version),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in old_frames[1:]:
        stale.unlink(missing_ok=True)


def _latest_frame_response(scope: Path, missing_detail: str) -> FileResponse:
    try:
        pointer = json.loads(_latest_pointer_path(scope).read_text(encoding="utf-8"))
        filename = str(pointer["filename"])
        uploaded_at = datetime.fromisoformat(str(pointer["uploaded_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=404, detail=missing_detail)
    if uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - uploaded_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < 0.0 or age_seconds > MAX_FRAME_AGE_SECONDS:
        raise HTTPException(status_code=404, detail=f"{missing_detail} (만료)")
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
        raise HTTPException(status_code=404, detail=missing_detail)
    frame = scope / filename
    if not frame.is_file():
        raise HTTPException(status_code=404, detail=missing_detail)
    return FileResponse(
        frame,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.put(
    "/telemetry",
    response_model=AgentTelemetryResponse,
    dependencies=[Depends(require_agent_publish_key)],
)
async def update_agent_telemetry(
    payload: AgentTelemetryUpdate,
    request: Request,
) -> AgentTelemetryResponse:
    document = {
        **payload.model_dump(),
        "updated_at": datetime.now(timezone.utc),
    }
    stored = await request.app.state.database.agent_telemetry.find_one_and_update(
        {"agent_id": payload.agent_id},
        {"$set": document},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _convert(stored or document)


@router.get("/telemetry", response_model=AgentTelemetryResponse)
async def get_agent_telemetry(
    request: Request,
    agent_id: str = "laptop-4070",
) -> AgentTelemetryResponse:
    document = await request.app.state.database.agent_telemetry.find_one(
        {"agent_id": agent_id}
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Agent 상태가 아직 없습니다.")
    return _convert(document)


@router.put(
    "/yolo-frame",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_agent_publish_key)],
)
async def upload_yolo_frame(request: Request) -> dict[str, object]:
    session_id, operation_id = _validated_identity(
        request.headers.get("x-inspection-session-id"),
        request.headers.get("x-inspection-operation-id"),
    )
    frame = await _read_jpeg_frame(request)
    _store_latest_frame(_frame_scope_dir(session_id, operation_id), frame)

    return {
        "ok": True,
        "path": (
            f"/api/agent/yolo-frame.jpg?session_id={session_id}"
            f"&operation_id={operation_id}"
        ),
    }


@router.put(
    "/live-frame",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_agent_publish_key)],
)
async def upload_live_frame(request: Request) -> dict[str, object]:
    frame = await _read_jpeg_frame(request)
    _store_latest_frame(_live_frame_scope_dir(), frame)
    return {"ok": True, "path": "/api/agent/live-frame.jpg"}


@router.get("/live-frame.jpg", response_class=FileResponse)
async def get_live_frame() -> FileResponse:
    return _latest_frame_response(
        _live_frame_scope_dir(),
        "실시간 YOLO frame이 아직 없습니다.",
    )


@router.get("/yolo-frame.jpg", response_class=FileResponse)
async def get_yolo_frame(
    session_id: str,
    operation_id: str,
) -> FileResponse:
    numeric_session_id, validated_operation_id = _validated_identity(session_id, operation_id)
    return _latest_frame_response(
        _frame_scope_dir(numeric_session_id, validated_operation_id),
        "해당 검사의 YOLO frame이 아직 없습니다.",
    )
