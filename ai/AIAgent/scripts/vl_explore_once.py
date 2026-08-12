#!/usr/bin/env python3
"""One bounded, sensor-gated replay of the proven Discord VL inspection path."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "scripts" / "vl_pilot.py"
PYTHON = ROOT.parent / ".venv" / "bin" / "python"
LOCK = Path("/tmp/pipecheckkk-vl-explore.lock")


def command(*parts: str) -> list[str]:
    return [str(PYTHON), str(PILOT), *parts]


def parse_summary(text: str) -> dict[str, float | bool | str | None]:
    front = re.search(r"front=([0-9.]+)m", text)
    yaw = re.search(r"\byaw=(-?[0-9.]+)", text)
    armed = re.search(r"\barmed=(True|False)", text)
    return {
        "front_m": float(front.group(1)) if front else None,
        "yaw_deg": float(yaw.group(1)) if yaw else None,
        "armed": armed.group(1) == "True" if armed else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    planned = [
        "IMU stationary calibration once",
        "initial camera snapshot",
        "LEFT 10deg x up to 3 with ultrasonic checks",
        "RIGHT one step per completed LEFT step to return target to 0deg",
        "0.40 forward burst for 600ms if clearance >0.12m",
        "final camera snapshot, STOP, disarm",
    ]
    if args.dry_run:
        print(json.dumps({"dry_run": True, "planned": planned}, ensure_ascii=False))
        return 0

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": False, "error": "inspection already running"}))
            return 2

        records: list[dict[str, object]] = []
        left_steps = 0
        initial_image = Path(f"/tmp/pipecheckkk-vl-initial-{time.time_ns()}.jpg")
        final_image = Path(f"/tmp/pipecheckkk-vl-final-{time.time_ns()}.jpg")

        def run(label: str, *parts: str, timeout: float = 30.0) -> dict[str, object]:
            proc = subprocess.run(
                command(*parts), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
            item = {"label": label, "exit_code": proc.returncode,
                    "summary": parse_summary(proc.stdout), "output": proc.stdout.strip()}
            records.append(item)
            if proc.returncode:
                raise RuntimeError(f"{label} failed: {proc.stdout.strip()}")
            return item

        error = None
        try:
            run("inspection_start_calibration", "arm", "--samples", "100", timeout=45)
            run("initial_snapshot", "snap", str(initial_image), timeout=15)
            for number in range(1, 4):
                run(f"left_{number}", "turn", "LEFT", "--timeout", "15", timeout=25)
                left_steps = number
                status = run(f"left_{number}_status", "status", timeout=15)
                status_summary = status.get("summary")
                assert isinstance(status_summary, dict)
                distance = status_summary.get("front_m")
                if isinstance(distance, float) and distance <= 0.20:
                    break
            for number in range(1, left_steps + 1):
                run(f"return_right_{number}", "turn", "RIGHT", "--timeout", "15", timeout=25)
            returned = run("return_status", "status", timeout=15)
            returned_summary = returned.get("summary")
            assert isinstance(returned_summary, dict)
            yaw = returned_summary.get("yaw_deg")
            clearance = returned_summary.get("front_m")
            if not isinstance(yaw, float) or abs(yaw) > 4.0:
                raise RuntimeError(f"original yaw not restored: {yaw}")
            if not isinstance(clearance, float) or clearance <= 0.12:
                raise RuntimeError(f"forward clearance unsafe: {clearance}")
            run("forward_burst", "fwd", "--speed", "0.40", "--ms", "600", timeout=20)
            run("final_snapshot", "snap", str(final_image), timeout=15)
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                run("inspection_end", "end", timeout=15)
            except Exception as exc:
                error = error or str(exc)

        result = {
            "ok": error is None,
            "error": error,
            "left_steps": left_steps,
            "initial_image": str(initial_image) if initial_image.exists() else None,
            "final_image": str(final_image) if final_image.exists() else None,
            "records": records,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
