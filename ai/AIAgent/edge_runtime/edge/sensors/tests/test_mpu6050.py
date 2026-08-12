import pytest

from edge.sensors.mpu6050 import ACCEL_XOUT_H, GYRO_XOUT_H, Mpu6050Sensor


class FakeBus:
    def __init__(self, *, failures: int = 0):
        self.failures = failures
        self.read_calls = 0

    def write_byte_data(self, _address, _register, _value):
        pass

    def read_i2c_block_data(self, _address, register, _length):
        self.read_calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise OSError("transient i2c read failure")
        if register == ACCEL_XOUT_H:
            return [0, 0, 0, 0, 0x40, 0]
        if register == GYRO_XOUT_H:
            return [0, 0, 0, 0, 0, 0]
        raise AssertionError(f"unexpected register: {register}")


def test_calibration_retries_transient_i2c_sample_failure():
    bus = FakeBus(failures=1)
    sensor = Mpu6050Sensor(bus=bus, sleep=lambda _seconds: None)

    sensor.calibrate(samples=2)

    assert sensor.calibrated is True
    assert bus.read_calls == 5


def test_calibration_fails_after_five_consecutive_i2c_sample_failures():
    bus = FakeBus(failures=5)
    sensor = Mpu6050Sensor(bus=bus, sleep=lambda _seconds: None)

    with pytest.raises(OSError, match="transient i2c read failure"):
        sensor.calibrate(samples=2)

    assert sensor.calibrated is False
    assert bus.read_calls == 5


def test_runtime_read_recovers_from_a_transient_i2c_failure():
    bus = FakeBus()
    sensor = Mpu6050Sensor(bus=bus, sleep=lambda _seconds: None)
    sensor.calibrate(samples=2)
    bus.failures = 1

    observation = sensor.read()

    assert observation.valid is True
    assert bus.read_calls == 7


def test_runtime_read_recovers_after_nineteen_consecutive_i2c_failures():
    bus = FakeBus()
    sensor = Mpu6050Sensor(bus=bus, sleep=lambda _seconds: None)
    sensor.calibrate(samples=2)
    bus.failures = 19

    observation = sensor.read()

    assert observation.valid is True
    assert bus.read_calls == 25


def test_runtime_read_fails_closed_after_twenty_consecutive_i2c_failures():
    bus = FakeBus()
    sensor = Mpu6050Sensor(bus=bus, sleep=lambda _seconds: None)
    sensor.calibrate(samples=2)
    bus.failures = 20

    observation = sensor.read()

    assert observation.valid is False
    assert bus.read_calls == 24
