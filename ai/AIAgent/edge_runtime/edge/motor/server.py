from __future__ import annotations

import ipaddress
import json
import os
import signal
import subprocess
from collections.abc import Callable
from math import isfinite
from threading import Event, Thread

from edge.motor.command_receiver import create_server
from edge.motor.command_service import EdgeCommandService
from edge.motor.driver import MotorPinConfig, SafeMotorDriver
from edge.motor.lgpio_backend import LgpioBackend
from edge.motor.local_safety import LocalSafetySupervisor
from edge.motor.runtime import EdgeRuntime
from edge.imu.recovery import V2ImuRecoveryController
from edge.sensors.hcsr04 import Hcsr04Sensor
from edge.sensors.mpu6050 import Mpu6050Sensor


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def trusted_lan_from_ip_json(
    routes: list[dict[str, object]],
    interfaces: list[dict[str, object]],
) -> tuple[str, str]:
    default_devices = {
        str(route.get("dev"))
        for route in routes
        if route.get("dst") == "default" and route.get("dev")
    }
    candidates: list[tuple[str, str]] = []
    for interface in interfaces:
        if str(interface.get("ifname")) not in default_devices:
            continue
        addr_info = interface.get("addr_info", [])
        if not isinstance(addr_info, list):
            continue
        for address in addr_info:
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            ip = ipaddress.IPv4Address(str(address.get("local", "")))
            prefix = int(address.get("prefixlen", 32))
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
            candidates.append((str(ip), str(network)))
    if len(candidates) != 1:
        raise SystemExit(f"trusted LAN auto-discovery requires exactly one default-route IPv4, found {candidates}")
    return candidates[0]


def resolve_trusted_lan() -> tuple[str, str]:
    routes = json.loads(
        subprocess.check_output(["ip", "-j", "-4", "route", "show", "default"], text=True)
    )
    interfaces = json.loads(
        subprocess.check_output(["ip", "-j", "-4", "address", "show", "up"], text=True)
    )
    return trusted_lan_from_ip_json(routes, interfaces)


def motor_network_config(
    *,
    auto_resolver: Callable[[], tuple[str, str]] = resolve_trusted_lan,
) -> tuple[str, tuple[str, ...]]:
    configured_host = os.environ.get("PIPECHECKKK_MOTOR_HOST", "127.0.0.1").strip()
    allowed_clients_value = os.environ.get("PIPECHECKKK_MOTOR_ALLOWED_CLIENTS")
    if configured_host == "auto":
        host, local_subnet = auto_resolver()
        if allowed_clients_value == "auto":
            allowed_clients_value = local_subnet
    else:
        host = configured_host
    if host in {"0.0.0.0", "::"}:
        raise SystemExit("PIPECHECKKK_MOTOR_HOST must not use a wildcard bind")
    if allowed_clients_value == "auto":
        raise SystemExit("PIPECHECKKK_MOTOR_ALLOWED_CLIENTS=auto requires PIPECHECKKK_MOTOR_HOST=auto")
    if host != "127.0.0.1" and not allowed_clients_value:
        raise SystemExit(
            "PIPECHECKKK_MOTOR_ALLOWED_CLIENTS is required for a trusted-LAN bind"
        )
    allowed_clients = tuple(
        value.strip()
        for value in (allowed_clients_value or "127.0.0.1/32,::1/128").split(",")
        if value.strip()
    )
    return host, allowed_clients


def ultrasonic_stop_m_config() -> float:
    value = float(os.environ.get("PIPECHECKKK_ULTRASONIC_STOP_M", "0.05"))
    if not isfinite(value) or value < 0.0:
        raise SystemExit("PIPECHECKKK_ULTRASONIC_STOP_M must be a non-negative finite number")
    return value


def main() -> None:
    if os.environ.get("PIPECHECKKK_REAL_MOTOR") != "enabled":
        raise SystemExit("PIPECHECKKK_REAL_MOTOR=enabled is required")
    token = os.environ.get("PIPECHECKKK_EDGE_TOKEN", "")
    if not token:
        raise SystemExit("PIPECHECKKK_EDGE_TOKEN is required")
    host, allowed_clients = motor_network_config()

    gpio = LgpioBackend(chip=int(os.environ.get("PIPECHECKKK_GPIO_CHIP", "0")))
    pins = MotorPinConfig(
        left_in1=int(os.environ.get("PIPECHECKKK_LEFT_IN1", "6")),
        left_in2=int(os.environ.get("PIPECHECKKK_LEFT_IN2", "5")),
        left_pwm=int(os.environ.get("PIPECHECKKK_LEFT_PWM", "0")),
        right_in1=int(os.environ.get("PIPECHECKKK_RIGHT_IN1", "19")),
        right_in2=int(os.environ.get("PIPECHECKKK_RIGHT_IN2", "13")),
        right_pwm=int(os.environ.get("PIPECHECKKK_RIGHT_PWM", "26")),
        left_forward_inverted=env_bool("PIPECHECKKK_LEFT_INVERTED"),
        right_forward_inverted=env_bool("PIPECHECKKK_RIGHT_INVERTED"),
    )
    motor = SafeMotorDriver(
        gpio,
        pins,
        max_duty=float(os.environ.get("PIPECHECKKK_MAX_DUTY", "0.90")),
        pwm_frequency_hz=int(os.environ.get("PIPECHECKKK_PWM_HZ", "100")),
    )
    sensor = Hcsr04Sensor(
        gpio,
        trigger_pin=int(os.environ.get("PIPECHECKKK_ULTRASONIC_TRIGGER", "15")),
        echo_pin=int(os.environ.get("PIPECHECKKK_ULTRASONIC_ECHO", "14")),
    )
    safety = LocalSafetySupervisor(
        motor,
        max_speed=float(os.environ.get("PIPECHECKKK_MAX_SPEED", "0.90")),
        heartbeat_timeout_ms=int(os.environ.get("PIPECHECKKK_HEARTBEAT_TIMEOUT_MS", "1000")),
        ultrasonic_stop_m=ultrasonic_stop_m_config(),
    )
    imu = None
    if env_bool("PIPECHECKKK_IMU_ENABLED"):
        imu_sensor = Mpu6050Sensor(
            bus_number=int(os.environ.get("PIPECHECKKK_IMU_BUS", "1")),
            address=int(os.environ.get("PIPECHECKKK_IMU_ADDRESS", "0x68"), 0),
        )
        imu = V2ImuRecoveryController(
            sensor=imu_sensor,
            safety=safety,
        )
    service = EdgeCommandService(
        safety,
        token=token,
        imu=imu,
        reference_nonce_store=os.environ.get(
            "PIPECHECKKK_REFERENCE_NONCE_STORE",
            "/run/pipecheckkk-reference-nonces.json",
        ),
    )
    runtime = EdgeRuntime(safety, sensor)
    server = create_server(
        service,
        host=host,
        port=int(os.environ.get("PIPECHECKKK_MOTOR_PORT", "8765")),
        allowed_clients=allowed_clients,
    )
    stop_event = Event()
    runtime_thread = Thread(target=runtime.run, args=(stop_event,), name="edge-safety", daemon=True)
    imu_thread = (
        Thread(target=imu.run, args=(stop_event,), name="edge-imu-50hz", daemon=True)
        if imu is not None
        else None
    )

    def shutdown(_signum: int, _frame: object) -> None:
        safety.disarm("PROCESS_SIGNAL")
        stop_event.set()
        Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    runtime_thread.start()
    if imu_thread is not None:
        imu_thread.start()
    try:
        print(
            "PipeCheckkk edge motor ready: "
            f"host={host} port={server.server_port} maxDuty={motor.max_duty * 100:.0f}% "
            f"pwm={motor.pwm_frequency_hz}Hz armed=false",
            flush=True,
        )
        server.serve_forever(poll_interval=0.05)
    finally:
        stop_event.set()
        safety.disarm("PROCESS_EXIT")
        server.server_close()
        runtime_thread.join(timeout=1)
        if imu_thread is not None:
            imu_thread.join(timeout=1)
        if imu is not None:
            imu.close()
        runtime.close()


if __name__ == "__main__":
    main()
