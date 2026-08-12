import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import motor_util

SPEED_POINTS = [0.4, 0.45, 0.5]
COUNTDOWN_S = 3


def one_trial(speed: float, duration: float, label: str) -> float | None:
    print(f"\n  [{label}] 출발선에 차를 놓고 Enter (건너뛰기 s+Enter)")
    if input().strip().lower() == "s":
        return None

    for i in range(COUNTDOWN_S, 0, -1):
        print(f"    {i}...", flush=True)
        time.sleep(1)

    print(f"    주행! (속도 {speed}, {duration}초)", flush=True)
    motor_util.set_speeds(speed, speed)
    time.sleep(duration)
    motor_util.stop()
    print("    정지. 출발선부터 잰 거리를 입력하세요.")

    while True:
        raw = input("    이동 거리(cm), 건너뛰려면 빈칸: ").strip()
        if not raw:
            return None
        try:
            cm = float(raw)
        except ValueError:
            print("    숫자를 입력하세요.")
            continue
        if cm < 0:
            print("    0 이상을 입력하세요.")
            continue
        mps = cm / 100.0 / duration
        print(f"    -> {mps:.3f} m/s")
        return mps


def fit_linear(results: dict) -> tuple:
    """v = a*s + b 형태로 최소제곱 적합. (a, b)를 돌려준다."""
    pts = [(s, sum(v) / len(v)) for s, v in results.items()]
    n = len(pts)
    if n == 1:
        s, v = pts[0]
        return v / s, 0.0  # 한 점뿐이면 비례로 가정하는 수밖에 없다
    sx = sum(s for s, _ in pts)
    sy = sum(v for _, v in pts)
    sxx = sum(s * s for s, _ in pts)
    sxy = sum(s * v for s, v in pts)
    den = n * sxx - sx * sx
    if den == 0:
        avg_s = sx / n
        return (sy / n) / avg_s, 0.0
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b


def main(duration: float, trials: int):
    print(f"속도 지점 {SPEED_POINTS} 각각 {duration}초 주행 x {trials}회")
    print("앞이 트인 평평한 곳인지 확인하세요. 차가 실제로 움직입니다.")

    results = {}  # speed -> [m/s, ...]
    try:
        for speed in SPEED_POINTS:
            print(f"\n=== 명령 속도 {speed} ===")
            got = []
            for i in range(1, trials + 1):
                v = one_trial(speed, duration, f"{speed} / {i}of{trials}")
                if v is not None:
                    got.append(v)
            if got:
                results[speed] = got
    except KeyboardInterrupt:
        print("\n중단됨")
    finally:
        motor_util.stop()
        motor_util.close()

    if not results:
        print("\n측정된 값이 없습니다.")
        return

    a, b = fit_linear(results)

    print("\n" + "=" * 60)
    print(f"{'명령속도':>8} {'실측 m/s':>10} {'적합값':>9} {'오차':>8}   측정값")
    for speed in sorted(results):
        vals = results[speed]
        avg = sum(vals) / len(vals)
        pred = a * speed + b
        print(f"{speed:>8.2f} {avg:>10.3f} {pred:>9.3f} {avg - pred:>+8.3f}   "
              + ", ".join(f"{v:.3f}" for v in vals))

    print("-" * 60)
    print(f"속도(m/s) = {a:.4f} x 명령값 + ({b:+.4f})")
    if a > 0:
        deadzone = -b / a
        print(f"추정 데드존: 명령값 {deadzone:.3f} 이하에서는 정지")
    print(f"\n적용 범위: 명령값 {min(results):.2f} ~ {max(results):.2f} (측정한 구간)")

    print("\nodometry.py에 넣을 값:")
    print(f"    SPEED_SLOPE = {a:.4f}")
    print(f"    SPEED_INTERCEPT = {b:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    main(duration, trials)
