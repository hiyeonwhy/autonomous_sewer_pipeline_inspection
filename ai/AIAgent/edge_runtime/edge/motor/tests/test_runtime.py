from datetime import datetime, timezone

from edge.motor.local_safety import LocalSafetySupervisor
from edge.motor.runtime import EdgeRuntime


class RecordingMotor:
    def __init__(self) -> None:
        self.stop_count = 0

    def set_speeds(self, left: float, right: float) -> None:
        pass

    def stop(self) -> None:
        self.stop_count += 1

    def close(self) -> None:
        self.stop()


class FakeSensor:
    def __init__(self, value: float | None) -> None:
        self.value = value

    def measure(self) -> float | None:
        return self.value


def test_runtime_updates_local_safety_from_ultrasonic_before_watchdog() -> None:
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor, ultrasonic_stop_m=0.10)
    runtime = EdgeRuntime(safety, FakeSensor(0.05))

    reason = runtime.poll_once(now=datetime(2026, 7, 23, tzinfo=timezone.utc))

    assert safety.front_distance_m == 0.05
    assert reason == "ULTRASONIC_HARD_STOP"
    assert motor.stop_count >= 2


def test_runtime_treats_sensor_none_as_open_pipe_without_extra_stop() -> None:
    motor = RecordingMotor()
    safety = LocalSafetySupervisor(motor)
    runtime = EdgeRuntime(safety, FakeSensor(None))
    baseline_stops = motor.stop_count

    reason = runtime.poll_once(now=datetime(2026, 7, 23, tzinfo=timezone.utc))

    assert reason is None
    assert motor.stop_count == baseline_stops
