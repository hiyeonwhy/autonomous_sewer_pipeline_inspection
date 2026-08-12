from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pymongo import ReturnDocument

from app.db.sequence import get_next_sequence
from app.domain.reconstructions.schema import (
    ReconstructionCreateRequest,
    ReconstructionResponse,
    ReconstructionUpdateRequest,
)

router = APIRouter(prefix="/api", tags=["reconstructions"])


def convert_reconstruction(document: dict) -> ReconstructionResponse:
    document.pop("_id", None)
    return ReconstructionResponse.model_validate(document)


@router.get("/sessions/{session_id}/reconstructions", response_model=list[ReconstructionResponse])
async def get_reconstructions(session_id: int, request: Request) -> list[ReconstructionResponse]:
    database = request.app.state.database
    documents = await database.reconstructions.find({"session_id": session_id}).to_list(length=100)
    return [convert_reconstruction(document) for document in documents]


@router.post(
    "/sessions/{session_id}/reconstructions",
    response_model=ReconstructionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reconstruction(
    session_id: int,
    payload: ReconstructionCreateRequest,
    request: Request,
) -> ReconstructionResponse:
    database = request.app.state.database

    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    reconstruction_id = await get_next_sequence(database=database, sequence_name="reconstructions")
    document = {
        "id": reconstruction_id,
        "session_id": session_id,
        "status": "PENDING",
        "stage": "INIT",
        "progress_percent": 0,
        "image_count": payload.image_count,
        "model_path": None,
        "thumbnail_path": None,
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "error_message": None,
    }

    await database.reconstructions.insert_one(document)
    return convert_reconstruction(document)


@router.patch("/reconstructions/{reconstruction_id}", response_model=ReconstructionResponse)
async def update_reconstruction(
    reconstruction_id: int,
    payload: ReconstructionUpdateRequest,
    request: Request,
) -> ReconstructionResponse:
    database = request.app.state.database
    update_data = payload.model_dump(exclude_unset=True, exclude_none=True)

    if update_data.get("status") in {"COMPLETED", "FAILED"} and "completed_at" not in update_data:
        update_data["completed_at"] = datetime.now(timezone.utc)

    document = await database.reconstructions.find_one_and_update(
        {"id": reconstruction_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )

    if document is None:
        raise HTTPException(status_code=404, detail="재구성을 찾을 수 없습니다.")

    return convert_reconstruction(document)
