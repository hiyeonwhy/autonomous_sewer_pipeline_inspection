# PipeCheckkk Edge Runtime Overlay

This directory mirrors the Raspberry Pi runtime package from `/home/ssafy/sewer-robot-edge/edge` so AI-controlled motor protocol changes are versioned and tested under `ai/AIAgent/`.

## Trusted LAN motor endpoint

The motor HTTP server never accepts a wildcard bind. It defaults to loopback; Laptop-to-Pi control requires an explicit Pi LAN-interface address and a narrow controller IP/CIDR allowlist:

```dotenv
# Raspberry Pi motor.env example
PIPECHECKKK_MOTOR_HOST=192.168.100.246
PIPECHECKKK_MOTOR_PORT=8765
PIPECHECKKK_MOTOR_ALLOWED_CLIENTS=192.168.100.50/32
PIPECHECKKK_EDGE_TOKEN=REPLACE_WITH_A_RANDOM_SHARED_TOKEN
```

Set `PIPECHECKKK_MOTOR_HOST` to an address assigned to the Pi's trusted LAN interface, not `0.0.0.0` or `::`. Set `PIPECHECKKK_MOTOR_ALLOWED_CLIENTS` to the laptop's fixed address as `/32` (or `/128` for IPv6) whenever possible; use a trusted LAN CIDR only when controller addressing requires it. The source allowlist applies to health and command requests, and token authentication still applies to control requests.

## V2 MPU6050 pose recovery

V2 keeps camera perception on the laptop and runs pitch/roll guarding plus pose recovery on the Pi at 50 Hz:

1. Mission start calls `/imu/calibrate` while motor control is disarmed. One hundred stationary samples establish relative yaw/roll/pitch as `0°`.
2. A finite front ultrasonic reading `<= 0.10 m` causes STOP and camera direction assessment. A missing ultrasonic reading alone continues through camera and IMU safety gates.
3. The laptop calls `/imu/yaw-step` with `LEFT` or `RIGHT`. It stops after each step and rotates `10° → 10° → 10° → 5°`, up to a cumulative `±35°` target.
4. `/imu/pitch-guard` enables local monitoring during advance. The displayed pitch warning is `34°`; the Pi stops at `|relative pitch| >= 35°` or `|relative roll| >= 45°`.
5. `/imu/recover` executes primary yaw → roll recovery → final yaw. The Pi then stops and recalibrates the recovered pose before the laptop resumes `SEARCH`; the current mission stays active.

NaN/Inf or negative ultrasonic measurements fail closed. Invalid/stale IMU data, heartbeat loss, command TTL expiry, recovery timeout, or local command rejection stops motion; terminal IMU safety faults disarm control. IMU limit transition, local STOP, command interlock validation, and motor apply share one safety lock, so an HTTP motion command cannot be applied from a stale pre-limit phase.

Enable the sensor only on V2-capable Pi deployments:

```dotenv
PIPECHECKKK_IMU_ENABLED=1
PIPECHECKKK_IMU_BUS=1
PIPECHECKKK_IMU_ADDRESS=0x68
```

V2 motion safety limits are code-fixed, not environment-configurable: yaw steps
`10° → 10° → 10° → 5°` with a `35°` limit and pitch warning/stop at `34°/35°`.
For the operator-taught replay, the Pi-local ultrasonic distance hard stop is disabled with
`PIPECHECKKK_ULTRASONIC_STOP_M=0.0`; invalid sensor data, E-stop, command expiry, and explicit STOP/disarm remain active.

Mission-start calibration occurs while disarmed. The recovery-cycle recalibration is the controlled exception: it happens after local STOP while the same armed mission session is retained, then V2 resumes `SEARCH`.

## Test

```bash
cd ai/AIAgent
PYTHONPATH=edge_runtime python -m pytest -q edge_runtime
```

## Deploy Edge runtime

Copy `edge/` to `/home/ssafy/sewer-robot-edge/edge/`, preserve the Pi's `motor.env`, and restart `pipecheckkk-motor.service`. Deployment verification must keep the service disarmed and must not issue a motor command. From an allowed controller, check `GET /health` for `armed=false`, `imu.available=true`, and the expected IMU phase.
