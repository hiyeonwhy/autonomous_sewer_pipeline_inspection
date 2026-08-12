#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pipecheckkk_ai.pi_discovery import discover_pi, hosts_from_ip_json  # noqa: E402


def _fetch_json(url: str, timeout: float) -> object:
    with request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def agent_already_running(
    *, fetch: Callable[[str, float], object] = _fetch_json
) -> bool:
    try:
        payload = fetch("http://127.0.0.1:8000/health", 1.0)
    except Exception:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("service") == "pipecheckkk-ai"
    )


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    if agent_already_running():
        print("[auto-pi] PipeCheckkk Agent already running on 127.0.0.1:8000")
        return
    load_env(ROOT / ".env")
    address_json = subprocess.check_output(
        ["ip", "-j", "-4", "address", "show", "up"],
        text=True,
    )
    hosts = hosts_from_ip_json(json.loads(address_json))
    pi_ip = discover_pi(hosts)
    os.environ.update(
        {
            "PIPECHECKKK_EDGE_URL": f"http://{pi_ip}:8765",
            "PIPECHECKKK_CAMERA_SNAPSHOT_URL": f"http://{pi_ip}:8081/snapshot.jpg",
            "PIPECHECKKK_CAMERA_STREAM_URL": f"http://{pi_ip}:8081/stream.mjpg",
        }
    )
    print(f"[auto-pi] camera+motor Edge discovered at {pi_ip}", flush=True)
    python = ROOT.parent / ".venv" / "bin" / "python"
    os.execvpe(
        str(python),
        [
            str(python),
            "-m",
            "uvicorn",
            "pipecheckkk_ai.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        {**os.environ, "PYTHONPATH": str(SRC)},
    )


if __name__ == "__main__":
    main()
