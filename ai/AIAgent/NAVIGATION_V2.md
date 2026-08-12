# Navigation V2 — Camera Search + Pi IMU Pose Recovery

V2 is the only production navigation runtime. The laptop runs Depth Anything free-space assessment and mission sequencing; the Raspberry Pi owns the 50 Hz IMU guard, bounded yaw steps, motor stop, and pose recovery.

## Active mission sequence

```text
mission starts while motor control is disarmed
→ Pi MPU6050 stationary calibration (100 samples)
→ relative yaw/roll/pitch = 0°
→ arm and enter SEARCH
→ finite front ultrasonic distance <= 0.10 m: STOP and assess camera free space
→ request /imu/yaw-step LEFT or RIGHT
→ Pi rotates 10° per request, stopping after every step for camera reassessment
→ yaw steps are 10° → 10° → 10° → 5°, bounded to ±35°
→ enable /imu/pitch-guard and advance
→ |relative pitch| >= 35° or |relative roll| >= 45°: immediate Pi-local STOP
→ /imu/recover performs primary yaw → roll recovery → final yaw
→ STOP, recalibrate the IMU reference, and resume SEARCH with the same mission active
```

A missing ultrasonic reading by itself does not stop V2; camera perception and IMU safety gates continue to govern motion. A present ultrasonic reading must be finite and non-negative. A finite reading at or below `0.10 m` always causes STOP before direction assessment. The displayed pitch warning threshold is `34°`; the enforced pitch stop is `35°`, and the enforced roll stop is `45°`.

## Pi IMU and recovery contract

- IMU loop: `50 Hz`
- `/imu/yaw-step`: `10° → 10° → 10° → 5°`, cumulative target limited to `±35°`
- pitch warning: `|relative pitch| >= 34°`
- pitch stop: `|relative pitch| >= 35°`
- roll stop: `|relative roll| >= 45°`
- recovery order: primary yaw → roll recovery → final yaw
- yaw tolerance: `±3°`, 10 consecutive samples
- roll tolerance: `±3°`, 10 consecutive samples
- yaw output: `0.65`, reduced to `0.45` inside `15°`
- roll output: `0.70/0.05` or `0.05/0.70`
- phase timeout: `15 s`
- stale IMU limit: `0.2 s`
- local command TTL: `0.25 s`
- hard local recovery cap: `0.70`

After recovery reaches the final yaw tolerance, the Pi stops, recalibrates from the recovered pose, and returns to `READY`; the laptop resets the V2 planner to `SEARCH` without ending the active mission. NaN/Inf or negative ultrasonic data, NaN/Inf IMU data, stale IMU, command rejection, timeout, missing calibration, lost heartbeat, or an unexpected phase fails closed with STOP; terminal IMU faults also disarm motor control.
