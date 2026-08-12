#!/usr/bin/env python3
"""Deterministic, single-process orchestration for inspection record 001.

Hermes invokes this runner once. The runner—not the language model—owns the
fixed action order. Every action remains bounded by ``vl_pilot.py`` and is
preceded by the operator-visible two-line narration. Any delivery or command
failure ends in STOP/disarm without retrying physical motion.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, NamedTuple
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "scripts" / "vl_pilot.py"
PYTHON = ROOT.parent / ".venv" / "bin" / "python"
DEFAULT_RECORD = ROOT / "run_records" / "inspection-record-001.json"
DEFAULT_TARGET = "discord:1534801753537511484:1534833329415655435"
LOCK = Path("/tmp/pipecheckkk-vl-replay-record.lock")


class ReplayError(RuntimeError):
    pass


class ReplayStep(NamedTuple):
    number: int
    scene: str
    response: str
    pre_command: tuple[str, ...] | None
    pre_command_repetitions: int
    pre_command_degrees: tuple[float, ...]
    imu_reinitialize_before: bool
    imu_samples: int
    command: tuple[str, ...]
    continue_after_completed_failure: bool

    def message(self, sensor_summary: str) -> str:
        scene = self.scene.rstrip(" .")
        return (
            f"이미지 분석 : {scene}, 현재 {sensor_summary}\n"
            f"대처 동작 : {self.response.strip()}"
        )


CommandRunner = Callable[[tuple[str, ...], float], str]
MessageSender = Callable[[int, str], None]
StagePublisher = Callable[[ReplayStep, str], None]


def _number(value: object, *, digits: int = 0) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ReplayError(f"record action contains an invalid numeric value: {value!r}")
    if digits:
        return f"{float(value):.{digits}f}"
    return str(int(value)) if float(value).is_integer() else str(value)


def _compile_command(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, dict) or not (
        raw.get("executed") is True
        or raw.get("completed") is True
        or raw.get("planned") is True
    ):
        raise ReplayError("every replay step must contain one completed action")
    action = str(raw.get("action", ""))
    if action == "TURN_LEFT":
        return ("turn", "LEFT", "--timeout", "30")
    if action == "TURN_RIGHT":
        return ("turn", "RIGHT", "--timeout", "30")
    if action == "PUBLISH_BLOCKAGE_THEN_CONTINUOUS_TEMP_YAW":
        return ("yaw-temp-180", "--timeout", "60")
    if action == "TURN_LEFT_BY_TEMP_YAW":
        return ("turn-relative", "LEFT", "--timeout", "15")
    if action in {"FORWARD_TIMED_BURST", "FORWARD"}:
        speed = _number(raw.get("speed"), digits=2)
        duration_ms = _number(raw.get("duration_ms"))
        return ("fwd", "--speed", speed, "--ms", duration_ms)
    if action == "CONTINUOUS_FORWARD_TIMED":
        speed = _number(raw.get("speed"), digits=2)
        duration_s = _number(raw.get("duration_s"))
        return (
            "fwd-timed-continuous",
            "--speed",
            speed,
            "--seconds",
            duration_s,
        )
    if action == "CONTINUOUS_FORWARD_UNTIL_DISTANCE":
        speed = _number(raw.get("speed"), digits=2)
        distance = _number(raw.get("target_distance_m"), digits=2)
        deadline_s = _number(raw.get("deadline_s"))
        command = (
            "fwd-continuous-until-distance",
            "--speed",
            speed,
            "--distance",
            distance,
            "--timeout",
            deadline_s,
        )
        if raw.get("complete_on_timeout") is True:
            command += ("--complete-on-timeout",)
        return command
    if action == "FORWARD_UNTIL_DISTANCE":
        speed = _number(raw.get("speed"), digits=2)
        distance = _number(raw.get("target_distance_m"), digits=2)
        max_duration_ms = float(_number(raw.get("max_duration_ms")))
        command = (
            "fwd-until-distance",
            "--speed",
            speed,
            "--distance",
            distance,
            "--timeout",
            _number(max_duration_ms / 1000.0),
        )
        burst_duration_ms = raw.get("burst_duration_ms")
        if burst_duration_ms is not None:
            command += ("--burst-ms", _number(burst_duration_ms))
        return command
    if action == "RECOVER_PATH":
        return ("recover-path", "--timeout", _number(raw.get("deadline_s")))
    if action == "YAW_SCAN_360":
        return ("yaw-scan-360", "--timeout", _number(raw.get("deadline_s")))
    raise ReplayError(f"unsupported replay action: {action or '<missing>'}")


def _compile_pre_command(raw: object) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ReplayError("record pre_action must be an object")
    action = str(raw.get("action", ""))
    if action == "IMU_RECALIBRATE":
        samples = _number(raw.get("samples", 100))
        return ("recal", "--samples", samples)
    if action == "IMU_TEMP_REFERENCE_CAPTURE":
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ReplayError("temporary IMU reference name is required")
        return ("temp-ref-capture", name.strip().upper())
    if action == "IMU_TEMP_REFERENCE_CLEAR":
        return ("temp-ref-clear",)
    if action == "IMU_CURRENT_YAW_TURN":
        direction = str(raw.get("direction", "")).upper()
        if direction not in {"LEFT", "RIGHT"}:
            raise ReplayError("current yaw turn direction must be LEFT or RIGHT")
        return (
            "turn-current",
            direction,
            "--timeout",
            _number(raw.get("timeout_s", 30.0)),
        )
    raise ReplayError(f"unsupported replay pre_action: {action or '<missing>'}")


def _compile_pre_command_repetitions(raw: object) -> int:
    if not isinstance(raw, dict) or raw.get("action") != "IMU_CURRENT_YAW_TURN":
        return 1
    degrees = raw.get("degrees", 10.0)
    if degrees not in {10, 10.0, 20, 20.0, 30, 30.0}:
        raise ReplayError("current yaw turn degrees must be 10, 20, or 30")
    return int(float(degrees) / 10.0)


def _compile_pre_command_degrees(raw: object) -> tuple[float, ...]:
    if not isinstance(raw, dict) or raw.get("action") != "IMU_CURRENT_YAW_TURN":
        return ()
    sequence = raw.get("degrees_sequence", [10.0])
    if not isinstance(sequence, list) or not sequence:
        raise ReplayError("current yaw degrees_sequence must be a non-empty list")
    degrees = tuple(float(value) for value in sequence)
    if any(value not in {5.0, 10.0} for value in degrees):
        raise ReplayError("current yaw sequence supports only 5 or 10 degree steps")
    return degrees


def load_plan(record_path: Path) -> list[ReplayStep]:
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    checkpoint = payload.get("checkpoint_after_step")
    replay_through = payload.get("replay_through_step", checkpoint)
    steps = payload.get("steps")
    if not isinstance(checkpoint, int) or checkpoint <= 0 or not isinstance(steps, list):
        raise ReplayError("record is missing a valid checkpoint or steps")
    if not isinstance(replay_through, int) or replay_through < checkpoint:
        raise ReplayError("record replay_through_step is invalid")
    selected = steps[:replay_through]
    if len(selected) != replay_through:
        raise ReplayError("record checkpoint exceeds the available steps")

    plan: list[ReplayStep] = []
    for expected, raw_step in enumerate(selected, start=1):
        if not isinstance(raw_step, dict) or raw_step.get("step") != expected:
            raise ReplayError("record steps must be contiguous and ordered from 1")
        scene = raw_step.get("operator_scene")
        response = raw_step.get("operator_response")
        if not isinstance(scene, str) or not scene.strip():
            raise ReplayError(f"step {expected} is missing operator_scene")
        if not isinstance(response, str) or not response.strip():
            raise ReplayError(f"step {expected} is missing operator_response")
        plan.append(
            ReplayStep(
                number=expected,
                scene=scene,
                response=response,
                pre_command=_compile_pre_command(raw_step.get("pre_action")),
                pre_command_repetitions=_compile_pre_command_repetitions(
                    raw_step.get("pre_action")
                ),
                pre_command_degrees=_compile_pre_command_degrees(
                    raw_step.get("pre_action")
                ),
                imu_reinitialize_before=bool(
                    isinstance(raw_step.get("pre_action"), dict)
                    and raw_step["pre_action"].get("imu_reinitialize_before") is True
                ),
                imu_samples=int(
                    raw_step.get("pre_action", {}).get("imu_samples", 100)
                    if isinstance(raw_step.get("pre_action"), dict)
                    else 100
                ),
                command=_compile_command(raw_step.get("executed_action")),
                continue_after_completed_failure=bool(
                    raw_step.get("continue_after_completed_failure", False)
                ),
            )
        )
    return plan


def sensor_summary(command_output: str) -> str:
    front = re.search(r"front=([0-9.]+|--)m", command_output)
    yaw = re.search(r"\byaw=(-?[0-9.]+)", command_output)
    roll = re.search(r"\broll=(-?[0-9.]+)", command_output)
    pitch = re.search(r"\bpitch=(-?[0-9.]+)", command_output)
    if front is None or yaw is None or roll is None or pitch is None:
        raise ReplayError("status output is missing current telemetry fields")
    front_summary = "unavailable" if front.group(1) == "--" else f"{front.group(1)}m"
    return (
        f"front={front_summary}, IMU yaw={yaw.group(1)}°, "
        f"roll={roll.group(1)}°, pitch={pitch.group(1)}°"
    )


def _motion_timeout(command: tuple[str, ...]) -> float:
    if command[0] in {"recover-path", "yaw-scan-360", "yaw-temp-180"}:
        return 75.0
    if command[0] == "turn":
        return 45.0
    return 30.0


def _is_safe_completed_failure_status(status: str) -> bool:
    return (
        "armed=False" in status
        and "estop=False" in status
        and "imu COMPLETED" in status
        and "valid=True" in status
    )


def run_replay(
    plan: list[ReplayStep],
    *,
    run_command: CommandRunner,
    send_message: MessageSender,
    publish_stage: StagePublisher = lambda _step, _sensor: None,
) -> dict[str, object]:
    completed: list[int] = []
    continued_after_failed: list[int] = []
    try:
        run_command(("arm", "--samples", "100"), 45.0)
        latest_status = run_command(("status",), 15.0)

        for step in plan:
            sensor = sensor_summary(latest_status)
            try:
                publish_stage(step, sensor)
            except Exception as error:
                print(
                    f"dashboard stage publish unavailable at step {step.number}: "
                    f"{type(error).__name__}",
                    file=sys.stderr,
                )
            send_message(step.number, step.message(sensor))
            if step.pre_command is not None and step.pre_command[0] == "turn-current":
                if step.imu_reinitialize_before:
                    run_command(("recal", "--samples", str(step.imu_samples)), 45.0)
                run_command(("resume",), 15.0)
                for degrees in step.pre_command_degrees:
                    run_command(
                        (
                            "turn-current",
                            step.pre_command[1],
                            "--degrees",
                            _number(degrees),
                            "--timeout",
                            step.pre_command[-1],
                        ),
                        45.0,
                    )
            else:
                if step.pre_command is not None:
                    run_command(step.pre_command, 45.0)
                run_command(("resume",), 15.0)
            try:
                run_command(step.command, _motion_timeout(step.command))
            except Exception:
                if not step.continue_after_completed_failure:
                    raise
                run_command(("stop",), 15.0)
                latest_status = run_command(("status",), 15.0)
                if not _is_safe_completed_failure_status(latest_status):
                    raise
                continued_after_failed.append(step.number)
                continue
            run_command(("stop",), 15.0)
            latest_status = run_command(("status",), 15.0)
            completed.append(step.number)
    except BaseException:
        try:
            run_command(("stop",), 15.0)
        except Exception:
            pass
        raise
    finally:
        run_command(("end",), 15.0)

    return {
        "ok": True,
        "completed_steps": completed,
        "continued_after_failed_steps": continued_after_failed,
        "final_state": "STOP_DISARMED",
    }


def subprocess_runner(command: tuple[str, ...], timeout: float) -> str:
    process = subprocess.run(
        [str(PYTHON), str(PILOT), *command],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    output = process.stdout.strip()
    if process.returncode:
        raise ReplayError(f"{' '.join(command)} failed ({process.returncode}): {output}")
    return output


def discord_sender(target: str, operation_id: str) -> MessageSender:
    safe_operation_id = re.sub(r"[^A-Za-z0-9_.-]", "_", operation_id)[:64]

    def send(step: int, message: str) -> None:
        evidence = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"pipecheckkk-vl-{safe_operation_id}-step-{step:02d}-",
            suffix=".txt",
            delete=False,
        )
        path = Path(evidence.name)
        try:
            with evidence:
                evidence.write(message + "\n")
            process = subprocess.run(
                ["hermes", "send", "--to", target, "--file", str(path), "--quiet"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30.0,
                check=False,
            )
            if process.returncode:
                raise ReplayError(
                    f"Discord delivery failed at step {step} ({process.returncode}): "
                    f"{process.stdout.strip()}"
                )
        finally:
            path.unlink(missing_ok=True)

    return send


def _agent_stage_publisher(agent_url: str) -> StagePublisher:
    endpoint = f"{agent_url.rstrip('/')}/api/hermes/stage"

    def publish(step: ReplayStep, sensor: str) -> None:
        payload = json.dumps(
            {
                "analysis": f"{step.scene.rstrip(' .')}, 현재 {sensor}",
                "action": step.response,
                "step": step.number,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=2.0) as response:
            if response.status != 202:
                raise ReplayError(f"Agent stage publish returned HTTP {response.status}")

    return publish


def _notify_agent_completion(agent_url: str, operation_id: str, message: str) -> None:
    payload = json.dumps(
        {"operation_id": operation_id, "message": message},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        f"{agent_url.rstrip('/')}/api/hermes/complete",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=2.0) as response:
        if response.status != 200:
            raise ReplayError(f"Agent completion returned HTTP {response.status}")


def _notify_agent_completion_best_effort(
    agent_url: str, operation_id: str, message: str
) -> None:
    try:
        _notify_agent_completion(agent_url, operation_id, message)
    except Exception as error:
        print(
            f"agent completion callback unavailable: {type(error).__name__}",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--discord-target", default=DEFAULT_TARGET)
    parser.add_argument("--agent-url", default="http://127.0.0.1:8000")
    parser.add_argument("--operation-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--record-maintenance",
        action="store_true",
        help="run physical replay without Dashboard/Qwen/Discord stage publication",
    )
    args = parser.parse_args()

    plan = load_plan(args.record)
    operation_id = args.operation_id or uuid.uuid4().hex
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "operation_id": operation_id,
                    "steps": [
                        {"step": step.number, "command": list(step.command)} for step in plan
                    ],
                    "calibration_count": 1,
                    "delivery_before_each_motion": True,
                    "final_state": "STOP_DISARMED",
                },
                ensure_ascii=False,
            )
        )
        return 0

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(
        LOCK,
        os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(lock_fd, "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": False, "error": "inspection already running"}))
            return 2
        started = time.monotonic()
        try:
            result = run_replay(
                plan,
                run_command=subprocess_runner,
                send_message=(
                    (lambda _step, _message: None)
                    if args.record_maintenance
                    else discord_sender(args.discord_target, operation_id)
                ),
                publish_stage=(
                    (lambda _step, _sensor: None)
                    if args.record_maintenance
                    else _agent_stage_publisher(args.agent_url)
                ),
            )
        except Exception as error:
            if not args.record_maintenance:
                _notify_agent_completion_best_effort(
                    args.agent_url, operation_id, "STOP_DISARM_REQUESTED"
                )
            print(
                json.dumps(
                    {
                        "ok": False,
                        "operation_id": operation_id,
                        "error": str(error),
                        "final_state": "STOP_DISARM_REQUESTED",
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        if not args.record_maintenance:
            _notify_agent_completion_best_effort(
                args.agent_url, operation_id, "STOP_DISARMED"
            )
        result["operation_id"] = operation_id
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        print(json.dumps(result, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
