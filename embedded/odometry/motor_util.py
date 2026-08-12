import RPi.GPIO as GPIO

# motor.env와 동일한 BCM 핀 번호
RIGHT_IN1 = 19
RIGHT_IN2 = 13
RIGHT_PWM = 26
LEFT_IN1 = 6
LEFT_IN2 = 5
LEFT_PWM = 0

PWM_HZ = 1000  # PIPECHECKKK_PWM_HZ
MAX_DUTY = 0.50  # PIPECHECKKK_MAX_DUTY

_pwm = {}
_initialized = False


def setup():
    global _initialized
    if _initialized:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (LEFT_IN1, LEFT_IN2, LEFT_PWM, RIGHT_IN1, RIGHT_IN2, RIGHT_PWM):
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, False)
    for pin in (LEFT_PWM, RIGHT_PWM):
        _pwm[pin] = GPIO.PWM(pin, PWM_HZ)
        _pwm[pin].start(0)
    _initialized = True


def _set_side(in1: int, in2: int, pwm_pin: int, speed: float):
    duty = min(abs(speed), MAX_DUTY)
    if duty == 0:
        GPIO.output(in1, False)
        GPIO.output(in2, False)
    elif speed > 0:
        GPIO.output(in1, True)
        GPIO.output(in2, False)
    else:
        GPIO.output(in1, False)
        GPIO.output(in2, True)
    _pwm[pwm_pin].ChangeDutyCycle(duty * 100.0)


def set_speeds(left: float, right: float):
    """바퀴별 속도를 지정한다. 0~0.5 범위(edge와 동일), 음수는 후진."""
    setup()
    _set_side(LEFT_IN1, LEFT_IN2, LEFT_PWM, left)
    _set_side(RIGHT_IN1, RIGHT_IN2, RIGHT_PWM, right)


def stop():
    if not _initialized:
        return
    set_speeds(0.0, 0.0)


def close():
    global _initialized
    if not _initialized:
        return
    stop()
    for pwm in _pwm.values():
        pwm.stop()
    _pwm.clear()
    _initialized = False
