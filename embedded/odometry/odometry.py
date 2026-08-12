import math
import time

import imu_util

SPEED_SLOPE = 0.5950
SPEED_INTERCEPT = -0.1473

MIN_MOVING_COMMAND = 0.40

TURN_LEFT_SIGN = 1  # 자이로 부호. 반시계(좌회전)가 양수인 것을 실측으로 확인함.


def command_to_speed(command: float) -> float:
    """바퀴 명령값(0~0.5)을 실제 속도(m/s)로 바꾼다. 데드존 아래는 0."""
    if command < MIN_MOVING_COMMAND:
        return 0.0
    return max(0.0, SPEED_SLOPE * command + SPEED_INTERCEPT)


class Odometry:
    def __init__(self, bus=None):
        """bus를 넘기지 않으면 직접 연다(그 경우 close()도 이 객체가 책임진다)."""
        self._own_bus = bus is None
        self.bus = bus if bus is not None else imu_util.open_sensor()
        self.cal = None
        self.reset()

    def reset(self):
        """위치와 방향을 원점으로 되돌린다. 보정값은 유지된다."""
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.distance = 0.0  # 이동한 총 거리(경로 길이)

    def calibrate(self):
        """차를 정지시킨 상태로 1회 호출한다. 자이로 영점과 중력 방향을 잡는다."""
        self.cal = imu_util.calibrate(self.bus)
        return self.cal

    def update(self, left_command: float, right_command: float, dt: float):
        """주행 루프에서 매 주기 호출한다."""
        if self.cal is None:
            raise RuntimeError("calibrate()를 먼저 호출하세요.")

        yaw = imu_util.yaw_rate(imu_util.read_gyro(self.bus), self.cal) * TURN_LEFT_SIGN
        self.theta += yaw * dt

        v = (command_to_speed(left_command) + command_to_speed(right_command)) / 2.0

        step = v * dt
        self.x += step * math.cos(self.theta)
        self.y += step * math.sin(self.theta)
        self.distance += step
        return self.x, self.y, self.theta

    @property
    def heading_deg(self) -> float:
        return math.degrees(self.theta)

    def close(self):
        if self._own_bus and self.bus is not None:
            self.bus.close()
            self.bus = None

    def __str__(self):
        return (f"x={self.x:+.2f}m y={self.y:+.2f}m "
                f"heading={self.heading_deg:+.1f}deg 이동거리={self.distance:.2f}m")


if __name__ == "__main__":
    # 모터를 돌리지 않고, 정지 상태에서 자이로만 살아있는지 확인하는 자체 점검.
    print("정지 상태 자체 점검 (모터는 돌리지 않음). Ctrl+C로 종료.")
    odom = Odometry()
    print(f"보정: {odom.calibrate()}\n")
    last = time.monotonic()
    try:
        while True:
            time.sleep(0.05)
            now = time.monotonic()
            odom.update(0.0, 0.0, now - last)
            last = now
            print(f"  {odom}", end="\r", flush=True)
    except KeyboardInterrupt:
        print(f"\n최종: {odom}")
    finally:
        odom.close()
