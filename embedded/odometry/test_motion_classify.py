import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imu_util

VELOCITY_THRESHOLD = 0.1  # m/s — 이보다 커야 전진/후진으로 판단
GYRO_THRESHOLD = 0.2  # rad/s — 이보다 커야 회전으로 판단

STOPPED_ACCEL_EPS = 0.3  # m/s^2 — 이 이하면 "조용함" 후보
STOPPED_GYRO_EPS = 0.15  # rad/s — 이 이하면 "조용함" 후보
STOPPED_HOLD_SAMPLES = 5  # 이만큼 연속으로 조용하면 정지로 보고 속도를 0으로 리셋

FORWARD_SIGN = 1  # 전진이 음수로 나오면 -1
TURN_LEFT_SIGN = 1  # 좌회전이 음수로 나오면 -1


def classify(velocity: float, yaw: float) -> str:
    turn = yaw * TURN_LEFT_SIGN
    if abs(turn) > GYRO_THRESHOLD:
        return "좌회전" if turn > 0 else "우회전"

    forward = velocity * FORWARD_SIGN
    if abs(forward) > VELOCITY_THRESHOLD:
        return "전진" if forward > 0 else "후진"

    return "정지"


def main(interval: float = 0.2):
    bus = imu_util.open_sensor()

    print("보정 중... 차를 가만히 두세요.")
    cal = imu_util.calibrate(bus)
    print(f"보정 완료: {cal}")

    velocity = 0.0
    quiet_count = 0
    last_t = time.monotonic()

    try:
        while True:
            accel = imu_util.read_accel(bus)
            gyro = imu_util.read_gyro(bus)

            now = time.monotonic()
            dt = now - last_t  # 고정값이 아니라 실제 경과 시간으로 적분해야 오차가 안 쌓인다
            last_t = now

            ax = imu_util.forward_accel(accel, cal)
            yaw = imu_util.yaw_rate(gyro, cal)

            velocity += ax * dt

            if abs(ax) < STOPPED_ACCEL_EPS and abs(yaw) < STOPPED_GYRO_EPS:
                quiet_count += 1
                if quiet_count >= STOPPED_HOLD_SAMPLES:
                    velocity = 0.0
            else:
                quiet_count = 0

            state = classify(velocity, yaw)
            print(
                f"{state:4s}  velocity={velocity:+.2f} m/s  "
                f"accel_fwd={ax:+.2f} m/s^2  yaw={yaw:+.3f} rad/s"
            )
            time.sleep(interval)
    finally:
        bus.close()


if __name__ == "__main__":
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0.2
    try:
        main(interval)
    except KeyboardInterrupt:
        print("\n종료")
    except OSError as e:
        print(f"MPU6050 응답 없음 ({e}). 배선과 i2c_arm 활성화+재부팅 여부를 확인하세요.")
        sys.exit(1)
