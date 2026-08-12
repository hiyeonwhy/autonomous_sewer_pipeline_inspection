import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import motor_util

DUTY_STEPS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def ask_yes(prompt: str) -> bool:
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "yes", "ㅇ"):
            return True
        if a in ("n", "no", "ㄴ"):
            return False
        print("    y 또는 n 으로 답해주세요.")


def burst(left: float, right: float, seconds: float, label: str):
    print(f"  [{label}] {seconds}초 구동...", flush=True)
    motor_util.set_speeds(left, right)
    time.sleep(seconds)
    motor_util.stop()
    time.sleep(0.3)


def main(burst_s: float = 1.5):
    # 진단을 위해 상한 해제 (실제 주행 코드에서는 하면 안 된다)
    motor_util.MAX_DUTY = 1.0

    print("최소 구동 듀티를 찾습니다. 차 앞을 비우고 지켜보세요.")
    print("각 단계마다 짧게 구동한 뒤 움직였는지 물어봅니다.\n")
    input("준비되면 Enter: ")

    found = None
    for duty in DUTY_STEPS:
        burst(duty, duty, burst_s, f"양쪽 {duty}")
        if ask_yes(f"    움직였나요? (y/n): "):
            found = duty
            break

    if found is None:
        print("\n0.9까지도 안 움직였습니다. 듀티 문제가 아닙니다.")
        print("각 바퀴를 따로 확인해 보겠습니다.\n")
        burst(0.8, 0.0, burst_s, "왼쪽만 0.8")
        left_ok = ask_yes("    왼쪽 바퀴가 돌았나요? (y/n): ")
        burst(0.0, 0.8, burst_s, "오른쪽만 0.8")
        right_ok = ask_yes("    오른쪽 바퀴가 돌았나요? (y/n): ")

        print("\n" + "=" * 50)
        if not left_ok and not right_ok:
            print("양쪽 다 안 돎 -> 모터 전원을 확인하세요.")
            print("  - L298N 모터 전원(배터리) 연결 및 스위치")
            print("  - 배터리 잔량 (부팅 후 저전압 이력이 감지됨)")
            print("  - L298N의 ENA/ENB 점퍼, GND 공통 연결")
        elif left_ok != right_ok:
            side = "오른쪽" if left_ok else "왼쪽"
            print(f"{side} 바퀴만 안 돎 -> 그쪽 배선/모터 문제입니다.")
            print(f"  확인할 핀: 왼쪽 IN {motor_util.LEFT_IN1}/{motor_util.LEFT_IN2} "
                  f"PWM {motor_util.LEFT_PWM} / "
                  f"오른쪽 IN {motor_util.RIGHT_IN1}/{motor_util.RIGHT_IN2} "
                  f"PWM {motor_util.RIGHT_PWM}")
        else:
            print("따로는 도는데 같이는 안 돎 -> 전류 부족(배터리) 가능성이 큽니다.")
        print("=" * 50)
        return

    print("\n" + "=" * 50)
    print(f"최소 구동 듀티: {found}")
    if found > 0.5:
        print("\n주의: edge의 듀티 상한(0.5)보다 높습니다.")
        print("      현재 설정으로는 edge가 이 차체를 움직일 수 없습니다.")
        print("      motor.env의 PIPECHECKKK_MAX_DUTY 상향을 담당자와 논의하세요.")
        print(f"      캘리브레이션은 {found} 이상 구간에서 진행해야 합니다.")
    else:
        print("edge 상한(0.5) 안에서 구동 가능합니다.")
        print(f"캘리브레이션 속도 지점을 {found} 이상으로 잡으세요.")
    print("=" * 50)


if __name__ == "__main__":
    burst_s = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
    try:
        main(burst_s)
    except KeyboardInterrupt:
        print("\n중단됨")
    finally:
        motor_util.stop()
        motor_util.close()
