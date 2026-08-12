from __future__ import annotations

from collections.abc import Callable
from time import perf_counter, sleep
from typing import Protocol


class PulseGpio(Protocol):
    def setup_output(self, pin: int, initial: int = 0) -> None: ...

    def setup_input(self, pin: int) -> None: ...

    def write(self, pin: int, value: int) -> None: ...

    def read(self, pin: int) -> int: ...


class Hcsr04Sensor:
    SPEED_OF_SOUND_M_S = 343.0

    def __init__(
        self,
        gpio: PulseGpio,
        *,
        trigger_pin: int = 15,
        echo_pin: int = 14,
        timeout_s: float = 0.03,
        clock: Callable[[], float] = perf_counter,
        sleep: Callable[[float], None] = sleep,
    ) -> None:
        self.gpio = gpio
        self.trigger_pin = trigger_pin
        self.echo_pin = echo_pin
        self.timeout_s = timeout_s
        self.clock = clock
        self.sleep = sleep
        self.gpio.setup_output(trigger_pin, initial=0)
        self.gpio.setup_input(echo_pin)

    def measure(self) -> float | None:
        self.gpio.write(self.trigger_pin, 0)
        self.sleep(0.000002)
        self.gpio.write(self.trigger_pin, 1)
        self.sleep(0.000010)
        self.gpio.write(self.trigger_pin, 0)

        deadline = self.clock() + self.timeout_s
        while self.gpio.read(self.echo_pin) == 0:
            if self.clock() >= deadline:
                return None
        pulse_started = self.clock()

        deadline = pulse_started + self.timeout_s
        while self.gpio.read(self.echo_pin) == 1:
            if self.clock() >= deadline:
                return None
        pulse_finished = self.clock()

        pulse_seconds = pulse_finished - pulse_started
        distance_m = pulse_seconds * self.SPEED_OF_SOUND_M_S / 2.0
        if not 0.01 <= distance_m <= 4.0:
            return None
        return distance_m
