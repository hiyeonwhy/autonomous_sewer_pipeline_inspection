#!/usr/bin/env python3
"""Keep the Pi Edge armed by sending /heartbeat faster than the 1000 ms timeout.

The Pi's EdgeRuntime calls watchdog_tick() every 60 ms and disarms as soon as the
last heartbeat is older than heartbeat_timeout_ms. A human-in-the-loop pilot takes
seconds per decision, so the heartbeat has to live in its own process.

Stop it by creating the sentinel file passed as --sentinel (default: ./STOP_HEARTBEAT).
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def post(url: str, token: str, path: str, payload: dict, timeout: float = 1.0) -> dict:
    request = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Edge-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("PIPECHECKKK_EDGE_URL", ""))
    parser.add_argument("--token", default=os.environ.get("PIPECHECKKK_EDGE_TOKEN", ""))
    parser.add_argument("--interval", type=float, default=0.30)
    parser.add_argument("--sentinel", default="STOP_HEARTBEAT")
    args = parser.parse_args()

    if not args.url or not args.token:
        raise SystemExit("PIPECHECKKK_EDGE_URL and PIPECHECKKK_EDGE_TOKEN are required")

    sentinel = Path(args.sentinel)
    sentinel.unlink(missing_ok=True)
    consecutive_failures = 0
    last_state = None
    print(f"heartbeat started interval={args.interval}s sentinel={sentinel}", flush=True)

    while not sentinel.exists():
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            response = post(args.url, args.token, "/heartbeat", {})
            state = "rejected" if not response.get("ok") else (
                "armed" if response.get("armed") else "disarmed"
            )
            # Only the transitions matter; a steady state at 3 Hz would flood the log.
            if state != last_state:
                print(f"{stamp} edge is {state}", flush=True)
                last_state = state
            consecutive_failures = 0
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            consecutive_failures += 1
            last_state = None
            print(f"{stamp} heartbeat failed ({consecutive_failures}): {error}", flush=True)
        time.sleep(args.interval)

    print("heartbeat stopped by sentinel", flush=True)


if __name__ == "__main__":
    main()
