import select
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import motor_util
import odometry

KEY_POLL_S = 0.02
PRINT_INTERVAL_S = 0.2

ARROW = {"A": "forward", "B": "backward", "C": "right", "D": "left"}


def read_key(timeout: float) -> str:
    """timeout초 안에 눌린 키를 돌려준다. 화살표는 ESC 3바이트 시퀀스로 들어온다."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return ""
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            return ARROW.get(sys.stdin.read(2)[1], "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def wheel_commands(action: str, command: float) -> tuple:
    """조작을 바퀴별 명령값으로 바꾼다. edge의 set_speeds(left, right)와 같은 의미."""
    if action == "forward":
        return command, command
    if action == "backward":
        return -command, -command
    if action == "left":
        return 0.0, command  # 오른쪽만 굴려 좌선회
    if action == "right":
        return command, 0.0
    return 0.0, 0.0


def main(command: float = 0.45):
    odom = odometry.Odometry()
    print("보정 중... 차를 가만히 두세요.")
    print(f"보정 완료: {odom.calibrate()}")
    print("화살표: 이동 / s: 정지 / r: 원점 초기화 / q: 종료\n")

    action = None
    last = time.monotonic()
    last_print = last

    try:
        while True:
            key = read_key(KEY_POLL_S)
            now = time.monotonic()
            dt = now - last  # 반드시 실제 경과 시간으로 적분해야 한다
            last = now

            if key == "q":
                break
            elif key == "s":
                action = None
            elif key == "r":
                odom.reset()
            elif key in ("forward", "backward", "left", "right"):
                action = key

            left, right = wheel_commands(action, command)
            motor_util.set_speeds(left, right)

            odom.update(abs(left), abs(right), dt)

            if now - last_print >= PRINT_INTERVAL_S:
                print(f"  [{action or '정지':8s}] {odom}   ", end="\r", flush=True)
                last_print = now
    finally:
        motor_util.stop()
        motor_util.close()
        print(f"\n\n최종 추정: {odom}")
        print("실제 위치를 줄자로 재서 비교해 보세요.")
        odom.close()


if __name__ == "__main__":
    command = float(sys.argv[1]) if len(sys.argv) > 1 else 0.45
    try:
        main(command)
    except KeyboardInterrupt:
        motor_util.stop()
        motor_util.close()
        print("\n중단됨 (모터 정지)")
