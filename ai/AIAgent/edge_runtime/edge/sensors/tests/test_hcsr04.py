import pytest

from edge.sensors.hcsr04 import Hcsr04Sensor


class FakePulseGpio:
    def __init__(self, reads: list[int]) -> None:
        self.reads = reads
        self.writes: list[tuple[int, int]] = []
        self.outputs: list[int] = []
        self.inputs: list[int] = []

    def setup_output(self, pin: int, initial: int = 0) -> None:
        self.outputs.append(pin)
        self.writes.append((pin, initial))

    def setup_input(self, pin: int) -> None:
        self.inputs.append(pin)

    def write(self, pin: int, value: int) -> None:
        self.writes.append((pin, value))

    def read(self, pin: int) -> int:
        return self.reads.pop(0) if self.reads else 0


class StepClock:
    def __init__(self, step: float = 0.001) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def test_hcsr04_converts_echo_pulse_to_distance() -> None:
    gpio = FakePulseGpio([0, 1, 1, 0])
    sensor = Hcsr04Sensor(
        gpio,
        trigger_pin=15,
        echo_pin=14,
        clock=StepClock(0.001),
        sleep=lambda _seconds: None,
    )

    distance = sensor.measure()

    assert distance == pytest.approx(0.343, rel=0.05)
    assert gpio.outputs == [15]
    assert gpio.inputs == [14]
    assert gpio.writes[-3:] == [(15, 0), (15, 1), (15, 0)]


def test_hcsr04_timeout_returns_none_instead_of_stale_distance() -> None:
    gpio = FakePulseGpio([0] * 100)
    sensor = Hcsr04Sensor(
        gpio,
        trigger_pin=15,
        echo_pin=14,
        timeout_s=0.003,
        clock=StepClock(0.001),
        sleep=lambda _seconds: None,
    )

    assert sensor.measure() is None
