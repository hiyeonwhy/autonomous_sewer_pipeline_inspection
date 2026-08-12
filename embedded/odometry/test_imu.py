import math
import sys
import time

from smbus2 import SMBus

I2C_BUS = 1
MPU6050_ADDR = 0x68

PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43

G_TO_MPS2 = 9.80665
DEG_TO_RAD = math.pi / 180.0

ACCEL_SCALE = 16384.0 / G_TO_MPS2  # LSB / (m/s^2) at +-2g
GYRO_SCALE = 131.0 / DEG_TO_RAD  # LSB / (rad/s) at +-250dps


def read_word(bus: SMBus, reg: int) -> int:
    high = bus.read_byte_data(MPU6050_ADDR, reg)
    low = bus.read_byte_data(MPU6050_ADDR, reg + 1)
    value = (high << 8) | low
    return value - 65536 if value >= 0x8000 else value


def main(interval: float = 0.2):
    with SMBus(I2C_BUS) as bus:
        bus.write_byte_data(MPU6050_ADDR, PWR_MGMT_1, 0)  # 슬립 모드 해제
        time.sleep(0.1)

        while True:
            ax = read_word(bus, ACCEL_XOUT_H) / ACCEL_SCALE
            ay = read_word(bus, ACCEL_XOUT_H + 2) / ACCEL_SCALE
            az = read_word(bus, ACCEL_XOUT_H + 4) / ACCEL_SCALE
            gx = read_word(bus, GYRO_XOUT_H) / GYRO_SCALE
            gy = read_word(bus, GYRO_XOUT_H + 2) / GYRO_SCALE
            gz = read_word(bus, GYRO_XOUT_H + 4) / GYRO_SCALE

            print(
                f"accel(m/s^2): x={ax:+.3f} y={ay:+.3f} z={az:+.3f}  "
                f"gyro(rad/s): x={gx:+.3f} y={gy:+.3f} z={gz:+.3f}"
            )
            time.sleep(interval)


if __name__ == "__main__":
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0.2
    try:
        main(interval)
    except KeyboardInterrupt:
        print("\n종료")
    except OSError as e:
        print(f"MPU6050 응답 없음 ({e}). 배선(SDA=Pin3, SCL=Pin5, VCC=Pin1, GND=Pin9)과 "
              f"i2c_arm 활성화+재부팅 여부를 확인하세요.")
        sys.exit(1)
