from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class GpioBackend(Protocol):
    def setup_output(self, pin: int, initial: int = 0) -> None: ...

    def write(self, pin: int, value: int) -> None: ...

    def set_pwm(self, pin: int, frequency_hz: int, duty_percent: float) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class MotorPinConfig:
    left_in1: int
    left_in2: int
    left_pwm: int
    right_in1: int
    right_in2: int
    right_pwm: int
    left_forward_inverted: bool = False
    right_forward_inverted: bool = False

    @classmethod
    def current_wiring(cls) -> "MotorPinConfig":
        return cls(
            left_in1=6,
            left_in2=5,
            left_pwm=0,
            right_in1=19,
            right_in2=13,
            right_pwm=26,
        )


class SafeMotorDriver:
    def __init__(
        self,
        gpio: GpioBackend,
        pins: MotorPinConfig,
        *,
        max_duty: float = 0.90,
        pwm_frequency_hz: int = 100,
    ) -> None:
        if not 0 < max_duty <= 1:
            raise ValueError("max_duty must be in (0, 1]")
        self.gpio = gpio
        self.pins = pins
        self.max_duty = max_duty
        self.pwm_frequency_hz = pwm_frequency_hz
        self._closed = False
        self._last_speeds: tuple[float, float] | None = None
        for pin in (
            pins.left_in1,
            pins.left_in2,
            pins.left_pwm,
            pins.right_in1,
            pins.right_in2,
            pins.right_pwm,
        ):
            self.gpio.setup_output(pin, initial=0)
        self.stop()

    @property
    def current_speeds(self) -> tuple[float, float]:
        return self._last_speeds or (0.0, 0.0)

    def set_speeds(self, left: float, right: float) -> None:
        if self._closed:
            raise RuntimeError("motor driver is closed")
        applied_left = min(abs(left), self.max_duty) * (-1.0 if left < 0 else 1.0)
        applied_right = min(abs(right), self.max_duty) * (-1.0 if right < 0 else 1.0)
        if self._last_speeds == (applied_left, applied_right):
            return
        self._set_direction(
            self.pins.left_in1,
            self.pins.left_in2,
            inverted=self.pins.left_forward_inverted,
            speed=applied_left,
        )
        self._set_direction(
            self.pins.right_in1,
            self.pins.right_in2,
            inverted=self.pins.right_forward_inverted,
            speed=applied_right,
        )
        self.gpio.set_pwm(
            self.pins.left_pwm,
            self.pwm_frequency_hz,
            abs(applied_left) * 100.0,
        )
        self.gpio.set_pwm(
            self.pins.right_pwm,
            self.pwm_frequency_hz,
            abs(applied_right) * 100.0,
        )
        self._last_speeds = (applied_left, applied_right)

    def stop(self) -> None:
        self.gpio.set_pwm(self.pins.left_pwm, self.pwm_frequency_hz, 0.0)
        self.gpio.set_pwm(self.pins.right_pwm, self.pwm_frequency_hz, 0.0)
        for pin in (
            self.pins.left_in1,
            self.pins.left_in2,
            self.pins.right_in1,
            self.pins.right_in2,
        ):
            self.gpio.write(pin, 0)
        self._last_speeds = (0.0, 0.0)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._closed = True
            self.gpio.close()

    def _set_direction(self, in1: int, in2: int, *, inverted: bool, speed: float) -> None:
        if speed == 0:
            self.gpio.write(in1, 0)
            self.gpio.write(in2, 0)
            return
        forward = speed > 0
        in1_high = forward != inverted
        self.gpio.write(in1, 1 if in1_high else 0)
        self.gpio.write(in2, 0 if in1_high else 1)
