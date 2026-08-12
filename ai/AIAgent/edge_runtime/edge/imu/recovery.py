from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class ImuObservation:
    yaw_deg: float
    roll_deg: float
    pitch_deg: float
    age_s: float
    valid: bool = True


class ImuSensor(Protocol):
    def calibrate(self, *, samples: int = 100, sample_delay_s: float = 0.01) -> None: ...

    def read(self) -> ImuObservation: ...

    def set_dynamic_mode(self, enabled: bool) -> None: ...

    def close(self) -> None: ...


class ImuSafety(Protocol):
    armed: bool
    safety_lock: Any

    def stop_motion(self, reason: str) -> None: ...

    def disarm(self, reason: str = "DISARMED") -> str: ...

    def begin_local_operation(
        self, operation: str, *, now: datetime, deadline_s: float
    ) -> None: ...

    def finish_local_operation(self, operation: str, reason: str) -> str: ...

    def apply_local_signed(
        self,
        left: float,
        right: float,
        *,
        reason: str,
        now: datetime,
        ttl_s: float = 0.25,
        local_operation: str | None = None,
    ) -> tuple[bool, str]: ...


class ImuPhase(StrEnum):
    UNCALIBRATED = "UNCALIBRATED"
    READY = "READY"
    PIVOT_YAW = "PIVOT_YAW"
    YAW_STEP_REACHED = "YAW_STEP_REACHED"
    PITCH_GUARD = "PITCH_GUARD"
    PITCH_LIMIT_REACHED = "PITCH_LIMIT_REACHED"
    ROLL_LIMIT_REACHED = "ROLL_LIMIT_REACHED"
    PRIMARY_YAW = "PRIMARY_YAW"
    ROLL_RECOVERY = "ROLL_RECOVERY"
    FINAL_YAW = "FINAL_YAW"
    YAW_SCAN_360 = "YAW_SCAN_360"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


def wrap_degrees(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


class V2ImuRecoveryController:
    """Pi-local V2 attitude monitor and yaw→roll→yaw pose recovery controller."""

    LOCAL_INVALID_GRACE_S = 10.0
    LOCAL_VALID_RESUME_SAMPLES = 3

    def __init__(
        self,
        *,
        sensor: ImuSensor,
        safety: ImuSafety,
        pitch_stop_deg: None = None,
        roll_stop_deg: None = None,
        yaw_step_deg: float = 10.0,
        yaw_tolerance_deg: float = 3.0,
        roll_tolerance_deg: float = 3.0,
        settle_samples: int = 10,
        stale_after_s: float = 0.2,
        invalid_retry_limit: int = 5,
        phase_timeout_s: float = 30.0,
        yaw_step_timeout_s: float = 30.0,
        turn_power: float = 0.65,
        slow_turn_power: float = 0.45,
        slow_threshold_deg: float = 15.0,
        roll_outer_power: float = 0.70,
        roll_inner_power: float = 0.05,
    ) -> None:
        finite_positive = (
            yaw_step_deg,
            yaw_tolerance_deg,
            roll_tolerance_deg,
            stale_after_s,
            phase_timeout_s,
            yaw_step_timeout_s,
            turn_power,
            slow_turn_power,
            slow_threshold_deg,
            roll_outer_power,
        )
        if not all(math.isfinite(value) and value > 0 for value in finite_positive):
            raise ValueError("V2 IMU limits must be finite and positive")
        if (
            settle_samples < 1
            or invalid_retry_limit < 1
            or not 0 <= roll_inner_power < roll_outer_power <= 0.70
        ):
            raise ValueError("invalid V2 IMU settling or roll powers")
        if (
            max(turn_power, slow_turn_power) > 0.70
            or phase_timeout_s > 30.0
            or yaw_step_timeout_s > 30.0
        ):
            raise ValueError("V2 IMU power or timeout exceeds hard limit")
        if (pitch_stop_deg, roll_stop_deg, yaw_step_deg) != (None, None, 10.0):
            raise ValueError(
                "V2 fixed motion contract requires monitor-only pitch/roll, "
                "yaw-step=10"
            )
        self.sensor = sensor
        self.safety = safety
        self.pitch_stop_deg = pitch_stop_deg
        self.roll_stop_deg = roll_stop_deg
        self.yaw_step_deg = yaw_step_deg
        self.yaw_limit_deg: None = None
        self.yaw_tolerance_deg = yaw_tolerance_deg
        self.roll_tolerance_deg = roll_tolerance_deg
        self.settle_samples = settle_samples
        self.stale_after_s = stale_after_s
        self.invalid_retry_limit = invalid_retry_limit
        self.phase_timeout_s = phase_timeout_s
        self.yaw_step_timeout_s = yaw_step_timeout_s
        self.turn_power = turn_power
        self.slow_turn_power = slow_turn_power
        self.slow_threshold_deg = slow_threshold_deg
        self.roll_outer_power = roll_outer_power
        self.roll_inner_power = roll_inner_power
        self.phase = ImuPhase.UNCALIBRATED
        self.reference_yaw_deg: float | None = None
        self.reference_roll_deg: float | None = None
        self.reference_pitch_deg: float | None = None
        self._pitch_guard_reference_deg: float | None = None
        self._advance_limit_trigger: str | None = None
        self.last_observation: ImuObservation | None = None
        self.last_reason = "IMU_NOT_CALIBRATED"
        self._settled = 0
        self._invalid_reads = 0
        self._local_invalid_started_at: float | None = None
        self._local_valid_recovery_reads = 0
        self._phase_started_at: float | None = None
        self._yaw_target_deg: float | None = None
        self._yaw_direction = 0
        self._local_operation: str | None = None
        self._operation_started_at: float | None = None
        self._operation_deadline_s: float | None = None
        self._reference_restored = False
        self._restored_nonce: str | None = None
        self._scan_previous_yaw_deg: float | None = None
        self._scan_accumulated_clockwise_deg: float | None = None
        self._scan_required_deg = 357.0
        self._scan_direction = 1
        self._scan_target_yaw_deg = 0.0
        self._temporary_reference_name: str | None = None
        self._temporary_reference_yaw_deg: float | None = None
        self._temporary_reference_roll_deg: float | None = None
        self._temporary_reference_pitch_deg: float | None = None

    def calibrate(self, *, samples: int = 100) -> dict[str, object]:
        recovery_cycle_resume = self.safety.armed and self.phase is ImuPhase.COMPLETED
        if self.safety.armed and not recovery_cycle_resume:
            raise RuntimeError("disarm before IMU calibration")
        self.safety.stop_motion(
            "V2_RECOVERY_CYCLE_RECALIBRATION"
            if recovery_cycle_resume
            else "V2_IMU_CALIBRATION"
        )
        self.sensor.set_dynamic_mode(False)
        self.sensor.calibrate(samples=samples)
        observation = self.sensor.read()
        self._validate_observation(observation)
        self.last_observation = observation
        self.reference_yaw_deg = wrap_degrees(observation.yaw_deg)
        self.reference_roll_deg = observation.roll_deg
        self.reference_pitch_deg = observation.pitch_deg
        self._pitch_guard_reference_deg = observation.pitch_deg
        self._advance_limit_trigger = None
        self.phase = ImuPhase.READY
        self.last_reason = "V2_IMU_CALIBRATED"
        self._settled = 0
        self._invalid_reads = 0
        self._phase_started_at = None
        self._yaw_target_deg = None
        self._yaw_direction = 0
        self._reference_restored = False
        self._restored_nonce = None
        self._clear_temporary_reference()
        return self.status()

    def restore_reference_state(
        self,
        state: dict[str, object],
        *,
        now: datetime,
        max_age_s: float = 300.0,
    ) -> dict[str, object]:
        """Restore a short-lived relative frame after refreshing sensor bias."""
        with self.safety.safety_lock:
            if self.safety.armed or self.phase is not ImuPhase.UNCALIBRATED:
                raise RuntimeError("reference restore requires UNCALIBRATED and disarmed")
            if not math.isfinite(max_age_s) or not 0 < max_age_s <= 300.0:
                raise ValueError("reference restore max age must be at most 300 seconds")
            if state.get("version") != 1 or state.get("phase") != "COMPLETED":
                raise RuntimeError("reference restore requires a COMPLETED version 1 state")
            nonce = state.get("nonce")
            if not isinstance(nonce, str) or not nonce.strip() or len(nonce) > 128:
                raise RuntimeError("reference restore nonce is invalid")
            if nonce == self._restored_nonce:
                raise RuntimeError("reference state was already restored")
            try:
                captured_at = datetime.fromisoformat(
                    str(state["capturedAt"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("reference capture timestamp is invalid") from error
            if captured_at.tzinfo is None:
                raise RuntimeError("reference capture timestamp must include timezone")
            age_s = (now - captured_at).total_seconds()
            if age_s < -5.0 or age_s > max_age_s:
                raise RuntimeError("reference transfer expired or is from the future")
            values = tuple(
                state.get(name)
                for name in ("relativeYawDeg", "relativeRollDeg", "relativePitchDeg")
            )
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in values
            ):
                raise RuntimeError("reference transfer pose is invalid")
            relative_yaw = float(values[0])  # type: ignore[arg-type]
            relative_roll = float(values[1])  # type: ignore[arg-type]
            relative_pitch = float(values[2])  # type: ignore[arg-type]
            if not -180.0 <= relative_yaw <= 180.0:
                raise RuntimeError("reference transfer yaw is outside wrapped range")
            self.safety.stop_motion("V2_IMU_REFERENCE_RESTORE")
            self.sensor.set_dynamic_mode(False)
            # A process restart loses the MPU6050 gyro bias. Re-estimate only
            # that physical sensor bias while stationary, then reconstruct the
            # original inspection-relative frame from the transferred pose.
            self.sensor.calibrate(samples=100)
            observation = self.sensor.read()
            self._validate_observation(observation)
            self.last_observation = observation
            self.reference_yaw_deg = wrap_degrees(observation.yaw_deg - relative_yaw)
            self.reference_roll_deg = wrap_degrees(observation.roll_deg - relative_roll)
            self.reference_pitch_deg = wrap_degrees(observation.pitch_deg - relative_pitch)
            self._pitch_guard_reference_deg = self.reference_pitch_deg
            self.phase = ImuPhase.COMPLETED
            self.last_reason = "V2_IMU_REFERENCE_RESTORED"
            self._reference_restored = True
            self._restored_nonce = nonce
            self._settled = 0
            self._invalid_reads = 0
            return self.status()

    def start_yaw_scan_360(
        self,
        *,
        now: datetime,
        deadline_s: float = 60.0,
        now_monotonic: float | None = None,
        temporary_reference_name: str | None = None,
    ) -> dict[str, object]:
        """Start a Pi-owned continuous yaw operation."""
        with self.safety.safety_lock:
            monotonic_now = time.monotonic() if now_monotonic is None else now_monotonic
            try:
                self._require_calibrated()
                if self.phase not in {ImuPhase.COMPLETED, ImuPhase.YAW_STEP_REACHED}:
                    raise RuntimeError("V2 yaw operation requires terminal yaw phase")
                observation = self.sensor.read()
                self._validate_observation(observation)
                self.last_observation = observation
                operation = "YAW_SCAN_360"
                if temporary_reference_name is not None:
                    normalized = temporary_reference_name.strip().upper()
                    if normalized != self._temporary_reference_name:
                        raise RuntimeError("V2 temporary IMU reference is unavailable")
                    current_yaw = self.relative_yaw_deg
                    temporary_yaw = self._temporary_reference_yaw_deg
                    if current_yaw is None or temporary_yaw is None:
                        raise RuntimeError("V2 temporary IMU reference is unavailable")
                    self._scan_target_yaw_deg = wrap_degrees(temporary_yaw + 180.0)
                    self._scan_required_deg = (
                        self._scan_target_yaw_deg - current_yaw
                    ) % 360.0
                    self._scan_direction = -1
                    operation = "YAW_TEMP_180"
                else:
                    self._scan_target_yaw_deg = 0.0
                    self._scan_required_deg = 357.0
                    self._scan_direction = 1
                self.safety.begin_local_operation(
                    operation, now=now, deadline_s=deadline_s
                )
                self._local_invalid_started_at = None
                self._local_valid_recovery_reads = 0
                self._local_operation = operation
                self._operation_started_at = monotonic_now
                self._operation_deadline_s = float(deadline_s)
                self.sensor.set_dynamic_mode(True)
            except Exception as error:
                reason = str(error)
                if not reason.startswith("V2_"):
                    reason = f"V2_YAW_SCAN_START_FAILED:{reason}"
                return self._fail(reason)
            self.phase = ImuPhase.YAW_SCAN_360
            self.last_reason = f"V2_{self._local_operation}"
            self._scan_previous_yaw_deg = self.relative_yaw_deg
            self._scan_accumulated_clockwise_deg = 0.0
            self._phase_started_at = monotonic_now
            return self.status()

    def capture_temporary_reference(self, name: str) -> dict[str, object]:
        with self.safety.safety_lock:
            self._require_calibrated()
            if self.safety.armed:
                raise RuntimeError("disarm before capturing temporary IMU reference")
            normalized_name = name.strip().upper()
            if not normalized_name or len(normalized_name) > 64:
                raise ValueError("temporary IMU reference name is invalid")
            yaw = self.relative_yaw_deg
            roll = self.relative_roll_deg
            pitch = self.relative_pitch_deg
            if yaw is None or roll is None or pitch is None:
                raise RuntimeError("temporary IMU reference values are unavailable")
            self._temporary_reference_name = normalized_name
            self._temporary_reference_yaw_deg = yaw
            self._temporary_reference_roll_deg = roll
            self._temporary_reference_pitch_deg = pitch
            return self.status()

    def clear_temporary_reference(self) -> dict[str, object]:
        with self.safety.safety_lock:
            if self.safety.armed:
                raise RuntimeError("disarm before clearing temporary IMU reference")
            self._clear_temporary_reference()
            return self.status()

    def restore_temporary_reference(
        self, name: str, *, yaw_deg: float, roll_deg: float, pitch_deg: float
    ) -> dict[str, object]:
        with self.safety.safety_lock:
            self._require_calibrated()
            if self.safety.armed:
                raise RuntimeError("disarm before restoring temporary IMU reference")
            normalized_name = name.strip().upper()
            values = (yaw_deg, roll_deg, pitch_deg)
            if not normalized_name or len(normalized_name) > 64 or not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in values
            ):
                raise ValueError("temporary IMU reference restore payload is invalid")
            self._temporary_reference_name = normalized_name
            self._temporary_reference_yaw_deg = wrap_degrees(float(yaw_deg))
            self._temporary_reference_roll_deg = float(roll_deg)
            self._temporary_reference_pitch_deg = float(pitch_deg)
            return self.status()

    def start_current_yaw_step(
        self,
        direction: str,
        *,
        step_degrees: float | None = None,
        now_monotonic: float | None = None,
    ) -> dict[str, object]:
        current_yaw = self.relative_yaw_deg
        if current_yaw is None:
            raise RuntimeError("current IMU yaw is unavailable")
        return self._start_yaw_step_toward(
            direction,
            base_target_deg=current_yaw,
            step_degrees=step_degrees,
            now_monotonic=now_monotonic,
        )

    def start_relative_yaw_step(
        self,
        direction: str,
        *,
        now_monotonic: float | None = None,
    ) -> dict[str, object]:
        if self._temporary_reference_yaw_deg is None:
            raise RuntimeError("capture temporary IMU reference before relative yaw step")
        return self._start_yaw_step_toward(
            direction,
            base_target_deg=self._temporary_reference_yaw_deg,
            now_monotonic=now_monotonic,
        )

    def start_yaw_step(
        self,
        direction: str,
        *,
        now_monotonic: float | None = None,
    ) -> dict[str, object]:
        base_target = self._yaw_target_deg if self._yaw_target_deg is not None else 0.0
        return self._start_yaw_step_toward(
            direction,
            base_target_deg=base_target,
            now_monotonic=now_monotonic,
        )

    def _start_yaw_step_toward(
        self,
        direction: str,
        *,
        base_target_deg: float,
        step_degrees: float | None = None,
        now_monotonic: float | None,
    ) -> dict[str, object]:
        with self.safety.safety_lock:
            self._require_calibrated()
            if not self.safety.armed:
                raise RuntimeError("arm before starting V2 yaw step")
            if self.phase not in {ImuPhase.READY, ImuPhase.YAW_STEP_REACHED}:
                raise RuntimeError(f"cannot start yaw step from {self.phase}")
            normalized = direction.upper()
            if normalized not in {"LEFT", "RIGHT"}:
                raise ValueError("V2 yaw direction must be LEFT or RIGHT")
            degrees = self.yaw_step_deg if step_degrees is None else float(step_degrees)
            if not math.isfinite(degrees) or not 1.0 <= degrees <= 30.0:
                raise ValueError("V2 yaw step degrees must be finite and between 1 and 30")
            current = self.relative_yaw_deg
            if current is None:
                raise RuntimeError("V2 relative yaw is unavailable")
            monotonic_now = time.monotonic() if now_monotonic is None else now_monotonic
            self.safety.begin_local_operation(
                "YAW_STEP",
                now=datetime.now(timezone.utc),
                deadline_s=self.yaw_step_timeout_s,
            )
            self._local_operation = "YAW_STEP"
            self._operation_started_at = monotonic_now
            self._operation_deadline_s = self.yaw_step_timeout_s
            sign = 1 if normalized == "LEFT" else -1
            previous_target = self._yaw_target_deg
            previous_direction = self._yaw_direction
            self._yaw_target_deg = wrap_degrees(base_target_deg + sign * degrees)
            self._yaw_direction = sign
            try:
                self.sensor.set_dynamic_mode(True)
            except Exception:
                try:
                    self.safety.finish_local_operation(
                        "YAW_STEP", "V2_YAW_STEP_START_FAILED"
                    )
                finally:
                    self._local_operation = None
                    self._operation_started_at = None
                    self._operation_deadline_s = None
                    self._yaw_target_deg = previous_target
                    self._yaw_direction = previous_direction
                raise
            self.phase = ImuPhase.PIVOT_YAW
            self.last_reason = "V2_YAW_STEP_ACTIVE"
            self._settled = 0
            self._phase_started_at = monotonic_now
            return self.status()

    def _clear_temporary_reference(self) -> None:
        self._temporary_reference_name = None
        self._temporary_reference_yaw_deg = None
        self._temporary_reference_roll_deg = None
        self._temporary_reference_pitch_deg = None

    def _temporary_reference_status(self) -> dict[str, object] | None:
        if self._temporary_reference_name is None:
            return None
        return {
            "name": self._temporary_reference_name,
            "yawDeg": self._temporary_reference_yaw_deg,
            "rollDeg": self._temporary_reference_roll_deg,
            "pitchDeg": self._temporary_reference_pitch_deg,
        }

    def start_pitch_guard(self) -> dict[str, object]:
        self._require_calibrated()
        if not self.safety.armed:
            raise RuntimeError("arm before starting V2 pitch guard")
        if self.phase not in {ImuPhase.READY, ImuPhase.YAW_STEP_REACHED, ImuPhase.PITCH_GUARD}:
            raise RuntimeError(f"cannot start pitch guard from {self.phase}")
        self.sensor.set_dynamic_mode(True)
        self.phase = ImuPhase.PITCH_GUARD
        self.last_reason = "V2_PITCH_GUARD_ACTIVE"
        return self.status()

    def start_recovery(self, *, now_monotonic: float | None = None) -> dict[str, object]:
        with self.safety.safety_lock:
            self._require_calibrated()
            if not self.safety.armed:
                raise RuntimeError("arm before starting V2 recovery")
            retrying_watchdog_disarm = (
                self.phase is ImuPhase.ERROR
                and self.last_reason == "V2_LOCAL_COMMAND_REJECTED:EDGE_NOT_ARMED"
            )
            if retrying_watchdog_disarm:
                try:
                    observation = self.sensor.read()
                    self._validate_observation(observation)
                except RuntimeError as error:
                    return self._fail(str(error))
                except Exception as error:
                    return self._fail(f"V2_IMU_START_EXCEPTION:{error}")
                self.last_observation = observation
                self._invalid_reads = 0
            if self.phase not in {
                ImuPhase.READY,
                ImuPhase.PITCH_GUARD,
                ImuPhase.PITCH_LIMIT_REACHED,
                ImuPhase.ROLL_LIMIT_REACHED,
            } and not retrying_watchdog_disarm:
                if self.phase is ImuPhase.ERROR:
                    self._fail(self.last_reason)
                raise RuntimeError(
                    "V2 recovery requires ready, pitch guard, or a reached advance limit"
                )
            self.safety.stop_motion("V2_RECOVERY_START")
            try:
                self.sensor.set_dynamic_mode(True)
            except Exception as error:
                return self._fail(f"V2_IMU_START_EXCEPTION:{error}")
            self.phase = ImuPhase.PRIMARY_YAW
            self.last_reason = "V2_PRIMARY_YAW"
            self._settled = 0
            self._phase_started_at = time.monotonic() if now_monotonic is None else now_monotonic
            return self.status()

    def start_local_recovery(
        self,
        *,
        now: datetime,
        deadline_s: float = 60.0,
        now_monotonic: float | None = None,
    ) -> dict[str, object]:
        """Atomically validate and launch heartbeat-independent RECOVER_PATH."""
        with self.safety.safety_lock:
            monotonic_now = time.monotonic() if now_monotonic is None else now_monotonic
            try:
                self._require_calibrated()
                if self.phase not in {
                    ImuPhase.READY,
                    ImuPhase.YAW_STEP_REACHED,
                    ImuPhase.PITCH_GUARD,
                    ImuPhase.PITCH_LIMIT_REACHED,
                    ImuPhase.ROLL_LIMIT_REACHED,
                }:
                    raise RuntimeError(
                        "V2 recover path requires ready, reached yaw step, pitch guard, or a reached advance limit"
                    )
                observation = self.sensor.read()
                self._validate_observation(observation)
                self.last_observation = observation
                self.safety.begin_local_operation(
                    "RECOVER_PATH", now=now, deadline_s=deadline_s
                )
                self._local_operation = "RECOVER_PATH"
                self._operation_started_at = monotonic_now
                self._operation_deadline_s = float(deadline_s)
                self.sensor.set_dynamic_mode(True)
            except Exception as error:
                reason = str(error)
                if not reason.startswith("V2_"):
                    reason = f"V2_RECOVER_PATH_START_FAILED:{reason}"
                return self._fail(reason)
            self.phase = ImuPhase.PRIMARY_YAW
            self.last_reason = "V2_PRIMARY_YAW"
            self._settled = 0
            self._invalid_reads = 0
            self._phase_started_at = monotonic_now
            return self.status()

    def poll_once(
        self,
        *,
        now_monotonic: float | None = None,
        now: datetime,
    ) -> dict[str, object]:
        with self.safety.safety_lock:
            try:
                return self._poll_once_locked(now_monotonic=now_monotonic, now=now)
            except Exception as error:
                return self._fail(f"V2_IMU_RUNTIME_EXCEPTION:{error}")

    def _poll_once_locked(
        self,
        *,
        now_monotonic: float | None = None,
        now: datetime,
    ) -> dict[str, object]:
        if self.phase is ImuPhase.UNCALIBRATED:
            return self.status()
        if self.phase is ImuPhase.ERROR:
            return self.status()
        monotonic_now = time.monotonic() if now_monotonic is None else now_monotonic
        if (
            self._local_operation is not None
            and self._operation_started_at is not None
            and self._operation_deadline_s is not None
            and monotonic_now - self._operation_started_at >= self._operation_deadline_s
        ):
            deadline_reason = {
                "RECOVER_PATH": "V2_RECOVER_PATH_DEADLINE",
                "YAW_STEP": "V2_YAW_STEP_DEADLINE",
            }.get(self._local_operation, "V2_YAW_SCAN_360_DEADLINE")
            return self._fail(deadline_reason)
        if self._local_operation is not None and (
            not self.safety.armed
            or getattr(self.safety, "local_operation", self._local_operation)
            != self._local_operation
        ):
            return self._fail(f"V2_{self._local_operation}_CANCELLED")
        observation = self.sensor.read()
        self.last_observation = observation
        finite_observation = all(
            math.isfinite(value)
            for value in (
                observation.yaw_deg,
                observation.roll_deg,
                observation.pitch_deg,
                observation.age_s,
            )
        )
        stale_observation = finite_observation and (
            observation.age_s < 0.0 or observation.age_s > self.stale_after_s
        )
        if not observation.valid or not finite_observation or stale_observation:
            self._invalid_reads += 1
            self._local_invalid_started_at = None
            self._local_valid_recovery_reads = 0
            refresh_lease = getattr(self.safety, "refresh_motion_lease", None)
            if callable(refresh_lease):
                refresh_lease(now=now, ttl_s=0.25)
            quality = "STALE" if stale_observation else "INVALID"
            self.last_reason = f"V2_IMU_{quality}_IGNORED_{self._invalid_reads}"
            return self.status()
        self._invalid_reads = 0
        self._local_invalid_started_at = None
        self._local_valid_recovery_reads = 0
        try:
            self._validate_observation(observation)
        except RuntimeError as error:
            return self._fail(str(error))

        if self.phase is ImuPhase.YAW_SCAN_360:
            current_yaw = self.relative_yaw_deg
            previous_yaw = self._scan_previous_yaw_deg
            accumulated = self._scan_accumulated_clockwise_deg
            assert current_yaw is not None and previous_yaw is not None and accumulated is not None
            delta = wrap_degrees(current_yaw - previous_yaw)
            accumulated += -delta if self._scan_direction > 0 else delta
            self._scan_previous_yaw_deg = current_yaw
            self._scan_accumulated_clockwise_deg = accumulated
            target_reached = accumulated >= max(
                0.0, self._scan_required_deg - self.yaw_tolerance_deg
            )
            if self._scan_direction > 0:
                target_reached = target_reached and abs(
                    wrap_degrees(current_yaw - self._scan_target_yaw_deg)
                ) <= self.yaw_tolerance_deg
            if target_reached:
                operation = self._local_operation or "YAW_SCAN_360"
                completed_reason = f"V2_{operation}_COMPLETED"
                self.safety.stop_motion(completed_reason)
                self.sensor.set_dynamic_mode(False)
                self.phase = ImuPhase.COMPLETED
                self.last_reason = completed_reason
                self.safety.finish_local_operation(
                    operation, completed_reason
                )
                self._local_operation = None
                self._operation_started_at = None
                self._operation_deadline_s = None
                self._phase_started_at = None
                return self.status()
            if self._scan_direction > 0:
                return self._apply(0.80, -0.80, "V2_YAW_SCAN_360", now)
            return self._apply(-0.80, 0.80, "V2_YAW_TEMP_180", now)

        if self.phase in {
            ImuPhase.READY,
            ImuPhase.YAW_STEP_REACHED,
            ImuPhase.PITCH_LIMIT_REACHED,
            ImuPhase.ROLL_LIMIT_REACHED,
            ImuPhase.COMPLETED,
            ImuPhase.ERROR,
        }:
            return self.status()
        if self.phase is ImuPhase.PITCH_GUARD:
            return self.status()

        active_phase_timeout_s = (
            self.yaw_step_timeout_s
            if self.phase is ImuPhase.PIVOT_YAW
            else self.phase_timeout_s
        )
        if (
            self._phase_started_at is not None
            and monotonic_now - self._phase_started_at > active_phase_timeout_s
        ):
            return self._fail("V2_RECOVERY_TIMEOUT")

        if self.phase is ImuPhase.PIVOT_YAW:
            target = self._yaw_target_deg
            current = self.relative_yaw_deg
            assert target is not None and current is not None
            error = wrap_degrees(target - current)
            reached = (
                abs(error) <= self.yaw_tolerance_deg
                or (self._yaw_direction > 0 and error <= 0.0)
                or (self._yaw_direction < 0 and error >= 0.0)
            )
            if reached:
                self.phase = ImuPhase.YAW_STEP_REACHED
                self.last_reason = "V2_YAW_STEP_REACHED"
                if self._local_operation == "YAW_STEP":
                    self.safety.finish_local_operation(
                        "YAW_STEP", "V2_YAW_STEP_REACHED"
                    )
                    self._local_operation = None
                    self._operation_started_at = None
                    self._operation_deadline_s = None
                else:
                    self.safety.stop_motion("V2_YAW_STEP_REACHED")
                self._phase_started_at = None
                return self.status()
            power = self.turn_power
            left, right = (-power, power) if error > 0 else (power, -power)
            return self._apply(left, right, "V2_YAW_STEP", now)

        for _ in range(3):
            if self.phase in {ImuPhase.PRIMARY_YAW, ImuPhase.FINAL_YAW}:
                error = self.yaw_error_deg
                assert error is not None
                if abs(error) <= self.yaw_tolerance_deg:
                    self._settled += 1
                    if self._settled < self.settle_samples:
                        self.safety.stop_motion("V2_YAW_SETTLING")
                        self.last_reason = "V2_YAW_SETTLING"
                        return self.status()
                    self._settled = 0
                    if self.phase is ImuPhase.PRIMARY_YAW:
                        self._transition(ImuPhase.ROLL_RECOVERY, monotonic_now, "V2_ROLL_RECOVERY")
                        continue
                    self.safety.stop_motion("V2_POSE_RECOVERED")
                    self.sensor.set_dynamic_mode(False)
                    self.phase = ImuPhase.COMPLETED
                    self.last_reason = "V2_POSE_RECOVERED"
                    if self._local_operation == "RECOVER_PATH":
                        self.safety.finish_local_operation(
                            "RECOVER_PATH", "V2_POSE_RECOVERED"
                        )
                        self._local_operation = None
                        self._operation_started_at = None
                        self._operation_deadline_s = None
                    return self.status()
                self._settled = 0
                power = (
                    self.slow_turn_power
                    if abs(error) <= self.slow_threshold_deg
                    else self.turn_power
                )
                left, right = (-power, power) if error > 0 else (power, -power)
                return self._apply(left, right, "V2_PRIMARY_YAW" if self.phase is ImuPhase.PRIMARY_YAW else "V2_FINAL_YAW", now)

            error = self.roll_error_deg
            assert error is not None
            if abs(error) <= self.roll_tolerance_deg:
                self._settled += 1
                if self._settled < self.settle_samples:
                    self.safety.stop_motion("V2_ROLL_SETTLING")
                    self.last_reason = "V2_ROLL_SETTLING"
                    return self.status()
                self._settled = 0
                self._transition(ImuPhase.FINAL_YAW, monotonic_now, "V2_FINAL_YAW")
                continue
            self._settled = 0
            if error > 0:
                left, right = self.roll_outer_power, self.roll_inner_power
            else:
                left, right = self.roll_inner_power, self.roll_outer_power
            return self._apply(left, right, "V2_ROLL_RECOVERY", now)
        return self._fail("V2_INVALID_PHASE_TRANSITION")

    def reset(self) -> dict[str, object]:
        self.safety.stop_motion("V2_IMU_RESET")
        self.sensor.set_dynamic_mode(False)
        self.reference_yaw_deg = None
        self.reference_roll_deg = None
        self.reference_pitch_deg = None
        self._pitch_guard_reference_deg = None
        self._advance_limit_trigger = None
        self.last_observation = None
        self.phase = ImuPhase.UNCALIBRATED
        self.last_reason = "V2_IMU_RESET"
        self._settled = 0
        self._local_operation = None
        self._operation_started_at = None
        self._operation_deadline_s = None
        self._phase_started_at = None
        self._yaw_target_deg = None
        self._yaw_direction = 0
        self._reference_restored = False
        self._restored_nonce = None
        self._scan_previous_yaw_deg = None
        self._scan_accumulated_clockwise_deg = None
        return self.status()

    def run(self, stop_event, *, poll_interval_s: float = 0.02) -> None:
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise ValueError("IMU poll interval must be positive")
        while not stop_event.is_set():
            try:
                self.poll_once(now=datetime.now(timezone.utc))
            except Exception as error:
                self._fail(f"V2_IMU_RUNTIME_EXCEPTION:{error}")
            stop_event.wait(poll_interval_s)

    def close(self) -> None:
        try:
            self.safety.stop_motion("V2_IMU_CLOSE")
            self.sensor.set_dynamic_mode(False)
        finally:
            self.sensor.close()

    def status(self) -> dict[str, object]:
        return {
            "available": True,
            "calibrated": self.reference_yaw_deg is not None,
            "phase": self.phase,
            "reason": self.last_reason,
            "yawDeg": self.relative_yaw_deg,
            "rollDeg": self.relative_roll_deg,
            "pitchDeg": self.relative_pitch_deg,
            "yawErrorDeg": self.yaw_error_deg,
            "rollErrorDeg": self.roll_error_deg,
            "pitchStopDeg": self.pitch_stop_deg,
            "rollStopDeg": self.roll_stop_deg,
            "advanceLimitTrigger": self._advance_limit_trigger,
            "yawTargetDeg": self._yaw_target_deg,
            "yawStepDeg": self.yaw_step_deg,
            "yawLimitDeg": self.yaw_limit_deg,
            "warningPitchDeg": None,
            "localOperation": self._local_operation,
            "operationDeadlineS": self._operation_deadline_s,
            "referenceRestored": self._reference_restored,
            "temporaryReference": self._temporary_reference_status(),
            "scanAccumulatedClockwiseDeg": self._scan_accumulated_clockwise_deg,
            "scanRequiredClockwiseDeg": self._scan_required_deg,
            "scanTargetYawDeg": self._scan_target_yaw_deg,
            "valid": self.last_observation.valid if self.last_observation else False,
            "ageS": self.last_observation.age_s if self.last_observation else None,
        }

    @property
    def relative_yaw_deg(self) -> float | None:
        if self.last_observation is None or self.reference_yaw_deg is None:
            return None
        return wrap_degrees(self.last_observation.yaw_deg - self.reference_yaw_deg)

    @property
    def relative_roll_deg(self) -> float | None:
        if self.last_observation is None or self.reference_roll_deg is None:
            return None
        return wrap_degrees(self.last_observation.roll_deg - self.reference_roll_deg)

    @property
    def relative_pitch_deg(self) -> float | None:
        if self.last_observation is None or self._pitch_guard_reference_deg is None:
            return None
        return wrap_degrees(
            self.last_observation.pitch_deg - self._pitch_guard_reference_deg
        )

    @property
    def yaw_error_deg(self) -> float | None:
        relative = self.relative_yaw_deg
        return None if relative is None else wrap_degrees(-relative)

    @property
    def roll_error_deg(self) -> float | None:
        return self.relative_roll_deg

    def _transition(self, phase: ImuPhase, now_monotonic: float, reason: str) -> None:
        self.phase = phase
        self.last_reason = reason
        self._phase_started_at = now_monotonic

    def _apply(self, left: float, right: float, reason: str, now: datetime) -> dict[str, object]:
        accepted, apply_reason = self.safety.apply_local_signed(
            left,
            right,
            reason=reason,
            now=now,
            ttl_s=0.25,
            local_operation=self._local_operation,
        )
        if not accepted:
            return self._fail(f"V2_LOCAL_COMMAND_REJECTED:{apply_reason}")
        self.last_reason = reason
        return self.status()

    def _validate_observation(self, observation: ImuObservation) -> None:
        values = (
            observation.yaw_deg,
            observation.roll_deg,
            observation.pitch_deg,
            observation.age_s,
        )
        if not observation.valid or not all(math.isfinite(value) for value in values):
            raise RuntimeError("V2_IMU_INVALID")
        if observation.age_s < 0 or observation.age_s > self.stale_after_s:
            raise RuntimeError("V2_IMU_STALE")

    def _require_calibrated(self) -> None:
        if None in (self.reference_yaw_deg, self.reference_roll_deg, self.reference_pitch_deg):
            raise RuntimeError("calibrate V2 IMU before use")

    def _fail(self, reason: str) -> dict[str, object]:
        self.safety.stop_motion(reason)
        self.safety.disarm(reason)
        try:
            self.sensor.set_dynamic_mode(False)
        except Exception:
            pass
        self.phase = ImuPhase.ERROR
        self.last_reason = reason
        self._local_operation = None
        self._operation_started_at = None
        self._operation_deadline_s = None
        return self.status()
