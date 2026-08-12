from __future__ import annotations

import math
import time
from typing import Callable

from edge.imu.recovery import ImuObservation, wrap_degrees


MPU6050_ADDRESS = 0x68
PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43
ACCEL_LSB_PER_G = 16384.0
GYRO_LSB_PER_DEG_S = 131.0


def _signed_word(high: int, low: int) -> int:
    value = (high << 8) | low
    return value - 65536 if value >= 32768 else value


def _decode_axes(data: list[int], scale: float) -> tuple[float, float, float]:
    if len(data) != 6:
        raise OSError("short MPU6050 block read")
    return tuple(_signed_word(data[i], data[i + 1]) / scale for i in (0, 2, 4))


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm < 1e-6:
        raise RuntimeError("invalid gravity vector")
    return tuple(value / norm for value in vector)


def _roll_deg(vector: tuple[float, float, float]) -> float:
    return math.degrees(math.atan2(vector[1], vector[2]))


def _pitch_deg(vector: tuple[float, float, float]) -> float:
    return math.degrees(math.atan2(vector[0], math.hypot(vector[1], vector[2])))


class Mpu6050Sensor:
    """Relative yaw plus gyro/accelerometer roll and pitch sensor."""

    def __init__(
        self,
        *,
        bus=None,
        bus_number: int = 1,
        address: int = MPU6050_ADDRESS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if bus is None:
            from smbus2 import SMBus

            bus = SMBus(bus_number)
            self._owns_bus = True
        else:
            self._owns_bus = False
        self.bus = bus
        self.address = address
        self.clock = clock
        self.sleep = sleep
        self.gyro_bias = (0.0, 0.0, 0.0)
        self.up = (0.0, 0.0, 1.0)
        self.calibrated = False
        self.yaw_deg = 0.0
        self.roll_deg = 0.0
        self.pitch_deg = 0.0
        self.dynamic_mode = False
        self._last_time: float | None = None
        self.bus.write_byte_data(self.address, PWR_MGMT_1, 0)
        self.sleep(0.1)

    def _read_accel(self) -> tuple[float, float, float]:
        data = self.bus.read_i2c_block_data(self.address, ACCEL_XOUT_H, 6)
        return _decode_axes(data, ACCEL_LSB_PER_G)

    def _read_gyro(self) -> tuple[float, float, float]:
        data = self.bus.read_i2c_block_data(self.address, GYRO_XOUT_H, 6)
        return _decode_axes(data, GYRO_LSB_PER_DEG_S)

    def calibrate(self, *, samples: int = 100, sample_delay_s: float = 0.01) -> None:
        if samples < 2:
            raise ValueError("at least two calibration samples are required")
        accel_sum = [0.0, 0.0, 0.0]
        gyro_sum = [0.0, 0.0, 0.0]
        for _ in range(samples):
            accel = (0.0, 0.0, 0.0)
            gyro = (0.0, 0.0, 0.0)
            for attempt in range(5):
                try:
                    accel = self._read_accel()
                    gyro = self._read_gyro()
                    break
                except OSError:
                    if attempt == 4:
                        raise
                    self.sleep(sample_delay_s)
            for axis in range(3):
                accel_sum[axis] += accel[axis]
                gyro_sum[axis] += gyro[axis]
            self.sleep(sample_delay_s)
        self.up = _unit(tuple(value / samples for value in accel_sum))
        self.gyro_bias = tuple(value / samples for value in gyro_sum)
        self.yaw_deg = 0.0
        self.roll_deg = _roll_deg(self.up)
        self.pitch_deg = _pitch_deg(self.up)
        self.dynamic_mode = False
        self._last_time = self.clock()
        self.calibrated = True

    def set_dynamic_mode(self, enabled: bool) -> None:
        self.dynamic_mode = bool(enabled)

    def read(self) -> ImuObservation:
        if not self.calibrated:
            return ImuObservation(0.0, 0.0, 0.0, age_s=float("inf"), valid=False)
        started_at = self.clock()
        accel = current_up = gyro = (0.0, 0.0, 0.0)
        # Twenty attempts with 2 ms gaps absorb brief motor-noise/bus glitches
        # while remaining well inside the 250 ms motor-command TTL.
        runtime_read_attempts = 20
        for attempt in range(runtime_read_attempts):
            try:
                accel = self._read_accel()
                current_up = _unit(accel)
                gyro = self._read_gyro()
                break
            except (OSError, ValueError, RuntimeError):
                if attempt == runtime_read_attempts - 1:
                    age_s = max(0.0, self.clock() - started_at)
                    return ImuObservation(
                        self.yaw_deg,
                        self.roll_deg,
                        self.pitch_deg,
                        age_s=age_s,
                        valid=False,
                    )
                self.sleep(0.002)
        completed_at = self.clock()
        age_s = max(0.0, completed_at - started_at)
        dt = 0.0 if self._last_time is None else max(0.0, min(completed_at - self._last_time, 0.25))
        self._last_time = completed_at
        unbiased = tuple(gyro[i] - self.gyro_bias[i] for i in range(3))
        yaw_rate_deg_s = sum(unbiased[i] * self.up[i] for i in range(3))
        self.yaw_deg = wrap_degrees(self.yaw_deg + yaw_rate_deg_s * dt)
        accel_roll_deg = _roll_deg(current_up)
        predicted_roll_deg = self.roll_deg + unbiased[0] * dt
        accel_pitch_deg = _pitch_deg(current_up)
        predicted_pitch_deg = self.pitch_deg - unbiased[1] * dt
        accel_norm_g = math.sqrt(sum(value * value for value in accel))
        roll_innovation_deg = accel_roll_deg - predicted_roll_deg
        pitch_innovation_deg = accel_pitch_deg - predicted_pitch_deg
        if (
            not self.dynamic_mode
            and 0.75 <= accel_norm_g <= 1.25
            and abs(roll_innovation_deg) <= 8.0
        ):
            self.roll_deg = 0.98 * predicted_roll_deg + 0.02 * accel_roll_deg
        else:
            self.roll_deg = predicted_roll_deg
        if (
            not self.dynamic_mode
            and 0.75 <= accel_norm_g <= 1.25
            and abs(pitch_innovation_deg) <= 8.0
        ):
            self.pitch_deg = 0.98 * predicted_pitch_deg + 0.02 * accel_pitch_deg
        else:
            self.pitch_deg = predicted_pitch_deg
        return ImuObservation(
            self.yaw_deg,
            self.roll_deg,
            self.pitch_deg,
            age_s=age_s,
            valid=True,
        )

    def close(self) -> None:
        if self._owns_bus and self.bus is not None:
            self.bus.close()
            self.bus = None
