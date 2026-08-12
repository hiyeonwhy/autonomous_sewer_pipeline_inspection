from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_CLOUD_API_URL = "https://i15c102.p.ssafy.io"

# The Cloud API caps uploads at 10MB, so anything larger is not one of our snapshots.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


def cloud_api_base_url(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    configured = source.get("PIPECHECKKK_CLOUD_API_URL", "").strip()
    return (configured or DEFAULT_CLOUD_API_URL).rstrip("/")


class HttpCloudClient:
    """Read-only Cloud API client.

    Uses `urllib` so the report module adds no runtime dependency, matching
    `pipecheckkk_ai.command.edge_client.HttpEdgeClient`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = 10.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        resolved = (base_url or cloud_api_base_url()).rstrip("/")
        if not resolved.startswith(("http://", "https://")):
            raise ValueError("cloud API base URL must use http or https")
        self.base_url = resolved
        self.timeout_s = timeout_s
        self.opener = opener

    def read_session(self, session_id: int) -> dict[str, Any]:
        payload = self._get(f"/api/sessions/{int(session_id)}")
        if not isinstance(payload, dict):
            raise RuntimeError("session response must be a JSON object")
        return payload

    def read_defects(self, session_id: int) -> list[dict[str, Any]]:
        payload = self._get(f"/api/sessions/{int(session_id)}/defects")
        if isinstance(payload, dict):
            # Tolerate a paginated envelope if the API ever grows one.
            for key in ("content", "items", "results"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
            raise RuntimeError("defects response object had no recognized list field")
        if not isinstance(payload, list):
            raise RuntimeError("defects response must be a JSON array")
        return [item for item in payload if isinstance(item, dict)]

    def write_report_summary(self, report_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH /api/reports/{id} — 서술형 요약을 보고서에 붙인다.

        Cloud API는 snake_case만 받으므로 호출자가 그 형태로 넘겨야 한다.
        """
        result = self._request("PATCH", f"/api/reports/{int(report_id)}", payload=payload)
        if not isinstance(result, dict):
            raise RuntimeError("report update response must be a JSON object")
        return result

    def media_url(self, snapshot_path: str) -> str:
        return f"{self.base_url}{snapshot_path}"

    def read_media(self, snapshot_path: str) -> bytes:
        """Download one defect snapshot.

        `snapshot_path` must already have passed `facts.sanitize_snapshot_path`;
        this re-checks the prefix so the guard holds even on a direct call.
        """
        if not snapshot_path.startswith("/media/") or ".." in snapshot_path:
            raise RuntimeError(f"refusing to fetch a non-media path: {snapshot_path}")

        request = Request(
            self.media_url(snapshot_path),
            headers={"Accept": "image/*"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=self.timeout_s) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
                if content_type and content_type not in ALLOWED_IMAGE_TYPES:
                    raise RuntimeError(f"unexpected snapshot content type: {content_type}")
                payload = response.read(MAX_IMAGE_BYTES + 1)
        except HTTPError as error:
            raise RuntimeError(f"snapshot request rejected: HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"snapshot connection failed: {error}") from error

        if not payload:
            raise RuntimeError("snapshot response was empty")
        if len(payload) > MAX_IMAGE_BYTES:
            raise RuntimeError("snapshot exceeded the 10MB budget")
        return payload

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except HTTPError as error:
            try:
                body = json.loads(error.read())
                reason = body.get("detail", body)
            except Exception:
                reason = f"HTTP {error.code}"
            raise RuntimeError(f"Cloud API request rejected: {reason}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"Cloud API connection failed: {error}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Cloud API returned invalid JSON: {error}") from error
