#!/usr/bin/env python3
"""Human/VL-in-the-loop pilot for the PipeCheckkk Pi motor edge.

Each subcommand is a single discrete step so a slow decision maker (a person or a
vision-language model) can drive: look at one frame, issue one step, the Pi stops
itself when the command TTL expires, look again.

Safety notes for this specific robot:
  * VL does not apply a separate distance judgment. The Pi Edge independently
    enforces the configured 0.05 m normal-driving ultrasonic hard stop.
  * start_yaw_step() only accepts phase READY or YAW_STEP_REACHED, so forward
    motion here uses AI_LOCAL_PLAN rather than the PITCH_GUARD / V2_PITCH_ADVANCE
    pair; entering PITCH_GUARD would lock out turning until a recovery cycle.
    Pitch and roll are therefore checked here before every move.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Pi rejects any single local command above 0.80 and any HTTP command above maxSpeed.
MAX_THROTTLE = 0.50
MAX_FORWARD_BURST_MS = 4000
YAW_STEP_PI_DEADLINE_S = 30.0
YAW_STEP_OBSERVATION_GRACE_S = 2.0
MOVE_PHASES = {"READY", "YAW_STEP_REACHED", "COMPLETED"}
TEMP_REFERENCE_CAPTURE_ATTEMPTS = 5
TEMP_REFERENCE_CAPTURE_RETRY_S = 0.2
HEARTBEAT_TRACE_PATH = Path(__file__).resolve().parents[1] / "run_records" / "heartbeat-traces.jsonl"


class PilotError(RuntimeError):
    pass


def load_env(env_path: Path) -> tuple[str, str]:
    url = os.environ.get("PIPECHECKKK_EDGE_URL", "")
    token = os.environ.get("PIPECHECKKK_EDGE_TOKEN", "")
    if (not url or not token) and env_path.is_file():
        for line in env_path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "PIPECHECKKK_EDGE_URL" and not url:
                url = value.strip()
            elif key.strip() == "PIPECHECKKK_EDGE_TOKEN" and not token:
                token = value.strip()
    if not url or not token:
        raise PilotError("edge URL and token not found in environment or .env")
    return url.rstrip("/"), token


class Edge:
    def __init__(self, url: str, token: str, timeout: float = 3.0) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Edge-Token": self.token},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                return json.loads(error.read())
            except Exception:
                raise PilotError(f"HTTP {error.code} from {path}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise PilotError(f"edge unreachable at {path}: {error}") from error

    def health(self) -> dict:
        return self._call("GET", "/health")

    def arm(self) -> dict:
        return self._call("POST", "/arm", {})

    def disarm(self) -> dict:
        return self._call("POST", "/disarm", {})

    def heartbeat(self) -> dict:
        return self._call("POST", "/heartbeat", {})

    def stop(self, sequence: int) -> dict:
        return self._call("POST", "/stop", {"sequence": sequence})

    def command(self, payload: dict) -> dict:
        return self._call("POST", "/command", payload)

    def imu_calibrate(self, samples: int = 100) -> dict:
        return self._call("POST", "/imu/calibrate", {"samples": samples})

    def imu_yaw_step(self, direction: str) -> dict:
        return self._call("POST", "/imu/yaw-step", {"direction": direction})

    def imu_yaw_step_current(self, direction: str, degrees: float = 10.0) -> dict:
        return self._call(
            "POST",
            "/imu/yaw-step-current",
            {"direction": direction, "degrees": degrees},
        )

    def imu_capture_temporary_reference(self, name: str) -> dict:
        return self._call(
            "POST", "/imu/temporary-reference/capture", {"name": name}
        )

    def imu_clear_temporary_reference(self) -> dict:
        return self._call("POST", "/imu/temporary-reference/clear", {})

    def imu_restore_temporary_reference(self, state: dict) -> dict:
        return self._call("POST", "/imu/temporary-reference/restore", state)

    def imu_yaw_step_relative(self, direction: str) -> dict:
        return self._call(
            "POST", "/imu/yaw-step-relative", {"direction": direction}
        )

    def imu_pitch_guard(self) -> dict:
        return self._call("POST", "/imu/pitch-guard", {})

    def imu_recover(self) -> dict:
        return self._call("POST", "/imu/recover", {})

    def forward_timed_continuous(self, speed: float, duration_s: float) -> dict:
        return self._call(
            "POST",
            "/forward-timed-continuous",
            {"speed": speed, "durationSeconds": duration_s},
        )

    def forward_until_distance_continuous(
        self, speed: float, target_distance_m: float, deadline_s: float
    ) -> dict:
        return self._call(
            "POST",
            "/forward-until-distance",
            {
                "speed": speed,
                "targetDistanceM": target_distance_m,
                "deadlineSeconds": deadline_s,
            },
        )

    def recover_path(self, deadline_s: float) -> dict:
        return self._call(
            "POST", "/recover-path", {"deadlineSeconds": deadline_s}
        )

    def yaw_scan_360(self, deadline_s: float) -> dict:
        return self._call(
            "POST", "/yaw-scan-360", {"deadlineSeconds": deadline_s}
        )

    def yaw_temp_180(self, deadline_s: float) -> dict:
        return self._call(
            "POST", "/yaw-temp-180", {"deadlineSeconds": deadline_s}
        )

    def imu_restore_reference(self, state: dict) -> dict:
        return self._call("POST", "/imu/reference/restore", state)

    def imu_reset(self) -> dict:
        return self._call("POST", "/imu/reset", {})


class _HeartbeatKeeper:
    def __init__(
        self,
        edge: Edge,
        interval_s: float = 0.15,
        trace_path: Path | None = HEARTBEAT_TRACE_PATH,
    ) -> None:
        self.edge = edge
        self.interval_s = interval_s
        self.trace_path = trace_path
        self._stop = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._samples: list[dict[str, object]] = []
        self._sample_count = 0
        self._flushed = False

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            sample: dict[str, object] = {
                "sentAt": datetime.now(timezone.utc).isoformat(),
            }
            try:
                heartbeat = self.edge.heartbeat()
                sample.update(
                    latencyMs=round((time.monotonic() - started) * 1000.0, 3),
                    armed=bool(heartbeat.get("armed")),
                    ok=bool(heartbeat.get("ok", True)),
                )
                if not heartbeat.get("armed"):
                    raise PilotError(f"edge disarmed during heartbeat: {heartbeat}")
            except Exception as error:
                sample.update(
                    latencyMs=round((time.monotonic() - started) * 1000.0, 3),
                    error=f"{type(error).__name__}: {error}",
                )
                self._record(sample)
                self._error = error
                self._stop.set()
                return
            self._record(sample)
            self._stop.wait(self.interval_s)

    def _record(self, sample: dict[str, object]) -> None:
        self._sample_count += 1
        self._samples.append(sample)
        if len(self._samples) > 128:
            del self._samples[:-128]

    def check(self) -> None:
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=float(getattr(self.edge, "timeout", 3.0)) + 0.5)
        self._flush_trace()

    def _flush_trace(self) -> None:
        if self._flushed or self.trace_path is None or not self._samples:
            return
        self._flushed = True
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "closedAt": datetime.now(timezone.utc).isoformat(),
            "sampleCount": self._sample_count,
            "error": None if self._error is None else f"{type(self._error).__name__}: {self._error}",
            "samples": self._samples,
        }
        with self.trace_path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def summarize(health: dict) -> str:
    imu = health.get("imu", {})

    def number(value: object, digits: int = 1) -> str:
        return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "--"

    front = health.get("frontDistanceM")
    return (
        f"armed={health.get('armed')} estop={health.get('emergencyStop')} "
        f"front={number(front, 3)}m seq={health.get('lastSequence')} "
        f"| imu {imu.get('phase')} yaw={number(imu.get('yawDeg'))} "
        f"roll={number(imu.get('rollDeg'))} pitch={number(imu.get('pitchDeg'))} "
        f"target={number(imu.get('yawTargetDeg'))} valid={imu.get('valid')} "
        f"age={number(imu.get('ageS'), 3)}s reason={imu.get('reason')}"
    )


def preflight(health: dict, *, forward: bool) -> None:
    """Raise unless it is safe to command motion right now."""
    if not health.get("ok"):
        raise PilotError("edge health is not ok")
    if health.get("emergencyStop"):
        raise PilotError("emergency stop is active")
    if not health.get("armed"):
        raise PilotError("edge is not armed — run 'arm' first")

    imu = health.get("imu", {})
    if not imu.get("available"):
        raise PilotError("IMU is unavailable")
    if not imu.get("calibrated"):
        raise PilotError("IMU is not calibrated — run 'arm' first")
    phase = str(imu.get("phase"))
    if phase not in MOVE_PHASES:
        raise PilotError(f"IMU phase {phase} does not accept a new pilot step")
    if not imu.get("valid"):
        raise PilotError("latest IMU sample is invalid")
    age = imu.get("ageS")
    if isinstance(age, (int, float)) and age > 0.2:
        raise PilotError(f"IMU sample is stale ({age:.3f}s > 0.2s)")


def wait_for_motion_preflight(edge: Edge, *, forward: bool) -> dict:
    """Wait out transient invalid/stale IMU samples before starting motion."""
    while True:
        health = edge.health()
        if not health.get("ok"):
            raise PilotError("edge health is not ok")
        if health.get("emergencyStop"):
            raise PilotError("emergency stop is active")

        imu = health.get("imu", {})
        if not imu.get("available"):
            raise PilotError("IMU is unavailable")
        if not imu.get("calibrated"):
            raise PilotError("IMU is not calibrated — run 'arm' first")
        phase = str(imu.get("phase"))
        if phase not in MOVE_PHASES:
            raise PilotError(f"IMU phase {phase} does not accept a new pilot step")
        age = imu.get("ageS")
        fresh = not isinstance(age, (int, float)) or age <= 0.2
        if imu.get("valid") and fresh:
            if not health.get("armed"):
                armed = edge.arm()
                if not armed.get("armed"):
                    print("IMU is valid but arm is not ready; retrying")
                    time.sleep(0.2)
                    continue
                health = edge.health()
                continue
            preflight(health, forward=forward)
            return health

        print(f"IMU sample not ready in phase {phase}; waiting before motion start")
        time.sleep(0.2)


def wait_for_imu_valid(edge: Edge, *, rearm: bool) -> dict:
    """Poll transient IMU invalid without introducing a laptop heartbeat dependency."""
    while True:
        health = edge.health()
        imu = health.get("imu", {})
        valid = imu.get("valid")
        if valid is not False:
            if rearm and valid is True and not health.get("armed"):
                armed = edge.arm()
                if not armed.get("armed"):
                    time.sleep(0.2)
                    continue
                health = edge.health()
                continue
            return health
        print("IMU sample invalid; retrying without laptop heartbeat")
        time.sleep(0.2)


def do_status(edge: Edge, _args) -> int:
    health = edge.health()
    print(summarize(health))
    print(json.dumps(health, indent=2))
    return 0


def do_snap(edge: Edge, args) -> int:
    destination = Path(args.out)
    request = urllib.request.Request(args.camera)
    with urllib.request.urlopen(request, timeout=5.0) as response:
        destination.write_bytes(response.read())
    health = edge.health()
    print(f"saved {destination} ({destination.stat().st_size} bytes)")
    print(summarize(health))
    return 0


def do_arm(edge: Edge, args) -> int:
    """Mission-start order from the spec: calibrate while still disarmed, then arm."""
    health = edge.health()
    if health.get("armed"):
        print("already armed; disarming first so the IMU can calibrate")
        edge.disarm()
    phase = str(health.get("imu", {}).get("phase"))
    if phase == "ERROR":
        print("IMU is in ERROR; resetting before calibration")
        edge.imu_reset()

    print(f"calibrating IMU ({args.samples} stationary samples) — keep the robot still")
    calibrated = edge.imu_calibrate(args.samples)
    if not calibrated.get("calibrated") or calibrated.get("phase") != "READY":
        raise PilotError(f"IMU calibration rejected: {calibrated}")

    armed = edge.arm()
    if not armed.get("armed"):
        raise PilotError(f"arm rejected: {armed}")
    print(summarize(edge.health()))
    return 0


def do_resume(edge: Edge, _args) -> int:
    """Arm for a later approved step without changing mission-start IMU zero."""
    health = edge.health()
    if health.get("armed"):
        health = wait_for_motion_preflight(edge, forward=False)
        print(summarize(health))
        return 0
    imu = health.get("imu", {})
    while True:
        if not imu.get("available") or not imu.get("calibrated"):
            raise PilotError("inspection-start IMU calibration is unavailable")
        phase = str(imu.get("phase"))
        if phase not in MOVE_PHASES:
            raise PilotError(f"IMU reference cannot resume from phase {phase}")
        if imu.get("valid"):
            break
        print(f"IMU sample invalid in phase {phase}; waiting for a valid sample")
        time.sleep(0.2)
        health = edge.health()
        imu = health.get("imu", {})
    armed = edge.arm()
    if not armed.get("armed"):
        raise PilotError(f"resume arm rejected: {armed}")
    health = wait_for_motion_preflight(edge, forward=False)
    print(summarize(health))
    return 0


def do_fwd(edge: Edge, args) -> int:
    throttle = min(MAX_THROTTLE, max(0.0, args.speed))
    ttl_ms = max(100, min(MAX_FORWARD_BURST_MS, args.ms))
    health = wait_for_motion_preflight(edge, forward=True)

    sequence = int(health.get("lastSequence", -1)) + 1
    issued_at = datetime.now(timezone.utc)
    payload = {
        "commandId": f"pilot-{sequence}",
        "missionId": "vl-pilot",
        "sequence": sequence,
        "action": "AI_LOCAL_PLAN",
        "leftSpeed": throttle,
        "rightSpeed": throttle,
        "issuedAt": issued_at.isoformat(),
        "expiresAt": (issued_at + timedelta(milliseconds=ttl_ms)).isoformat(),
    }
    ack = edge.command(payload)
    print(f"fwd {throttle:.2f} for {ttl_ms}ms -> {ack.get('reason')} accepted={ack.get('accepted')}")
    if not ack.get("accepted"):
        raise PilotError(f"command rejected: {ack}")

    # The Pi's watchdog stops the motors at expiry; wait it out, then confirm.
    time.sleep(ttl_ms / 1000.0 + 0.25)
    print(summarize(edge.health()))
    return 0


def do_fwd_timed_continuous(edge: Edge, args) -> int:
    speed = args.speed
    duration_s = args.seconds
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value)
        for value in (speed, duration_s)
    ):
        raise PilotError("timed continuous forward request must be finite")
    if not 0 < speed <= 0.40 or not 0 < duration_s <= 60.0:
        raise PilotError("timed continuous forward request is outside the safe range")
    wait_for_motion_preflight(edge, forward=True)
    started_at = time.monotonic()
    started = edge.forward_timed_continuous(speed, duration_s)
    if started.get("localOperation") != "FORWARD_TIMED_CONTINUOUS":
        raise PilotError(f"timed continuous forward rejected: {started}")
    deadline = started_at + duration_s + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.1)
        health = edge.health()
        if health.get("localOperation") == "FORWARD_TIMED_CONTINUOUS":
            continue
        elapsed = time.monotonic() - started_at
        if elapsed < duration_s - 0.25:
            raise PilotError(f"timed continuous forward stopped early: {health}")
        if health.get("armed") is False:
            print(f"timed continuous forward completed after {elapsed:.2f}s")
            return 0
        raise PilotError(f"timed continuous forward did not disarm: {health}")
    raise PilotError("timed continuous forward did not stop after Pi deadline")


def do_fwd_continuous_until_distance(edge: Edge, args) -> int:
    speed = args.speed
    target = args.distance
    timeout_s = args.timeout
    values = (speed, target, timeout_s)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise PilotError("continuous distance request must be finite")
    if not 0 < speed <= 0.40 or not 0.05 <= target <= 2.0:
        raise PilotError("continuous distance request is outside the safe range")
    if not 0 < timeout_s <= 60.0:
        raise PilotError("continuous distance timeout must be at most 60 seconds")
    wait_for_motion_preflight(edge, forward=True)
    started = edge.forward_until_distance_continuous(speed, target, timeout_s)
    if started.get("localOperation") is None:
        health = edge.health()
        distance = health.get("frontDistanceM")
        if isinstance(distance, (int, float)) and distance <= target:
            return 0
        raise PilotError(f"continuous forward rejected: {started}")
    deadline = time.monotonic() + timeout_s + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.1)
        health = edge.health()
        distance = health.get("frontDistanceM")
        operation = health.get("localOperation")
        if operation == "FORWARD_UNTIL_DISTANCE":
            continue
        terminal_reason = health.get("lastTerminalReason")
        if terminal_reason == "V2_FORWARD_DISTANCE_REACHED":
            print(f"continuous distance target completed: {terminal_reason}")
            return 0
        if (
            getattr(args, "complete_on_timeout", False)
            and terminal_reason == "V2_FORWARD_DURATION_COMPLETED"
        ):
            print(f"continuous duration completed: {terminal_reason}")
            return 0
        if (
            health.get("armed") is False
            and isinstance(distance, (int, float))
            and math.isfinite(distance)
            and distance <= target
        ):
            print(f"continuous distance target reached: {distance:.3f}m <= {target:.3f}m")
            return 0
        raise PilotError(
            f"continuous forward terminated without completion reason: {health}"
        )
    raise PilotError("continuous forward did not become terminal after Pi deadline")


def do_fwd_until_distance(edge: Edge, args) -> int:
    target = float(args.distance)
    timeout_s = float(args.timeout)
    if not 0.05 <= target <= 2.0:
        raise PilotError("target distance must be between 0.05m and 2.0m")
    if not 0.2 <= timeout_s <= 30.0:
        raise PilotError("distance approach timeout must be between 0.2s and 30s")
    elapsed_ms = 0
    max_ms = int(timeout_s * 1000)
    fixed_burst_ms = getattr(args, "burst_ms", None)
    if fixed_burst_ms is not None and (
        not isinstance(fixed_burst_ms, int) or not 100 <= fixed_burst_ms <= 3000
    ):
        raise PilotError("fixed distance burst must be between 100ms and 3000ms")
    while elapsed_ms < max_ms:
        health = edge.health()
        distance = health.get("frontDistanceM")
        if health.get("emergencyStop"):
            raise PilotError("unsafe telemetry during distance approach")
        if not isinstance(distance, (int, float)) or not math.isfinite(distance):
            raise PilotError("front distance is unavailable during distance approach")
        if distance <= target:
            print(f"distance target reached: {distance:.3f}m <= {target:.3f}m")
            return 0
        duration_ms = (
            fixed_burst_ms
            if fixed_burst_ms is not None
            else (500 if distance > target + 0.15 else 200)
        )
        if elapsed_ms + duration_ms > max_ms:
            break
        do_resume(edge, argparse.Namespace())
        result = do_fwd(
            edge,
            argparse.Namespace(speed=args.speed, ms=duration_ms),
        )
        if result != 0:
            raise PilotError("distance approach burst failed")
        elapsed_ms += duration_ms
    raise PilotError("distance approach reached its bounded timeout")


def do_temp_ref_clear(edge: Edge, _args) -> int:
    response = edge.imu_clear_temporary_reference()
    if response.get("temporaryReference") is not None:
        raise PilotError("temporary IMU reference clear was not confirmed")
    print("temporary IMU reference cleared")
    return 0


def do_temp_ref_capture(edge: Edge, args) -> int:
    name = args.name.strip().upper()
    last_response: dict = {}
    for attempt in range(1, TEMP_REFERENCE_CAPTURE_ATTEMPTS + 1):
        last_response = edge.imu_capture_temporary_reference(name)
        temporary = last_response.get("temporaryReference")
        if isinstance(temporary, dict) and temporary.get("name") == name:
            print(json.dumps(temporary, ensure_ascii=False, sort_keys=True))
            return 0
        if attempt < TEMP_REFERENCE_CAPTURE_ATTEMPTS:
            time.sleep(TEMP_REFERENCE_CAPTURE_RETRY_S)

    reason = last_response.get("reason", "TEMP_REFERENCE_NOT_CONFIRMED")
    detail = last_response.get("detail")
    suffix = f": {detail}" if detail else ""
    raise PilotError(
        f"temporary IMU reference capture failed after "
        f"{TEMP_REFERENCE_CAPTURE_ATTEMPTS} attempts: {reason}{suffix}"
    )


def do_turn(edge: Edge, args) -> int:
    direction = args.direction.upper()
    if direction not in {"LEFT", "RIGHT"}:
        raise PilotError("direction must be LEFT or RIGHT")
    wait_for_motion_preflight(edge, forward=False)

    if getattr(args, "current", False):
        started = edge.imu_yaw_step_current(direction, float(args.degrees))
    elif getattr(args, "relative", False):
        started = edge.imu_yaw_step_relative(direction)
    else:
        started = edge.imu_yaw_step(direction)
    if (
        started.get("phase") != "PIVOT_YAW"
        or started.get("localOperation") != "YAW_STEP"
    ):
        raise PilotError(f"Pi-owned yaw step rejected: {started}")
    target = started.get("yawTargetDeg")
    print(f"Pi-owned pivot {direction} toward cumulative target {target}°")

    observation_s = max(float(args.timeout), YAW_STEP_PI_DEADLINE_S)
    deadline = time.monotonic() + observation_s + YAW_STEP_OBSERVATION_GRACE_S
    while time.monotonic() < deadline:
        time.sleep(0.15)
        health = edge.health()
        imu = health.get("imu", {})
        phase = str(imu.get("phase"))
        if phase == "YAW_STEP_REACHED":
            if (
                health.get("armed") is not False
                or health.get("localOperation") is not None
                or imu.get("localOperation") is not None
            ):
                raise PilotError("Pi-owned yaw step terminal state is unsafe")
            print(summarize(health))
            return 0
        if phase in {"ERROR", "PITCH_LIMIT_REACHED", "ROLL_LIMIT_REACHED"}:
            print(summarize(health))
            raise PilotError(f"pivot ended in {phase}")
    raise PilotError(
        f"pivot status did not become terminal after the Pi-owned "
        f"{YAW_STEP_PI_DEADLINE_S:.0f}s deadline"
    )


def _stop_and_disarm(edge: Edge, last_sequence: int) -> None:
    try:
        edge.stop(last_sequence + 1)
    finally:
        edge.disarm()


def do_recover(edge: Edge, args) -> int:
    health = edge.health()
    if not health.get("armed"):
        imu = health.get("imu", {})
        recoverable_watchdog_error = (
            str(imu.get("phase")) == "ERROR"
            and str(imu.get("reason")) == "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"
            and imu.get("calibrated")
            and imu.get("valid")
        )
        if recoverable_watchdog_error:
            raise PilotError(
                "watchdog recovery requires a manual re-arm after fresh IMU validation"
            )
        raise PilotError("recovery requires an armed controller")
    last_sequence = int(health.get("lastSequence", 0))
    try:
        edge.heartbeat()
        started = edge.imu_recover()
        if started.get("phase") != "PRIMARY_YAW":
            raise PilotError(f"recovery rejected: {started}")
        print("recovery started: primary yaw -> roll -> final yaw")
        heartbeat_keeper = _HeartbeatKeeper(edge)
        heartbeat_keeper.start()

        try:
            deadline = time.monotonic() + args.timeout
            last_phase = ""
            while time.monotonic() < deadline:
                time.sleep(0.2)
                heartbeat_keeper.check()
                health = edge.health()
                heartbeat_keeper.check()
                last_sequence = int(health.get("lastSequence", last_sequence))
                phase = str(health.get("imu", {}).get("phase"))
                if phase != last_phase:
                    print(f"  {phase}")
                    last_phase = phase
                if phase == "COMPLETED":
                    print(summarize(health))
                    print("recovery complete — run 'recal' to re-reference the IMU")
                    return 0
                if phase == "ERROR":
                    print(summarize(health))
                    raise PilotError("recovery ended in ERROR")
            raise PilotError(f"recovery did not complete within {args.timeout}s")
        finally:
            heartbeat_keeper.close()
    except Exception:
        _stop_and_disarm(edge, last_sequence)
        raise


def do_recover_path(edge: Edge, args) -> int:
    """Start one bounded Pi-owned recovery; this client only polls status."""
    deadline_s = args.timeout
    if (
        not isinstance(deadline_s, (int, float))
        or not math.isfinite(deadline_s)
        or not 0 < deadline_s <= 60.0
    ):
        raise PilotError("recover-path timeout must be between 0 and 60 seconds")
    wait_for_imu_valid(edge, rearm=True)
    started = edge.recover_path(float(deadline_s))
    if (
        started.get("phase") != "PRIMARY_YAW"
        or started.get("localOperation") != "RECOVER_PATH"
    ):
        raise PilotError(f"recover-path rejected: {started}")
    print("Pi-local recover-path started: primary yaw -> roll -> final yaw")

    client_deadline = time.monotonic() + float(deadline_s) + 2.0
    last_phase = ""
    while time.monotonic() < client_deadline:
        time.sleep(0.2)
        health = edge.health()
        imu = health.get("imu", {})
        phase = str(imu.get("phase"))
        if phase != last_phase:
            print(f"  {phase}")
            last_phase = phase
        if phase == "COMPLETED":
            print(summarize(health))
            return 0
        if phase == "ERROR":
            raise PilotError(f"recover-path ended in ERROR: {imu.get('reason')}")
    raise PilotError("recover-path status did not become terminal after its Pi deadline")


def do_export_reference(edge: Edge, args) -> int:
    """Capture the live relative checkpoint pose before replacing the Pi process."""
    health = wait_for_imu_valid(edge, rearm=False)
    imu = health.get("imu", {})
    if health.get("armed"):
        raise PilotError("reference export requires STOP / disarmed")
    if (
        not health.get("ok")
        or not imu.get("available")
        or not imu.get("calibrated")
        or str(imu.get("phase")) != "COMPLETED"
        or not imu.get("valid")
    ):
        raise PilotError("reference export requires fresh calibrated COMPLETED IMU state")
    age = imu.get("ageS")
    if not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= 0.2:
        raise PilotError("reference export IMU sample is stale or invalid")
    pose = [imu.get(name) for name in ("yawDeg", "rollDeg", "pitchDeg")]
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in pose
    ):
        raise PilotError("reference export relative pose is invalid")
    yaw, roll, pitch = map(float, pose)
    if not -180.0 <= yaw <= 180.0:
        raise PilotError("reference export yaw is outside wrapped range")
    state = {
        "version": 1,
        "nonce": uuid.uuid4().hex,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "phase": "COMPLETED",
        "relativeYawDeg": yaw,
        "relativeRollDeg": roll,
        "relativePitchDeg": pitch,
    }
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    state["authTag"] = hmac.new(
        edge.token.encode("utf-8"), canonical, hashlib.sha256
    ).hexdigest()
    destination = Path(args.out)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(f"exported one-shot reference transfer to {destination}")
    return 0


def do_restore_reference(edge: Edge, args) -> int:
    """Consume a checkpoint transfer after restart and verify relative-pose continuity."""
    transfer = Path(args.file)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(transfer, os.O_RDONLY | os.O_NOFOLLOW)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise PilotError("reference transfer must be a regular file")
            if opened.st_uid != os.getuid():
                raise PilotError("reference transfer must be owned by the current uid")
            if stat.S_IMODE(opened.st_mode) != 0o600:
                raise PilotError("reference transfer must have exact mode 0600")
            with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except PilotError:
            raise
        except (OSError, ValueError) as error:
            raise PilotError(f"cannot read reference transfer: {error}") from error
        if not isinstance(state, dict):
            raise PilotError("reference transfer must be a JSON object")
        restored = edge.imu_restore_reference(state)
        if restored.get("phase") != "COMPLETED" or not restored.get("referenceRestored"):
            raise PilotError(f"reference restore rejected: {restored}")
        health = edge.health()
        imu = health.get("imu", {})
        if health.get("armed") or imu.get("phase") != "COMPLETED" or not imu.get("referenceRestored"):
            raise PilotError("reference restore read-back did not verify disarmed COMPLETED state")
        expected_names = (
            ("relativeYawDeg", "yawDeg"),
            ("relativeRollDeg", "rollDeg"),
            ("relativePitchDeg", "pitchDeg"),
        )
        for state_name, health_name in expected_names:
            if state_name in state:
                actual = imu.get(health_name)
                if not isinstance(actual, (int, float)) or abs(
                    float(actual) - float(state[state_name])
                ) > 0.5:
                    raise PilotError(
                        f"reference restore read-back mismatch for {health_name}"
                    )
        try:
            current = os.lstat(transfer)
        except OSError as error:
            raise PilotError(f"reference transfer changed during restore: {error}") from error
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise PilotError("reference transfer changed during restore")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    transfer.unlink()
    print(summarize(health))
    print("reference transfer verified and one-shot file deleted")
    return 0


def do_yaw_scan_360(edge: Edge, args) -> int:
    """Start one bounded Pi-owned clockwise full turn; this client only polls."""
    deadline_s = args.timeout
    if (
        not isinstance(deadline_s, (int, float))
        or not math.isfinite(deadline_s)
        or not 0 < deadline_s <= 60.0
    ):
        raise PilotError("yaw-scan-360 timeout must be between 0 and 60 seconds")
    wait_for_imu_valid(edge, rearm=True)
    started = edge.yaw_scan_360(float(deadline_s))
    if (
        started.get("phase") != "YAW_SCAN_360"
        or started.get("localOperation") != "YAW_SCAN_360"
    ):
        raise PilotError(f"yaw-scan-360 rejected: {started}")
    print("Pi-local RIGHT yaw scan started (opposite wheel directions)")
    client_deadline = time.monotonic() + float(deadline_s) + 2.0
    while time.monotonic() < client_deadline:
        time.sleep(0.2)
        health = edge.health()
        imu = health.get("imu", {})
        phase = str(imu.get("phase"))
        if phase == "COMPLETED":
            accumulated = imu.get("scanAccumulatedClockwiseDeg")
            yaw = imu.get("yawDeg")
            accumulated_valid = (
                isinstance(accumulated, (int, float))
                and not isinstance(accumulated, bool)
                and math.isfinite(accumulated)
                and float(accumulated) >= 357.0
            )
            yaw_valid = (
                isinstance(yaw, (int, float))
                and not isinstance(yaw, bool)
                and math.isfinite(yaw)
                and abs(float(yaw)) <= 3.0
            )
            if (
                health.get("armed") is not False
                or health.get("lease") is not None
                or imu.get("localOperation") is not None
                or imu.get("reason") != "V2_YAW_SCAN_360_COMPLETED"
                or not accumulated_valid
                or not yaw_valid
            ):
                raise PilotError("yaw-scan-360 terminal state failed safety verification")
            print(summarize(health))
            return 0
        if phase == "ERROR":
            raise PilotError(f"yaw-scan-360 ended in ERROR: {imu.get('reason')}")
    raise PilotError("yaw-scan-360 status did not become terminal after its Pi deadline")


def do_yaw_temp_180(edge: Edge, args) -> int:
    """Continuously pivot LEFT at 0.80 until opposite the saved AVOIDANCE yaw."""
    deadline_s = args.timeout
    if not isinstance(deadline_s, (int, float)) or not 0 < deadline_s <= 60.0:
        raise PilotError("yaw-temp-180 timeout must be between 0 and 60 seconds")
    wait_for_imu_valid(edge, rearm=True)
    started = edge.yaw_temp_180(float(deadline_s))
    if started.get("localOperation") != "YAW_TEMP_180":
        raise PilotError(f"yaw-temp-180 rejected: {started}")
    client_deadline = time.monotonic() + float(deadline_s) + 2.0
    while time.monotonic() < client_deadline:
        time.sleep(0.2)
        health = edge.health()
        imu = health.get("imu", {})
        if imu.get("phase") == "COMPLETED":
            if (
                health.get("armed") is not False
                or health.get("lease") is not None
                or imu.get("localOperation") is not None
                or imu.get("reason") != "V2_YAW_TEMP_180_COMPLETED"
            ):
                raise PilotError("yaw-temp-180 terminal state failed safety verification")
            print(summarize(health))
            return 0
        if imu.get("phase") == "ERROR":
            raise PilotError(f"yaw-temp-180 ended in ERROR: {imu.get('reason')}")
    raise PilotError("yaw-temp-180 status did not become terminal after its Pi deadline")


def do_pitch_guard(edge: Edge, _args) -> int:
    """Enable pitch guard mode for obstacle avoidance."""
    wait_for_motion_preflight(edge, forward=False)

    response = edge.imu_pitch_guard()
    if response.get("phase") != "PITCH_GUARD":
        raise PilotError(f"pitch guard rejected: {response}")
    print("pitch guard enabled")
    print(summarize(edge.health()))
    return 0


def do_recal(edge: Edge, args) -> int:
    """Re-reference the IMU after a completed recovery, staying armed."""
    health = edge.health()
    if str(health.get("imu", {}).get("phase")) != "COMPLETED":
        raise PilotError("recal only applies after a COMPLETED recovery")
    response = edge.imu_calibrate(args.samples)
    if not response.get("calibrated") or response.get("phase") != "READY":
        raise PilotError(f"recalibration rejected: {response}")
    print(summarize(edge.health()))
    return 0


def do_stop(edge: Edge, _args) -> int:
    health = edge.health()
    ack = edge.stop(int(health.get("lastSequence", 0)) + 1)
    print(f"stop -> {ack.get('reason')}")
    print(summarize(edge.health()))
    return 0


def do_end(edge: Edge, _args) -> int:
    health = edge.health()
    edge.stop(int(health.get("lastSequence", 0)) + 1)
    edge.disarm()
    print(summarize(edge.health()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="/home/ssafy/S15P11C102/ai/AIAgent/.env")
    parser.add_argument(
        "--camera",
        default="http://192.168.100.246:8081/snapshot.jpg",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="print edge health").set_defaults(func=do_status)

    snap = subparsers.add_parser("snap", help="save one camera frame")
    snap.add_argument(
        "out",
        nargs="?",
        default="/tmp/pipecheckkk-vl-snapshot.jpg",
    )
    snap.set_defaults(func=do_snap)

    arm = subparsers.add_parser("arm", help="calibrate the IMU, then arm")
    arm.add_argument("--samples", type=int, default=100)
    arm.set_defaults(func=do_arm)

    subparsers.add_parser(
        "resume",
        help="arm for another approved step without recalibrating the IMU",
    ).set_defaults(func=do_resume)

    fwd = subparsers.add_parser("fwd", help="one forward burst, auto-stopped by TTL")
    fwd.add_argument("--speed", type=float, default=0.40)
    fwd.add_argument("--ms", type=int, default=600)
    fwd.set_defaults(func=do_fwd)

    fwd_timed = subparsers.add_parser(
        "fwd-timed-continuous",
        help="Pi-local uninterrupted forward motion for a fixed duration",
    )
    fwd_timed.add_argument("--speed", type=float, default=0.40)
    fwd_timed.add_argument("--seconds", type=float, required=True)
    fwd_timed.set_defaults(func=do_fwd_timed_continuous)

    fwd_continuous = subparsers.add_parser(
        "fwd-continuous-until-distance",
        help="Pi-local uninterrupted forward motion until ultrasonic target",
    )
    fwd_continuous.add_argument("--speed", type=float, default=0.40)
    fwd_continuous.add_argument("--distance", type=float, required=True)
    fwd_continuous.add_argument("--timeout", type=float, default=60.0)
    fwd_continuous.add_argument("--complete-on-timeout", action="store_true")
    fwd_continuous.set_defaults(func=do_fwd_continuous_until_distance)

    fwd_distance = subparsers.add_parser(
        "fwd-until-distance",
        help="bounded forward bursts until ultrasonic distance is reached",
    )
    fwd_distance.add_argument("--speed", type=float, default=0.40)
    fwd_distance.add_argument("--distance", type=float, required=True)
    fwd_distance.add_argument("--timeout", type=float, default=8.0)
    fwd_distance.add_argument("--burst-ms", type=int, default=None)
    fwd_distance.set_defaults(func=do_fwd_until_distance)


    turn = subparsers.add_parser("turn", help="one 10 degree in-place yaw step")
    turn.add_argument("direction")
    turn.add_argument("--timeout", type=float, default=15.0)
    turn.set_defaults(func=do_turn, relative=False, current=False)

    current_turn = subparsers.add_parser(
        "turn-current", help="one 10 degree yaw step from the current live IMU yaw"
    )
    current_turn.add_argument("direction")
    current_turn.add_argument("--degrees", type=float, default=10.0)
    current_turn.add_argument("--timeout", type=float, default=15.0)
    current_turn.set_defaults(func=do_turn, relative=False, current=True)

    temp_ref = subparsers.add_parser(
        "temp-ref-capture", help="save current IMU values without recalibration"
    )
    temp_ref.add_argument("name")
    temp_ref.set_defaults(func=do_temp_ref_capture)

    subparsers.add_parser(
        "temp-ref-clear", help="delete the saved temporary IMU reference"
    ).set_defaults(func=do_temp_ref_clear)

    relative_turn = subparsers.add_parser(
        "turn-relative", help="one 10 degree yaw step from saved temporary IMU values"
    )
    relative_turn.add_argument("direction")
    relative_turn.add_argument("--timeout", type=float, default=15.0)
    relative_turn.set_defaults(func=do_turn, relative=True)

    recover = subparsers.add_parser("recover", help="yaw -> roll -> yaw pose recovery")
    recover.add_argument("--timeout", type=float, default=60.0)
    recover.set_defaults(func=do_recover)

    recover_path = subparsers.add_parser(
        "recover-path",
        help="one heartbeat-independent, Pi-local bounded pose recovery",
    )
    recover_path.add_argument("--timeout", type=float, default=60.0)
    recover_path.set_defaults(func=do_recover_path)

    export_reference = subparsers.add_parser(
        "export-reference",
        help="capture a one-shot COMPLETED IMU transfer before Pi restart",
    )
    export_reference.add_argument("out")
    export_reference.set_defaults(func=do_export_reference)

    restore_reference = subparsers.add_parser(
        "restore-reference",
        help="restore and consume a checkpoint IMU transfer after Pi restart",
    )
    restore_reference.add_argument("file")
    restore_reference.set_defaults(func=do_restore_reference)

    yaw_scan = subparsers.add_parser(
        "yaw-scan-360",
        help="Pi-local bounded RIGHT full-turn scan; no client heartbeat",
    )
    yaw_scan.add_argument("--timeout", type=float, default=60.0)
    yaw_scan.set_defaults(func=do_yaw_scan_360)

    yaw_temp = subparsers.add_parser(
        "yaw-temp-180",
        help="Pi-local LEFT 0.80 pivot to 180 degrees from AVOIDANCE TEMP_IMU",
    )
    yaw_temp.add_argument("--timeout", type=float, default=60.0)
    yaw_temp.set_defaults(func=do_yaw_temp_180)

    subparsers.add_parser("pitch-guard", help="enable pitch guard for obstacle avoidance").set_defaults(func=do_pitch_guard)

    recal = subparsers.add_parser("recal", help="re-reference the IMU after recovery")
    recal.add_argument("--samples", type=int, default=100)
    recal.set_defaults(func=do_recal)

    subparsers.add_parser("stop", help="stop the motors").set_defaults(func=do_stop)
    subparsers.add_parser("end", help="stop and disarm").set_defaults(func=do_end)

    args = parser.parse_args()
    try:
        url, token = load_env(Path(args.env))
        return args.func(Edge(url, token), args)
    except PilotError as error:
        print(f"PILOT ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
