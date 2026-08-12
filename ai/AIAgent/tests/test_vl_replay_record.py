from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "vl_replay_record.py"
SPEC = importlib.util.spec_from_file_location("vl_replay_record", SCRIPT)
assert SPEC and SPEC.loader
vl_replay_record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vl_replay_record)

RECORD = Path(__file__).parents[1] / "run_records" / "inspection-record-001.json"
STATUS = "armed=False estop=False front=1.150m seq=1 | imu READY yaw=0.0 roll=0.0 pitch=0.0 target=0.0 valid=True age=0.001s reason=READY"


def test_agent_completion_notifier_sends_operation_identity(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(vl_replay_record.request, "urlopen", fake_urlopen)

    vl_replay_record._notify_agent_completion(
        "http://127.0.0.1:8000", "operation-18", "STOP_DISARMED"
    )

    assert captured == {
        "url": "http://127.0.0.1:8000/api/hermes/complete",
        "payload": {"operation_id": "operation-18", "message": "STOP_DISARMED"},
        "timeout": 2.0,
    }


def test_discord_evidence_uses_secure_temp_file_and_removes_it_on_failure(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}

    class FailedProcess:
        returncode = 1
        stdout = "delivery rejected"

    def fake_run(command, **_kwargs):
        evidence = Path(command[command.index("--file") + 1])
        captured["path"] = evidence
        captured["mode"] = evidence.stat().st_mode & 0o777
        captured["content"] = evidence.read_text(encoding="utf-8")
        return FailedProcess()

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(vl_replay_record.subprocess, "run", fake_run)
    sender = vl_replay_record.discord_sender("discord:test", "operation-test")

    try:
        with pytest.raises(vl_replay_record.ReplayError, match="Discord delivery failed"):
            sender(3, "stage evidence")

        evidence = captured["path"]
        assert isinstance(evidence, Path)
        assert evidence.parent == tmp_path
        assert captured["mode"] == 0o600
        assert captured["content"] == "stage evidence\n"
        assert not evidence.exists()
    finally:
        stale = captured.get("path")
        if isinstance(stale, Path):
            stale.unlink(missing_ok=True)


def test_main_refuses_symlink_lock_without_modifying_target(
    monkeypatch, tmp_path
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("do not truncate", encoding="utf-8")
    lock = tmp_path / "replay.lock"
    lock.symlink_to(victim)

    monkeypatch.setattr(vl_replay_record, "LOCK", lock)
    monkeypatch.setattr(vl_replay_record, "load_plan", lambda _record: [])
    monkeypatch.setattr(
        vl_replay_record,
        "run_replay",
        lambda *_args, **_kwargs: {"ok": True, "final_state": "STOP_DISARMED"},
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--record-maintenance"])

    with pytest.raises(OSError):
        vl_replay_record.main()

    assert victim.read_text(encoding="utf-8") == "do not truncate"


def test_record_001_compiles_to_the_exact_twenty_two_bounded_actions() -> None:
    plan = vl_replay_record.load_plan(RECORD)

    assert [step.number for step in plan] == list(range(1, 23))
    assert [step.command for step in plan] == [
        ("turn", "LEFT", "--timeout", "30"),
        ("turn", "LEFT", "--timeout", "30"),
        ("turn", "RIGHT", "--timeout", "30"),
        ("turn", "RIGHT", "--timeout", "30"),
        ("turn", "RIGHT", "--timeout", "30"),
        ("turn", "RIGHT", "--timeout", "30"),
        ("fwd", "--speed", "0.40", "--ms", "600"),
        ("fwd", "--speed", "0.40", "--ms", "1500"),
        ("fwd", "--speed", "0.40", "--ms", "1500"),
        ("fwd", "--speed", "0.40", "--ms", "1500"),
        ("recover-path", "--timeout", "60"),
        ("yaw-scan-360", "--timeout", "60"),
        ("fwd", "--speed", "0.40", "--ms", "1500"),
        ("fwd", "--speed", "0.40", "--ms", "1500"),
        ("fwd", "--speed", "0.40", "--ms", "2500"),
        ("fwd", "--speed", "0.40", "--ms", "2000"),
        ("fwd", "--speed", "0.40", "--ms", "2000"),
        ("turn-relative", "LEFT", "--timeout", "15"),
        ("fwd-until-distance", "--speed", "0.40", "--distance", "0.30", "--timeout", "30", "--burst-ms", "3000"),
        ("yaw-temp-180", "--timeout", "60"),
        ("fwd-continuous-until-distance", "--speed", "0.40", "--distance", "0.20", "--timeout", "9", "--complete-on-timeout"),
        ("fwd-continuous-until-distance", "--speed", "0.40", "--distance", "0.20", "--timeout", "5", "--complete-on-timeout"),
    ]
    assert [step.pre_command for step in plan[:12]] == [None] * 12
    assert plan[12].pre_command == ("recal", "--samples", "100")
    assert plan[13].pre_command is None
    assert plan[14].pre_command is None
    assert plan[15].pre_command is None
    assert plan[16].pre_command is None
    assert plan[17].pre_command == ("temp-ref-capture", "AVOIDANCE")
    assert plan[18].pre_command is None
    assert plan[19].pre_command is None
    assert plan[20].pre_command == ("temp-ref-clear",)
    assert plan[21].pre_command == ("turn-current", "LEFT", "--timeout", "30")
    assert plan[21].pre_command_degrees == (10.0, 10.0, 5.0)
    assert plan[21].imu_reinitialize_before is True
    assert plan[21].imu_samples == 100


def test_yaw_temp_outer_timeout_exceeds_its_internal_timeout() -> None:
    command = ("yaw-temp-180", "--timeout", "60")

    outer_timeout = vl_replay_record._motion_timeout(command)
    internal_timeout = float(command[command.index("--timeout") + 1])

    assert outer_timeout >= 75.0
    assert outer_timeout > internal_timeout


def test_sensor_summary_keeps_ultrasonic_invalid_as_telemetry_only() -> None:
    summary = vl_replay_record.sensor_summary(
        "armed=True estop=False front=--m seq=1 | "
        "imu READY yaw=1.0 roll=2.0 pitch=3.0 valid=True"
    )

    assert summary == "front=unavailable, IMU yaw=1.0°, roll=2.0°, pitch=3.0°"


def test_step_18_captures_temp_imu_then_turns_left_relative_to_it(tmp_path) -> None:
    raw = json.loads(RECORD.read_text())
    raw["checkpoint_after_step"] = 18
    raw["steps"][17]["executed_action"].update(
        {"executed": True, "completed": True, "status": "COMPLETED"}
    )
    candidate = tmp_path / "record.json"
    candidate.write_text(json.dumps(raw))

    step = vl_replay_record.load_plan(candidate)[17]

    assert step.pre_command == ("temp-ref-capture", "AVOIDANCE")
    assert step.command == ("turn-relative", "LEFT", "--timeout", "15")


def test_replay_sends_each_two_line_message_before_motion_and_stops_afterward() -> None:
    events: list[tuple[str, object]] = []

    def run_command(command: tuple[str, ...], _timeout: float) -> str:
        events.append(("command", command))
        return "armed=False estop=False front=1.150m seq=1 | imu READY yaw=0.0 roll=0.0 pitch=0.0 target=0.0 valid=True age=0.001s reason=READY"

    def send_message(step: int, message: str) -> None:
        events.append(("message", (step, message)))

    result = vl_replay_record.run_replay(
        vl_replay_record.load_plan(RECORD),
        run_command=run_command,
        send_message=send_message,
    )

    assert result["ok"] is True
    commands = [value for kind, value in events if kind == "command"]
    assert commands.count(("arm", "--samples", "100")) == 1
    assert commands.count(("turn-current", "LEFT", "--degrees", "10", "--timeout", "30")) == 2
    assert commands.count(("turn-current", "LEFT", "--degrees", "5", "--timeout", "30")) == 1
    assert commands[:2] == [("arm", "--samples", "100"), ("status",)]
    assert commands[-1] == ("end",)

    for step in vl_replay_record.load_plan(RECORD):
        message_index = events.index(("message", (step.number, step.message("front=1.150m, IMU yaw=0.0°, roll=0.0°, pitch=0.0°"))))
        resume_index = events.index(("command", ("resume",)), message_index)
        motion_index = events.index(("command", step.command), resume_index)
        stop_index = events.index(("command", ("stop",)), motion_index)
        status_index = events.index(("command", ("status",)), stop_index)
        if step.pre_command is not None:
            if step.pre_command[0] == "turn-current":
                first_turn = (
                    "turn-current",
                    step.pre_command[1],
                    "--degrees",
                    str(int(step.pre_command_degrees[0])),
                    "--timeout",
                    step.pre_command[-1],
                )
                pre_index = events.index(("command", first_turn), message_index)
                assert message_index < resume_index < pre_index < motion_index
            else:
                pre_index = events.index(("command", step.pre_command), message_index)
                assert message_index < pre_index < resume_index
        assert message_index < resume_index < motion_index < stop_index < status_index


def test_delivery_failure_aborts_before_motion_and_always_disarms() -> None:
    events: list[tuple[str, object]] = []

    def run_command(command: tuple[str, ...], _timeout: float) -> str:
        events.append(("command", command))
        return "armed=False estop=False front=1.150m seq=1 | imu READY yaw=0.0 roll=0.0 pitch=0.0 target=0.0 valid=True age=0.001s reason=READY"

    def fail_delivery(step: int, _message: str) -> None:
        events.append(("message", step))
        raise RuntimeError("Discord delivery failed")

    with pytest.raises(RuntimeError, match="Discord delivery failed"):
        vl_replay_record.run_replay(
            vl_replay_record.load_plan(RECORD),
            run_command=run_command,
            send_message=fail_delivery,
        )

    assert ("command", ("turn", "LEFT", "--timeout", "15")) not in events
    assert events[-1] == ("command", ("end",))


def test_step_12_completed_edge_state_continues_after_pilot_failure() -> None:
    events: list[tuple[str, object]] = []
    plan = vl_replay_record.load_plan(RECORD)

    assert plan[11].continue_after_completed_failure is True

    def run_command(command: tuple[str, ...], _timeout: float) -> str:
        events.append(("command", command))
        if command == plan[11].command:
            raise vl_replay_record.ReplayError("terminal state failed safety verification")
        if command == ("status",) and ("command", plan[11].command) in events:
            return "armed=False estop=False front=1.100m seq=1 | imu COMPLETED yaw=4.6 roll=-36.5 pitch=17.3 target=-20.0 valid=True age=0.001s reason=V2_YAW_SCAN_360_COMPLETED"
        return STATUS

    result = vl_replay_record.run_replay(
        plan,
        run_command=run_command,
        send_message=lambda _step, _message: None,
    )

    assert result["continued_after_failed_steps"] == [12]
    assert 12 not in result["completed_steps"]
    assert result["completed_steps"][-1] == 22
    assert ("command", plan[12].command) in events


def test_final_disarm_failure_raises_after_allowed_completed_failure() -> None:
    plan = vl_replay_record.load_plan(RECORD)

    def run_command(command: tuple[str, ...], _timeout: float) -> str:
        if command == plan[11].command:
            raise vl_replay_record.ReplayError("allowed completed failure")
        if command == ("end",):
            raise vl_replay_record.ReplayError("final disarm failed")
        if command == ("status",):
            return (
                "armed=False estop=False front=1.100m seq=1 | "
                "imu COMPLETED yaw=4.6 roll=-36.5 pitch=17.3 "
                "target=-20.0 valid=True age=0.001s reason=COMPLETED"
            )
        return STATUS

    with pytest.raises(vl_replay_record.ReplayError, match="final disarm failed"):
        vl_replay_record.run_replay(
            plan,
            run_command=run_command,
            send_message=lambda _step, _message: None,
        )


def test_dashboard_publish_failure_does_not_block_motion() -> None:
    events: list[tuple[str, object]] = []

    def run_command(command: tuple[str, ...], _timeout: float) -> str:
        events.append(("command", command))
        return STATUS

    def send_message(step: int, _message: str) -> None:
        events.append(("message", step))

    def fail_dashboard(_step, _sensor: str) -> None:
        events.append(("dashboard", "failed"))
        raise OSError("dashboard unavailable")

    result = vl_replay_record.run_replay(
        vl_replay_record.load_plan(RECORD),
        run_command=run_command,
        send_message=send_message,
        publish_stage=fail_dashboard,
    )

    assert result["completed_steps"] == list(range(1, 23))
    assert sum(1 for kind, _ in events if kind == "dashboard") == 22
    assert sum(1 for kind, _ in events if kind == "message") == 22
