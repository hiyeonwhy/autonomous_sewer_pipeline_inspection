import math
import time

from smbus2 import SMBus

I2C_BUS = 1
MPU6050_ADDR = 0x68

PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43

G = 9.80665
DEG_TO_RAD = math.pi / 180.0
ACCEL_LSB_PER_MPS2 = 16384.0 / G  # +-2g 레인지
GYRO_LSB_PER_RADS = 131.0 / DEG_TO_RAD  # +-250dps 레인지

CALIBRATION_SAMPLES = 50
CALIBRATION_DELAY_S = 0.02

READ_RETRIES = 3
READ_RETRY_DELAY_S = 0.002

GYRO_SANITY_RADS = 4.0  # 풀스케일 4.363 rad/s (250 deg/s)
ACCEL_SANITY_MPS2 = 17.0  # 풀스케일 19.61 m/s^2 (2g)

corrupt_reads = 0  # 걸러낸 손상 표본 수 (진단용)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    n = _norm(v)
    return (v[0] / n, v[1] / n, v[2] / n)


def open_sensor() -> SMBus:
    """I2C 버스를 열고 MPU6050 슬립 모드를 해제한다."""
    bus = SMBus(I2C_BUS)
    bus.write_byte_data(MPU6050_ADDR, PWR_MGMT_1, 0)
    time.sleep(0.1)
    return bus


def _read_word(bus: SMBus, reg: int) -> int:
    last_error = None
    for attempt in range(READ_RETRIES):
        try:
            high = bus.read_byte_data(MPU6050_ADDR, reg)
            low = bus.read_byte_data(MPU6050_ADDR, reg + 1)
            value = (high << 8) | low
            return value - 65536 if value >= 0x8000 else value
        except OSError as e:
            last_error = e
            if attempt < READ_RETRIES - 1:
                time.sleep(READ_RETRY_DELAY_S)
    raise last_error


_last_good = {}


def _read_axes(bus: SMBus, base_reg: int, lsb_per_unit: float, sanity_limit: float) -> tuple:
    """3축을 읽되, 손상이 의심되는 표본은 버리고 다시 읽는다."""
    global corrupt_reads
    for attempt in range(READ_RETRIES):
        values = tuple(
            _read_word(bus, base_reg + i * 2) / lsb_per_unit for i in range(3)
        )
        if all(abs(v) < sanity_limit for v in values):
            _last_good[base_reg] = values
            return values
        corrupt_reads += 1
        if attempt < READ_RETRIES - 1:
            time.sleep(READ_RETRY_DELAY_S)
    return _last_good.get(base_reg, tuple(0.0 for _ in range(3)))


def read_accel(bus: SMBus) -> tuple:
    """보정 전 가속도 3축 (m/s^2). 손상 표본은 걸러낸다."""
    return _read_axes(bus, ACCEL_XOUT_H, ACCEL_LSB_PER_MPS2, ACCEL_SANITY_MPS2)


def read_gyro(bus: SMBus) -> tuple:
    """보정 전 각속도 3축 (rad/s). 손상 표본은 걸러낸다."""
    return _read_axes(bus, GYRO_XOUT_H, GYRO_LSB_PER_RADS, GYRO_SANITY_RADS)


class Calibration:
    """정지 상태에서 구한 기준값 묶음."""

    def __init__(self, up, forward, gyro_bias, accel_scale, tilt_deg):
        self.up = up
        self.forward = forward
        self.gyro_bias = gyro_bias
        self.accel_scale = accel_scale
        self.tilt_deg = tilt_deg

    def __str__(self):
        return (
            f"tilt={self.tilt_deg:.1f}deg  accel_scale={self.accel_scale:.3f}  "
            f"up=({self.up[0]:+.2f},{self.up[1]:+.2f},{self.up[2]:+.2f})  "
            f"gyro_bias=({self.gyro_bias[0]:+.3f},{self.gyro_bias[1]:+.3f},{self.gyro_bias[2]:+.3f})"
        )


def calibrate(bus: SMBus, samples: int = CALIBRATION_SAMPLES) -> Calibration:
    """차를 정지시킨 상태로 호출해야 한다. 중력 방향·자이로 영점·가속도 배율을 구한다."""
    acc_sum = [0.0, 0.0, 0.0]
    gyr_sum = [0.0, 0.0, 0.0]
    for _ in range(samples):
        a = read_accel(bus)
        g = read_gyro(bus)
        for i in range(3):
            acc_sum[i] += a[i]
            gyr_sum[i] += g[i]
        time.sleep(CALIBRATION_DELAY_S)

    acc = tuple(v / samples for v in acc_sum)
    gyro_bias = tuple(v / samples for v in gyr_sum)

    # 정지 상태의 가속도계는 "위" 방향으로 1g를 읽는다 -> 그대로 위 방향 벡터가 된다.
    up = _unit(acc)
    accel_scale = G / _norm(acc)

    # X축(정면)에서 위쪽 성분을 제거해 수평면 상의 정면 방향을 얻는다.
    x_axis = (1.0, 0.0, 0.0)
    proj = _dot(x_axis, up)
    horiz = (x_axis[0] - proj * up[0], x_axis[1] - proj * up[1], x_axis[2] - proj * up[2])
    forward = _unit(horiz)

    tilt_deg = math.degrees(math.acos(min(1.0, abs(up[2]))))
    return Calibration(up, forward, gyro_bias, accel_scale, tilt_deg)


def yaw_rate(gyro: tuple, cal: Calibration) -> float:
    """차가 좌우로 도는 각속도(rad/s). 반시계(= 좌회전)가 양수."""
    unbiased = tuple(gyro[i] - cal.gyro_bias[i] for i in range(3))
    return _dot(unbiased, cal.up)


def forward_accel(accel: tuple, cal: Calibration) -> float:
    """차량 정면 방향의 가속도(m/s^2). 중력 성분과 스케일 오차를 제거한다."""
    scaled = tuple(accel[i] * cal.accel_scale for i in range(3))
    dynamic = tuple(scaled[i] - cal.up[i] * G for i in range(3))
    return _dot(dynamic, cal.forward)
