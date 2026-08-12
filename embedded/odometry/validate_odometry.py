import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import motor_util
import odometry

COUNTDOWN_S = 3


def main(command: float = 0.45, duration: float = 8.0):
    if command < odometry.MIN_MOVING_COMMAND:
        print(f"명령값 {command}는 데드존({odometry.MIN_MOVING_COMMAND}) 아래라 움직이지 않습니다.")
        return
    if command > motor_util.MAX_DUTY:
        print(f"명령값 {command}는 상한({motor_util.MAX_DUTY})을 넘습니다.")
        return

    odom = odometry.Odometry()
    print("보정 중... 차를 가만히 두세요.")
    print(f"보정 완료: {odom.calibrate()}\n")

    print(f"명령값 {command}로 {duration}초 직진합니다.")
    print(f"예상 속도 {odometry.command_to_speed(command):.3f} m/s "
          f"-> 예상 거리 {odometry.command_to_speed(command) * duration:.2f}m")
    input("출발선에 차를 놓고 Enter: ")

    for i in range(COUNTDOWN_S, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)

    print("  주행!", flush=True)
    last = time.monotonic()
    start = last
    try:
        motor_util.set_speeds(command, command)
        while time.monotonic() - start < duration:
            time.sleep(0.02)
            now = time.monotonic()
            odom.update(command, command, now - last)
            last = now
    finally:
        motor_util.stop()
        motor_util.close()

    elapsed = time.monotonic() - start
    print(f"  정지. ({elapsed:.2f}초 주행)\n")
    print(f"추정 결과: {odom}")
    print(f"  주행 중 heading 변화: {odom.heading_deg:+.1f}deg", end="  ")
    print("(직진 명령이므로 0에 가까워야 하며, 벌어진 만큼 차가 휜 것)")

    raw = input("\n출발선부터 실제 이동한 직선거리(cm), 건너뛰려면 빈칸: ").strip()
    if not raw:
        odom.close()
        return
    try:
        actual_m = float(raw) / 100.0
    except ValueError:
        print("숫자가 아닙니다.")
        odom.close()
        return

    est = odom.distance
    err = est - actual_m
    err_pct = err / actual_m * 100 if actual_m else 0

    print("\n" + "=" * 52)
    print(f"  실측 거리 : {actual_m:.3f} m")
    print(f"  추정 거리 : {est:.3f} m")
    print(f"  오차      : {err:+.3f} m ({err_pct:+.1f}%)")
    print("-" * 52)
    if abs(err_pct) < 10:
        print("  오차 10% 이내 — 짧은 점검 구간 근사치로 쓸 만합니다.")
    elif abs(err_pct) < 25:
        print("  오차가 다소 큽니다. 노면/배터리 상태를 확인하고,")
        print("  조건이 바뀌었다면 calibrate_speed.py를 다시 돌리세요.")
    else:
        print("  오차가 큽니다. 캘리브레이션 조건과 지금 조건이 다를 가능성이 큽니다")
        print("  (노면 재질, 배터리 잔량, 적재 무게 등).")
    print("=" * 52)
    odom.close()


if __name__ == "__main__":
    command = float(sys.argv[1]) if len(sys.argv) > 1 else 0.45
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    try:
        main(command, duration)
    except KeyboardInterrupt:
        motor_util.stop()
        motor_util.close()
        print("\n중단됨 (모터 정지)")
