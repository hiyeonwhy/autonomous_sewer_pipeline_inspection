import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imu_util

SAMPLE_INTERVAL_S = 0.01  # 적분 주기. 촘촘할수록 빠른 회전을 정확히 따라간다.


def main(print_interval: float = 0.2):
    bus = imu_util.open_sensor()

    print("보정 중... 차를 가만히 두세요.")
    cal = imu_util.calibrate(bus)
    print(f"보정 완료: {cal}")
    print("차를 돌려보세요. 반시계(좌회전)가 +. Ctrl+C로 종료.\n")

    theta = 0.0
    peak = 0.0
    start = time.monotonic()
    last_t = start
    last_print = start

    try:
        while True:
            time.sleep(SAMPLE_INTERVAL_S)
            now = time.monotonic()
            dt = now - last_t
            last_t = now

            theta += imu_util.yaw_rate(imu_util.read_gyro(bus), cal) * dt
            if abs(theta) > abs(peak):
                peak = theta

            if now - last_print >= print_interval:
                deg = math.degrees(theta)
                print(f"theta={deg:+7.1f}deg   최대={math.degrees(peak):+7.1f}deg   "
                      f"경과={now - start:5.1f}s")
                last_print = now
    finally:
        elapsed = time.monotonic() - start
        deg = math.degrees(theta)
        print(f"\n최종 theta={deg:+.1f}deg (경과 {elapsed:.1f}s)")
        if elapsed > 0:
            print(f"정지 상태로만 두었다면 드리프트는 약 {deg / elapsed * 60:+.1f} deg/분")
        bus.close()


if __name__ == "__main__":
    print_interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0.2
    try:
        main(print_interval)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"MPU6050 응답 없음 ({e}).")
        sys.exit(1)
