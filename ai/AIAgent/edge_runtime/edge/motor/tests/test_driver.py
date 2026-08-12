from edge.motor.driver import MotorPinConfig, SafeMotorDriver


class FakeGpio:
    def __init__(self) -> None:
        self.claimed: list[int] = []
        self.writes: list[tuple[int, int]] = []
        self.pwm: list[tuple[int, int, float]] = []
        self.closed = False

    def setup_output(self, pin: int, initial: int = 0) -> None:
        self.claimed.append(pin)
        self.writes.append((pin, initial))

    def write(self, pin: int, value: int) -> None:
        self.writes.append((pin, value))

    def set_pwm(self, pin: int, frequency_hz: int, duty_percent: float) -> None:
        self.pwm.append((pin, frequency_hz, duty_percent))

    def close(self) -> None:
        self.closed = True


def pins() -> MotorPinConfig:
    return MotorPinConfig(
        left_in1=6,
        left_in2=5,
        left_pwm=12,
        right_in1=19,
        right_in2=13,
        right_pwm=26,
        left_forward_inverted=False,
        right_forward_inverted=True,
    )


def test_driver_starts_stopped_and_maps_limited_forward_duty() -> None:
    gpio = FakeGpio()
    driver = SafeMotorDriver(gpio, pins(), pwm_frequency_hz=1000)

    assert gpio.pwm[-2:] == [(12, 1000, 0.0), (26, 1000, 0.0)]

    driver.set_speeds(0.40, 0.10)

    assert (6, 1) in gpio.writes
    assert (5, 0) in gpio.writes
    assert (19, 0) in gpio.writes
    assert (13, 1) in gpio.writes
    assert gpio.pwm[-2:] == [(12, 1000, 40.0), (26, 1000, 10.0)]


def test_stop_and_close_always_drive_pwm_and_direction_low() -> None:
    gpio = FakeGpio()
    config = pins()
    driver = SafeMotorDriver(gpio, config)
    driver.set_speeds(0.1, 0.1)

    driver.stop()

    assert gpio.pwm[-2:] == [(config.left_pwm, 100, 0.0), (config.right_pwm, 100, 0.0)]
    for pin in (config.left_in1, config.left_in2, config.right_in1, config.right_in2):
        assert (pin, 0) in gpio.writes

    driver.close()
    assert gpio.closed is True


def test_default_driver_uses_keyboard_pwm_and_ninety_percent_cap() -> None:
    gpio = FakeGpio()
    config = pins()
    driver = SafeMotorDriver(gpio, config)

    driver.set_speeds(1.0, 0.9)

    assert gpio.pwm[-2:] == [(config.left_pwm, 100, 90.0), (config.right_pwm, 100, 90.0)]


def test_identical_speed_command_keeps_existing_pwm_without_restarting_it() -> None:
    gpio = FakeGpio()
    driver = SafeMotorDriver(gpio, pins())
    driver.set_speeds(0.9, 0.9)
    pwm_count = len(gpio.pwm)
    write_count = len(gpio.writes)

    driver.set_speeds(0.9, 0.9)

    assert len(gpio.pwm) == pwm_count
    assert len(gpio.writes) == write_count


def test_negative_speeds_reverse_both_motor_directions_at_bounded_duty() -> None:
    gpio = FakeGpio()
    driver = SafeMotorDriver(gpio, pins())

    driver.set_speeds(-0.20, -0.20)

    assert (6, 0) in gpio.writes
    assert (5, 1) in gpio.writes
    assert (19, 1) in gpio.writes
    assert (13, 0) in gpio.writes
    assert gpio.pwm[-2:] == [(12, 100, 20.0), (26, 100, 20.0)]
