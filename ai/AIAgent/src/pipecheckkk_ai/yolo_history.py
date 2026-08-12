"""Bounded persistent storage for YOLO detection snapshots."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


class YoloCaptureStore:
    """Store detection frames and metadata with bounded disk usage."""

    def __init__(self, directory: Path, *, max_items: int = 50, min_interval_s: float = 5.0) -> None:
        self.directory = directory
        self.max_items = max_items
        self.min_interval_s = min_interval_s
        self.index_path = directory / "history.json"
        self._lock = threading.Lock()
        self._last_saved_at = float("-inf")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._records = self._load_records()

    def _load_records(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []
        records: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            capture_id = item.get("captureId")
            if isinstance(capture_id, str) and (self.directory / f"{capture_id}.jpg").is_file():
                records.append(item)
        return records[: self.max_items]

    def _write_index(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.index_path)

    def add(
        self,
        frame_bytes: bytes,
        detections: list[dict[str, Any]],
        *,
        frame_width: int | None,
        frame_height: int | None,
    ) -> dict[str, Any] | None:
        if not detections:
            return None
        with self._lock:
            now = monotonic()
            if now - self._last_saved_at < self.min_interval_s:
                return None
            digest = hashlib.sha256(frame_bytes).hexdigest()[:16]
            captured_at = datetime.now(timezone.utc)
            capture_id = f"{int(captured_at.timestamp() * 1000)}-{digest}"
            image_path = self.directory / f"{capture_id}.jpg"
            image_path.write_bytes(frame_bytes)
            record: dict[str, Any] = {
                "captureId": capture_id,
                "capturedAt": captured_at.isoformat(),
                "snapshotUrl": f"/api/yolo/captures/{capture_id}.jpg",
                "frameWidth": frame_width,
                "frameHeight": frame_height,
                "detections": detections,
            }
            self._records.insert(0, record)
            removed = self._records[self.max_items :]
            self._records = self._records[: self.max_items]
            for item in removed:
                old_id = item.get("captureId")
                if isinstance(old_id, str):
                    (self.directory / f"{old_id}.jpg").unlink(missing_ok=True)
            self._write_index()
            self._last_saved_at = now
            return dict(record)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._records[: max(0, min(limit, self.max_items))]]

    def image_path(self, capture_id: str) -> Path | None:
        with self._lock:
            if not any(item.get("captureId") == capture_id for item in self._records):
                return None
            path = self.directory / f"{capture_id}.jpg"
            return path if path.is_file() else None
