from __future__ import annotations

from importlib import import_module
from typing import Any


class LgpioBackend:
    def __init__(self, *, chip: int = 0, module: Any | None = None) -> None:
        self._lgpio = module or import_module("lgpio")
        self._handle = self._lgpio.gpiochip_open(chip)
        self._pwm: dict[int, int] = {}
        self._closed = False

    def setup_output(self, pin: int, initial: int = 0) -> None:
        self._ensure_open()
        self._lgpio.gpio_claim_output(self._handle, pin, initial)

    def setup_input(self, pin: int) -> None:
        self._ensure_open()
        self._lgpio.gpio_claim_input(self._handle, pin)

    def read(self, pin: int) -> int:
        self._ensure_open()
        return int(self._lgpio.gpio_read(self._handle, pin))

    def write(self, pin: int, value: int) -> None:
        self._ensure_open()
        self._lgpio.gpio_write(self._handle, pin, 1 if value else 0)

    def set_pwm(self, pin: int, frequency_hz: int, duty_percent: float) -> None:
        self._ensure_open()
        duty = min(100.0, max(0.0, float(duty_percent)))
        self._lgpio.tx_pwm(self._handle, pin, frequency_hz, duty)
        self._pwm[pin] = frequency_hz

    def close(self) -> None:
        if self._closed:
            return
        try:
            for pin, frequency in self._pwm.items():
                self._lgpio.tx_pwm(self._handle, pin, frequency, 0.0)
        finally:
            self._lgpio.gpiochip_close(self._handle)
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("GPIO chip is closed")
