from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import agent_auth
from app.domain.agent_telemetry import router as telemetry_router
from app.domain.agent_telemetry.schema import AgentTelemetryUpdate


class FakeTelemetryCollection:
    def __init__(self) -> None:
        self.document: dict[str, object] | None = None

    async def find_one_and_update(self, query, update, *, upsert, return_document):
        assert query == {"agent_id": "laptop-4070"}
        assert upsert is True
        self.document = dict(update["$set"])
        return dict(self.document)

    async def find_one(self, query):
        if self.document is None or self.document.get("agent_id") != query.get("agent_id"):
            return None
        return dict(self.document)


def test_agent_telemetry_is_upserted_by_stable_agent_identity() -> None:
    collection = FakeTelemetryCollection()
    database = SimpleNamespace(agent_telemetry=collection)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))
    payload = AgentTelemetryUpdate.model_validate(
        {
            "agent_id": "laptop-4070",
            "session_id": 17,
            "operation_id": "operation-17",
            "hermes": {"state": "RUNNING", "message": "점검 중"},
            "qwen_summary": {"status": "ready", "message": "균열 후보 확인"},
            "drive": {"controlMode": "real-motor"},
        }
    )

    result = asyncio.run(telemetry_router.update_agent_telemetry(payload, request))

    assert result.agent_id == "laptop-4070"
    assert result.session_id == 17
    assert result.hermes["state"] == "RUNNING"
    assert collection.document is not None
    assert collection.document["updated_at"] is not None


class FakeJpegRequest:
    def __init__(
        self,
        *chunks: bytes,
        session_id: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        self._chunks = chunks
        self.headers = {"content-type": "image/jpeg"}
        if session_id is not None:
            self.headers["x-inspection-session-id"] = session_id
        if operation_id is not None:
            self.headers["x-inspection-operation-id"] = operation_id

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def test_latest_yolo_frame_is_written_to_cloud_media(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    request = FakeJpegRequest(
        b"\xff\xd8annotated-yolo\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )

    result = asyncio.run(telemetry_router.upload_yolo_frame(request))  # type: ignore[arg-type]

    response = asyncio.run(telemetry_router.get_yolo_frame("17", "operation-17"))
    assert Path(response.path).read_bytes() == b"\xff\xd8annotated-yolo\xff\xd9"
    assert result["ok"] is True
    assert "session_id=17&operation_id=operation-17" in str(result["path"])


def test_live_frame_is_available_without_inspection_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    request = FakeJpegRequest(b"\xff\xd8idle-live-frame\xff\xd9")

    result = asyncio.run(telemetry_router.upload_live_frame(request))  # type: ignore[arg-type]
    response = asyncio.run(telemetry_router.get_live_frame())

    assert Path(response.path).read_bytes() == b"\xff\xd8idle-live-frame\xff\xd9"
    assert result == {"ok": True, "path": "/api/agent/live-frame.jpg"}


def test_yolo_frame_is_isolated_by_inspection_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    request = FakeJpegRequest(
        b"\xff\xd8operation-frame\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )
    asyncio.run(telemetry_router.upload_yolo_frame(request))  # type: ignore[arg-type]

    response = asyncio.run(telemetry_router.get_yolo_frame("17", "operation-17"))
    assert Path(response.path).read_bytes() == b"\xff\xd8operation-frame\xff\xd9"

    with pytest.raises(HTTPException) as mismatch:
        asyncio.run(telemetry_router.get_yolo_frame("18", "operation-18"))
    assert mismatch.value.status_code == 404


def test_selected_frame_remains_immutable_during_next_upload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    first = FakeJpegRequest(
        b"\xff\xd8first-frame\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )
    second = FakeJpegRequest(
        b"\xff\xd8second-frame\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )
    asyncio.run(telemetry_router.upload_yolo_frame(first))  # type: ignore[arg-type]
    selected_first = asyncio.run(telemetry_router.get_yolo_frame("17", "operation-17"))

    asyncio.run(telemetry_router.upload_yolo_frame(second))  # type: ignore[arg-type]
    selected_second = asyncio.run(telemetry_router.get_yolo_frame("17", "operation-17"))

    assert Path(selected_first.path).read_bytes() == b"\xff\xd8first-frame\xff\xd9"
    assert Path(selected_second.path).read_bytes() == b"\xff\xd8second-frame\xff\xd9"
    assert selected_first.path != selected_second.path


def test_yolo_frame_expires_when_publisher_stops(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    request = FakeJpegRequest(
        b"\xff\xd8fresh\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )
    asyncio.run(telemetry_router.upload_yolo_frame(request))  # type: ignore[arg-type]
    scope = telemetry_router._frame_scope_dir(17, "operation-17")
    pointer_path = telemetry_router._latest_pointer_path(scope)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["uploaded_at"] = (datetime.now(timezone.utc) - timedelta(seconds=16)).isoformat()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(HTTPException) as expired:
        asyncio.run(telemetry_router.get_yolo_frame("17", "operation-17"))
    assert expired.value.status_code == 404
    assert "만료" in str(expired.value.detail)


def test_yolo_frame_rejects_non_jpeg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(telemetry_router.upload_yolo_frame(FakeJpegRequest(
            b"not-jpeg", session_id="17", operation_id="operation-17"
        )))  # type: ignore[arg-type]

    assert error.value.status_code == 415


def test_yolo_frame_rejects_oversize_stream_before_buffering_all_chunks(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        telemetry_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    request = FakeJpegRequest(
        b"\xff\xd8",
        b"x" * (8 * 1024 * 1024),
        b"ignored\xff\xd9",
        session_id="17",
        operation_id="operation-17",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(telemetry_router.upload_yolo_frame(request))  # type: ignore[arg-type]

    assert error.value.status_code == 413


def test_agent_publish_without_server_key_allows_configured_agent(monkeypatch) -> None:
    """서버에 키가 없으면 키를 **보낸** 요청만 통과시킨다.

    원래는 이 경우 무조건 503 으로 막았다(`..._fails_closed`). 그 편이 안전하지만,
    배포 자격증명에 `AGENT_PUBLISH_KEY` 가 들어 있지 않은 채로 운영돼 왔기 때문에
    Agent 의 텔레메트리·프레임·결함 업로드가 한꺼번에 503 이 되어 대시보드가 멈췄다.

    **이것은 임시 상태다.** 자격증명에 키를 넣으면 이 분기는 죽고 아래
    `test_agent_publish_key_is_required_and_constant_time_checked` 의 정확 비교만
    남는다. 그때 이 시험을 원래대로(fails_closed) 되돌릴 것.
    """
    monkeypatch.setattr(
        agent_auth,
        "get_settings",
        lambda: SimpleNamespace(agent_publish_key=""),
    )

    # 키를 보낸 Agent 는 통과한다
    assert agent_auth.require_agent_publish_key("laptop-configured-key") is None
    # 헤더조차 없으면 Agent 가 아니라고 보고 막는다
    with pytest.raises(HTTPException) as missing_server_key_error:
        agent_auth.require_agent_publish_key("")
    assert missing_server_key_error.value.status_code == 503


def test_agent_publish_key_is_required_and_constant_time_checked(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_auth,
        "get_settings",
        lambda: SimpleNamespace(agent_publish_key="expected-key"),
    )

    with pytest.raises(HTTPException) as error:
        agent_auth.require_agent_publish_key("wrong-key")
    assert error.value.status_code == 401

    with pytest.raises(HTTPException) as non_ascii_error:
        agent_auth.require_agent_publish_key("잘못된-key")
    assert non_ascii_error.value.status_code == 401

    assert agent_auth.require_agent_publish_key("expected-key") is None
