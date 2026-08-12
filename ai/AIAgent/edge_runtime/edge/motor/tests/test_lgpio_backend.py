from edge.motor.lgpio_backend import LgpioBackend


class FakeLgpioModule:
    SET_PULL_NONE = 0

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def gpiochip_open(self, chip: int) -> int:
        self.calls.append(("open", chip))
        return 7

    def gpio_claim_output(self, handle: int, pin: int, initial: int) -> None:
        self.calls.append(("claim", handle, pin, initial))

    def gpio_write(self, handle: int, pin: int, value: int) -> None:
        self.calls.append(("write", handle, pin, value))

    def gpio_claim_input(self, handle: int, pin: int) -> None:
        self.calls.append(("claim-input", handle, pin))

    def gpio_read(self, handle: int, pin: int) -> int:
        self.calls.append(("read", handle, pin))
        return 1

    def tx_pwm(self, handle: int, pin: int, frequency: int, duty: float) -> None:
        self.calls.append(("pwm", handle, pin, frequency, duty))

    def gpiochip_close(self, handle: int) -> None:
        self.calls.append(("close", handle))


def test_lgpio_backend_uses_gpiochip_and_stops_pwm_on_close() -> None:
    module = FakeLgpioModule()
    gpio = LgpioBackend(module=module)

    gpio.setup_output(19, initial=0)
    gpio.setup_input(14)
    assert gpio.read(14) == 1
    gpio.write(19, 1)
    gpio.set_pwm(26, 1000, 12.0)
    gpio.close()

    assert module.calls == [
        ("open", 0),
        ("claim", 7, 19, 0),
        ("claim-input", 7, 14),
        ("read", 7, 14),
        ("write", 7, 19, 1),
        ("pwm", 7, 26, 1000, 12.0),
        ("pwm", 7, 26, 1000, 0.0),
        ("close", 7),
    ]
