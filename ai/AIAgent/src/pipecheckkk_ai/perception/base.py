from __future__ import annotations

from typing import Protocol

from pipecheckkk_ai.models import PerceptionResult


class PerceptionProvider(Protocol):
    name: str

    def analyze(self, image_bytes: bytes) -> PerceptionResult: ...
