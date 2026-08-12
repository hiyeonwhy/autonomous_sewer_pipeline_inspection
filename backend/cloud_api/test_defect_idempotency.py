from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from app.core.agent_auth import require_agent_publish_key
from app.db.indexes import ensure_database_indexes
from app.domain.defects import router as defects_router
from app.domain.defects.schema import DefectCaptureRequest, DetectionCaptureRequest, Severity
from app.domain.media import router as media_router


class RecordingCollection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def create_index(self, keys: object, **kwargs: object) -> None:
        self.calls.append((keys, kwargs))


class RecordingDatabase:
    def __init__(self) -> None:
        names = [
            "sessions",
            "agent_telemetry",
            "defects",
            "drive_logs",
            "reports",
            "reconstructions",
            "districts",
            "admin_dongs",
            "priority_scores",
            "priority_by_sido",
            "priority_meta",
            "priority_batches",
            "subsidence_records",
            "robots",
            "operators",
            "counters",
        ]
        for name in names:
            setattr(self, name, RecordingCollection())


def test_detection_capture_requires_stable_operation_and_event_identity() -> None:
    payload = DetectionCaptureRequest.model_validate(
        {
            "operation_id": "inspection-record-001-run-7",
            "event_id": "frame-000042",
            "snapshot_path": "/media/captures/frame.jpg",
            "detections": [
                {
                    "defect_type": "CRACK",
                    "confidence": 0.91,
                    "bbox": {"x1": 10, "y1": 20, "x2": 30, "y2": 40},
                }
            ],
        }
    )

    assert payload.operation_id == "inspection-record-001-run-7"
    assert payload.event_id == "frame-000042"


def test_defect_event_identity_has_a_compound_unique_index() -> None:
    database = RecordingDatabase()

    asyncio.run(ensure_database_indexes(database))  # type: ignore[arg-type]

    assert (
        [
            ("session_id", 1),
            ("operation_id", 1),
            ("event_id", 1),
            ("detection_index", 1),
        ],
        {
            "unique": True,
            "partialFilterExpression": {
                "operation_id": {"$type": "string"},
                "event_id": {"$type": "string"},
                "detection_index": {"$type": "int"},
            },
        },
    ) in database.defects.calls


class FakeSessions:
    def __init__(self, *, fail_updates: int = 0) -> None:
        self.document = {
            "id": 7,
            "defect_summary": {"total": 0, "critical": 0, "warning": 0, "normal": 0},
        }
        self.update_count = 0
        self.fail_updates = fail_updates
        self.counted_detection_keys: set[str] = set()

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return self.document if query.get("id") == 7 else None

    async def update_one(self, query: dict[str, object], update: dict[str, object]) -> None:
        assert query.get("id") == 7
        if "$set" in update:
            self.document.update(update["$set"])  # type: ignore[arg-type]
            self.update_count += 1
            return
        identity = str(query["counted_detection_keys"]["$ne"])  # type: ignore[index]
        if identity in self.counted_detection_keys:
            return
        if self.fail_updates > 0:
            self.fail_updates -= 1
            raise RuntimeError("simulated summary write failure")
        self.counted_detection_keys.add(identity)
        summary = self.document["defect_summary"]
        for path, amount in update["$inc"].items():  # type: ignore[union-attr]
            summary[path.rsplit(".", 1)[-1]] += amount  # type: ignore[index,operator]
        self.update_count += 1


class FakeDefects:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )

    async def update_one(
        self,
        query: dict[str, object],
        update: dict[str, dict[str, object]],
        *,
        upsert: bool,
    ) -> SimpleNamespace:
        assert upsert is True
        existing = await self.find_one(query)
        if existing is not None:
            return SimpleNamespace(upserted_id=None)
        document = dict(update["$setOnInsert"])
        self.documents.append(document)
        return SimpleNamespace(upserted_id=document["id"])


async def _exercise_duplicate_event(monkeypatch, tmp_path) -> tuple[int, int]:
    database = SimpleNamespace(sessions=FakeSessions(), defects=FakeDefects())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))
    counter = 0

    async def next_sequence(**_kwargs: object) -> int:
        nonlocal counter
        counter += 1
        return counter

    def build_documents(session_id, payload, items, _source, _directory, defect_ids):
        return [
            {
                "id": defect_ids[index],
                "session_id": session_id,
                "operation_id": payload.operation_id,
                "event_id": payload.event_id,
                "detection_index": index,
                "defect_type": item.defect_type.value,
                "severity": "WARNING",
                "confidence": item.confidence,
                "approx_position_m": None,
                "snapshot_path": f"/media/defects/{index}.jpg",
                "source_snapshot_path": payload.snapshot_path,
                "bbox": item.bbox.model_dump(),
                "repair_status": "BEFORE",
                "action_type": None,
                "review_status": "PENDING",
                "detected_at": "2026-08-07T00:00:00+00:00",
            }
            for index, item in enumerate(items)
        ]

    source = tmp_path / "frame.jpg"
    source.write_bytes(b"jpeg")
    monkeypatch.setattr(defects_router, "resolve_media_file", lambda _path: source)
    monkeypatch.setattr(defects_router, "get_next_sequence", next_sequence)
    monkeypatch.setattr(defects_router, "build_detection_documents", build_documents)
    monkeypatch.setattr(
        defects_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    payload = DetectionCaptureRequest.model_validate(
        {
            "operation_id": "operation-7",
            "event_id": "frame-42",
            "snapshot_path": "/media/captures/frame.jpg",
            "detections": [
                {
                    "defect_type": "CRACK",
                    "confidence": 0.91,
                    "bbox": {"x1": 10, "y1": 20, "x2": 30, "y2": 40},
                }
            ],
        }
    )

    await defects_router.create_detection_defects(7, payload, request)
    await defects_router.create_detection_defects(7, payload, request)
    return len(database.defects.documents), database.sessions.update_count


def test_duplicate_detection_event_is_stored_and_counted_once(monkeypatch, tmp_path) -> None:
    stored, summary_updates = asyncio.run(_exercise_duplicate_event(monkeypatch, tmp_path))

    assert stored == 1
    assert summary_updates == 1


def test_retry_repairs_summary_after_defect_was_stored_but_accounting_failed(
    monkeypatch, tmp_path
) -> None:
    sessions = FakeSessions(fail_updates=1)
    database = SimpleNamespace(sessions=sessions, defects=FakeDefects())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))
    counter = 0

    async def next_sequence(**_kwargs: object) -> int:
        nonlocal counter
        counter += 1
        return counter

    def build_documents(session_id, payload, items, _source, _directory, defect_ids):
        return [
            {
                "id": defect_ids[index],
                "session_id": session_id,
                "operation_id": payload.operation_id,
                "event_id": payload.event_id,
                "detection_index": index,
                "defect_type": item.defect_type.value,
                "severity": "WARNING",
                "confidence": item.confidence,
                "approx_position_m": None,
                "snapshot_path": f"/media/defects/{index}.jpg",
                "source_snapshot_path": payload.snapshot_path,
                "bbox": item.bbox.model_dump(),
                "repair_status": "BEFORE",
                "action_type": None,
                "review_status": "PENDING",
                "detected_at": "2026-08-07T00:00:00+00:00",
            }
            for index, item in enumerate(items)
        ]

    source = tmp_path / "frame.jpg"
    source.write_bytes(b"jpeg")
    monkeypatch.setattr(defects_router, "resolve_media_file", lambda _path: source)
    monkeypatch.setattr(defects_router, "get_next_sequence", next_sequence)
    monkeypatch.setattr(defects_router, "build_detection_documents", build_documents)
    monkeypatch.setattr(
        defects_router,
        "get_settings",
        lambda: SimpleNamespace(media_root=str(tmp_path)),
    )
    payload = DetectionCaptureRequest.model_validate(
        {
            "operation_id": "operation-7",
            "event_id": "frame-repair",
            "snapshot_path": "/media/captures/frame.jpg",
            "detections": [
                {
                    "defect_type": "CRACK",
                    "confidence": 0.91,
                    "bbox": {"x1": 10, "y1": 20, "x2": 30, "y2": 40},
                }
            ],
        }
    )

    with pytest.raises(RuntimeError, match="summary write failure"):
        asyncio.run(defects_router.create_detection_defects(7, payload, request))  # type: ignore[arg-type]
    result = asyncio.run(
        defects_router.create_detection_defects(7, payload, request)  # type: ignore[arg-type]
    )

    assert len(result) == 1
    assert len(database.defects.documents) == 1
    assert sessions.document["defect_summary"]["total"] == 1  # type: ignore[index]


def test_agent_media_and_defect_write_routes_require_publish_key() -> None:
    protected = {
        ("/api/media/captures", "POST"),
        ("/api/sessions/{session_id}/captures", "POST"),
        ("/api/sessions/{session_id}/detections", "POST"),
    }
    routes = [
        route
        for router in (media_router.router, defects_router.router)
        for route in router.routes
        if isinstance(route, APIRoute)
    ]
    for path, method in protected:
        route = next(item for item in routes if item.path == path and method in item.methods)
        assert require_agent_publish_key in {
            dependency.call for dependency in route.dependant.dependencies
        }


def test_counted_identity_encoding_does_not_collapse_colon_containing_tuples() -> None:
    sessions = FakeSessions()
    database = SimpleNamespace(sessions=sessions)
    first = {
        "session_id": 7,
        "operation_id": "a:b",
        "event_id": "c",
        "detection_index": 0,
    }
    second = {
        "session_id": 7,
        "operation_id": "a",
        "event_id": "b:c",
        "detection_index": 0,
    }

    asyncio.run(
        defects_router.account_detection_summary_once(
            database, 7, first, Severity.WARNING
        )
    )
    asyncio.run(
        defects_router.account_detection_summary_once(
            database, 7, second, Severity.WARNING
        )
    )

    assert sessions.document["defect_summary"]["total"] == 2  # type: ignore[index]
    assert len(sessions.counted_detection_keys) == 2


def test_legacy_capture_retry_is_upserted_and_counted_once(monkeypatch) -> None:
    sessions = FakeSessions()
    database = SimpleNamespace(sessions=sessions, defects=FakeDefects())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))
    counter = 0

    async def next_sequence(**_kwargs: object) -> int:
        nonlocal counter
        counter += 1
        return counter

    payload = DefectCaptureRequest.model_validate(
        {
            "defect_type": "CRACK",
            "severity": "WARNING",
            "confidence": 0.91,
            "snapshot_path": "/media/defects/manual-frame.jpg",
        }
    )
    monkeypatch.setattr(defects_router, "get_next_sequence", next_sequence)
    asyncio.run(defects_router.create_capture_defect(7, payload, request))  # type: ignore[arg-type]
    asyncio.run(defects_router.create_capture_defect(7, payload, request))  # type: ignore[arg-type]

    assert len(database.defects.documents) == 1
    assert sessions.document["defect_summary"]["total"] == 1  # type: ignore[index]
