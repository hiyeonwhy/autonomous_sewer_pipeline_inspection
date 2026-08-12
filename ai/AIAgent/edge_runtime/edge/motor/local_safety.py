from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
import math
from threading import RLock
from typing import Protocol


class MotorBackend(Protocol):
    @property
    def current_speeds(self) -> tuple[float, float]: ...

    def set_speeds(self, left: float, right: float) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


def safety_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.safety_lock:
            return method(self, *args, **kwargs)

    return locked


@dataclass(frozen=True)
class MotorCommand:
    command_id: str
    mission_id: str
    sequence: int
    action: str
    left_speed: float
    right_speed: float
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CommandAck:
    command_id: str
    sequence: int
    accepted: bool
    reason: str
    applied_left_speed: float
    applied_right_speed: float
    timestamp: datetime


class LocalSafetySupervisor:
    LOCAL_OPERATION_MAX_DEADLINE_S = 60.0
    LOCAL_OPERATION_COMMANDS = {
        "YAW_STEP": {"V2_YAW_STEP"},
        "RECOVER_PATH": {"V2_PRIMARY_YAW", "V2_ROLL_RECOVERY", "V2_FINAL_YAW"},
        "YAW_SCAN_360": {"V2_YAW_SCAN_360"},
        "YAW_TEMP_180": {"V2_YAW_TEMP_180"},
        "FORWARD_UNTIL_DISTANCE": {"V2_FORWARD_UNTIL_DISTANCE"},
        "FORWARD_TIMED_CONTINUOUS": {"V2_FORWARD_TIMED_CONTINUOUS"},
    }
    ALLOWED_ACTIONS = {
        "FOLLOW_PIPE",
        "SLOW_DOWN",
        "AVOID_LEFT",
        "AVOID_RIGHT",
        "BYPASS_OBSTACLE",
        "AI_LOCAL_PLAN",
        "BACKWARD",
        "CONTINUE",
        "RECOVER_LEFT",
        "RECOVER_RIGHT",
        "REACQUIRE_CENTER",
        "V2_PITCH_ADVANCE",
        "STOP",
    }

    def __init__(
        self,
        motor: MotorBackend,
        *,
        max_speed: float = 0.90,
        heartbeat_timeout_ms: int = 1000,
        ultrasonic_stop_m: float = 0.10,
    ) -> None:
        self.motor = motor
        self.safety_lock = RLock()
        self.max_speed = max_speed
        self.heartbeat_timeout = timedelta(milliseconds=heartbeat_timeout_ms)
        self.ultrasonic_stop_m = ultrasonic_stop_m

        self.armed = False
        self.emergency_stop = False
        self.last_heartbeat: datetime | None = None
        self.heartbeat_count = 0
        self.last_heartbeat_gap_ms: float | None = None
        self.max_heartbeat_gap_ms: float | None = None
        self.heartbeat_timeout_gap_ms: float | None = None
        self.last_sequence = -1
        self.active_command_expires_at: datetime | None = None
        self.local_operation: str | None = None
        self.local_operation_deadline: datetime | None = None
        self.local_operation_target_distance_m: float | None = None
        self.last_terminal_reason: str | None = None
        self.front_distance_m: float | None = None
        self.ultrasonic_invalid = False
        self.motor.stop()

    @safety_locked
    def arm(self, *, now: datetime | None = None) -> None:
        if self.emergency_stop:
            raise RuntimeError("Cannot arm while emergency stop is active")
        if self.local_operation is not None:
            raise RuntimeError("Cannot arm while local operation is active")
        self.motor.stop()
        self.last_sequence = -1
        self.last_heartbeat = now or datetime.now(timezone.utc)
        self.heartbeat_timeout_gap_ms = None
        self.active_command_expires_at = None
        self.armed = True

    @safety_locked
    def disarm(self, reason: str = "DISARMED") -> str:
        self.motor.stop()
        self.armed = False
        self.active_command_expires_at = None
        self.local_operation = None
        self.local_operation_deadline = None
        self.local_operation_target_distance_m = None
        self.last_terminal_reason = reason
        return reason

    @safety_locked
    def begin_local_operation(
        self,
        operation: str,
        *,
        now: datetime | None = None,
        deadline_s: float,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        self.motor.stop()
        if (
            operation not in self.LOCAL_OPERATION_COMMANDS
            or not isinstance(deadline_s, (int, float))
            or not math.isfinite(deadline_s)
            or not 0 < deadline_s <= self.LOCAL_OPERATION_MAX_DEADLINE_S
        ):
            self.disarm("INVALID_LOCAL_OPERATION_DEADLINE")
            raise ValueError("local operation deadline must be finite and at most 60 seconds")
        if self.emergency_stop:
            self.disarm("EMERGENCY_STOP")
            raise RuntimeError("Cannot start local operation during emergency stop")
        if self.local_operation is not None:
            self.disarm("LOCAL_OPERATION_ALREADY_ACTIVE")
            raise RuntimeError("local operation is already active")
        self.last_sequence = -1
        self.last_heartbeat = None
        self.active_command_expires_at = None
        self.local_operation = operation
        self.local_operation_deadline = current + timedelta(seconds=float(deadline_s))
        self.last_terminal_reason = None
        self.armed = True

    @safety_locked
    def finish_local_operation(self, operation: str, reason: str) -> str:
        if self.local_operation != operation:
            return self.disarm("LOCAL_OPERATION_MISMATCH")
        return self.disarm(reason)

    @safety_locked
    def begin_continuous_timed_forward(
        self,
        *,
        speed: float,
        duration_s: float,
        now: datetime | None = None,
    ) -> None:
        values = (speed, duration_s)
        if not all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in values
        ):
            self.disarm("INVALID_TIMED_FORWARD_REQUEST")
            raise ValueError("timed forward values must be finite")
        if not 0 < speed <= min(self.max_speed, 0.40):
            self.disarm("INVALID_TIMED_FORWARD_SPEED")
            raise ValueError("timed forward speed must be at most 0.40")
        if not 0 < duration_s <= self.LOCAL_OPERATION_MAX_DEADLINE_S:
            self.disarm("INVALID_TIMED_FORWARD_DURATION")
            raise ValueError("timed forward duration must be at most 60 seconds")
        self.begin_local_operation(
            "FORWARD_TIMED_CONTINUOUS", now=now, deadline_s=duration_s
        )
        self.motor.set_speeds(float(speed), float(speed))

    @safety_locked
    def begin_continuous_forward(
        self,
        *,
        speed: float,
        target_distance_m: float,
        deadline_s: float,
        now: datetime | None = None,
    ) -> None:
        values = (speed, target_distance_m, deadline_s)
        if not all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in values
        ):
            self.disarm("INVALID_CONTINUOUS_FORWARD_REQUEST")
            raise ValueError("continuous forward values must be finite")
        if not 0 < speed <= min(self.max_speed, 0.40):
            self.disarm("INVALID_CONTINUOUS_FORWARD_SPEED")
            raise ValueError("continuous forward speed must be at most 0.40")
        if not 0.05 <= target_distance_m <= 2.0:
            self.disarm("INVALID_CONTINUOUS_FORWARD_TARGET")
            raise ValueError("continuous forward target must be between 0.05m and 2.0m")
        if (
            self.front_distance_m is not None
            and not self.ultrasonic_invalid
            and math.isfinite(self.front_distance_m)
            and self.front_distance_m <= target_distance_m
        ):
            self.disarm("FORWARD_DISTANCE_ALREADY_REACHED")
            return
        self.begin_local_operation(
            "FORWARD_UNTIL_DISTANCE", now=now, deadline_s=deadline_s
        )
        self.local_operation_target_distance_m = float(target_distance_m)
        self.motor.set_speeds(float(speed), float(speed))

    @safety_locked
    def heartbeat(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        if self.last_heartbeat is not None:
            gap_ms = round(
                max(0.0, (current - self.last_heartbeat).total_seconds() * 1000.0), 3
            )
            self.last_heartbeat_gap_ms = gap_ms
            self.max_heartbeat_gap_ms = max(self.max_heartbeat_gap_ms or 0.0, gap_ms)
        self.last_heartbeat = current
        self.heartbeat_count += 1

    @safety_locked
    def set_emergency_stop(self, active: bool) -> None:
        self.emergency_stop = active
        if active:
            self.disarm("EMERGENCY_STOP")

    @safety_locked
    def update_ultrasonic(self, distance_m: float | None) -> None:
        self.front_distance_m = distance_m
        self.ultrasonic_invalid = distance_m is not None and (
            not math.isfinite(distance_m) or distance_m < 0.0
        )
        if self.local_operation in {"RECOVER_PATH", "YAW_SCAN_360", "YAW_TEMP_180"}:
            return
        if (
            self.local_operation == "FORWARD_UNTIL_DISTANCE"
            and distance_m is not None
            and not self.ultrasonic_invalid
            and self.local_operation_target_distance_m is not None
            and distance_m <= self.local_operation_target_distance_m
        ):
            self.finish_local_operation(
                "FORWARD_UNTIL_DISTANCE", "V2_FORWARD_DISTANCE_REACHED"
            )
            return
        if (
            self.ultrasonic_stop_m > 0.0
            and distance_m is not None
            and not self.ultrasonic_invalid
            and distance_m <= self.ultrasonic_stop_m
        ):
            self.motor.stop()
            self.active_command_expires_at = None
            if self.local_operation is not None:
                self.disarm("ULTRASONIC_HARD_STOP")

    @safety_locked
    def apply(self, command: MotorCommand, *, now: datetime | None = None) -> CommandAck:
        current = now or datetime.now(timezone.utc)
        if command.action == "STOP":
            if self.local_operation is not None:
                self.disarm("STOP_APPLIED")
            else:
                self.motor.stop()
                self.active_command_expires_at = None
            self.last_sequence = max(self.last_sequence, command.sequence)
            return self._ack(command, current, True, "STOP_APPLIED", 0.0, 0.0)
        if self.local_operation is not None:
            return self._reject(command, current, "LOCAL_OPERATION_ACTIVE")
        if self.emergency_stop:
            return self._reject(command, current, "EMERGENCY_STOP")

        if (
            self.ultrasonic_stop_m > 0.0
            and self.front_distance_m is not None
            and not self.ultrasonic_invalid
            and self.front_distance_m <= self.ultrasonic_stop_m
        ):
            return self._reject(command, current, "ULTRASONIC_HARD_STOP")
        if not self.armed:
            return self._reject(command, current, "EDGE_NOT_ARMED")
        if self.last_heartbeat is None or current - self.last_heartbeat > self.heartbeat_timeout:
            self.disarm("HEARTBEAT_TIMEOUT")
            return self._ack(command, current, False, "HEARTBEAT_TIMEOUT", 0.0, 0.0)
        if command.expires_at <= current:
            return self._reject(command, current, "COMMAND_EXPIRED")
        if command.issued_at > current + timedelta(seconds=1):
            return self._reject(command, current, "COMMAND_FROM_FUTURE")
        if command.sequence <= self.last_sequence:
            return self._reject(command, current, "INVALID_SEQUENCE")
        if command.action not in self.ALLOWED_ACTIONS:
            return self._reject(command, current, "ACTION_NOT_ALLOWED")
        if not math.isfinite(command.left_speed) or not math.isfinite(command.right_speed):
            return self._reject(command, current, "NON_FINITE_SPEED")
        if command.left_speed < 0 or command.right_speed < 0:
            return self._reject(command, current, "NEGATIVE_SPEED_NOT_ALLOWED")

        if command.action == "BACKWARD":
            duration_s = (command.expires_at - command.issued_at).total_seconds()
            remaining_s = (command.expires_at - current).total_seconds()
            if (
                duration_s <= 0.0
                or duration_s > 0.8
                or remaining_s > 0.8
                or max(command.left_speed, command.right_speed) > 0.30
            ):
                return self._reject(command, current, "BACKWARD_RECOVERY_LIMIT")

        left = min(command.left_speed, self.max_speed)
        right = min(command.right_speed, self.max_speed)
        applied_left, applied_right = (
            (-left, -right) if command.action == "BACKWARD" else (left, right)
        )
        self.motor.set_speeds(applied_left, applied_right)
        self.active_command_expires_at = command.expires_at
        self.last_sequence = command.sequence
        return self._ack(command, current, True, "COMMAND_APPLIED", left, right)

    @safety_locked
    def refresh_motion_lease(
        self, *, now: datetime, ttl_s: float = 0.25
    ) -> bool:
        if self.emergency_stop or not self.armed:
            return False
        if self.local_operation is not None and (
            self.local_operation_deadline is None or now >= self.local_operation_deadline
        ):
            self.disarm("LOCAL_OPERATION_DEADLINE")
            return False
        self.active_command_expires_at = now + timedelta(seconds=ttl_s)
        return True

    @safety_locked
    def stop_motion(self, reason: str = "LOCAL_STOP") -> None:
        self.motor.stop()
        self.active_command_expires_at = None

    @safety_locked
    def apply_local_signed(
        self,
        left: float,
        right: float,
        *,
        reason: str,
        now: datetime,
        ttl_s: float = 0.25,
        local_operation: str | None = None,
    ) -> tuple[bool, str]:
        values = (left, right, ttl_s)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            self.stop_motion("LOCAL_NON_FINITE")
            return False, "LOCAL_NON_FINITE"
        if not 0 < ttl_s <= 0.25:
            self.stop_motion("LOCAL_TTL_LIMIT")
            return False, "LOCAL_TTL_LIMIT"
        if max(abs(left), abs(right)) > min(self.max_speed, 0.80):
            self.stop_motion("LOCAL_SPEED_LIMIT")
            return False, "LOCAL_SPEED_LIMIT"
        if self.emergency_stop:
            self.stop_motion("EMERGENCY_STOP")
            return False, "EMERGENCY_STOP"
        if not self.armed:
            self.stop_motion("EDGE_NOT_ARMED")
            return False, "EDGE_NOT_ARMED"
        if self.local_operation is not None:
            if local_operation != self.local_operation:
                self.disarm("LOCAL_OPERATION_CAPABILITY_REQUIRED")
                return False, "LOCAL_OPERATION_CAPABILITY_REQUIRED"
            if reason not in self.LOCAL_OPERATION_COMMANDS[self.local_operation]:
                self.disarm("LOCAL_OPERATION_COMMAND_REJECTED")
                return False, "LOCAL_OPERATION_COMMAND_REJECTED"
            if self.local_operation_deadline is None or now >= self.local_operation_deadline:
                self.disarm("LOCAL_OPERATION_DEADLINE")
                return False, "LOCAL_OPERATION_DEADLINE"
        elif local_operation is not None:
            self.disarm("LOCAL_OPERATION_NOT_ACTIVE")
            return False, "LOCAL_OPERATION_NOT_ACTIVE"
        else:
            if (
                self.ultrasonic_stop_m > 0.0
                and self.front_distance_m is not None
                and not self.ultrasonic_invalid
                and self.front_distance_m <= self.ultrasonic_stop_m
            ):
                self.stop_motion("ULTRASONIC_HARD_STOP")
                return False, "ULTRASONIC_HARD_STOP"
            if self.last_heartbeat is None or now - self.last_heartbeat > self.heartbeat_timeout:
                self.disarm("HEARTBEAT_TIMEOUT")
                return False, "HEARTBEAT_TIMEOUT"
        self.motor.set_speeds(float(left), float(right))
        self.active_command_expires_at = now + timedelta(seconds=ttl_s)
        return True, "COMMAND_APPLIED"

    @safety_locked
    def watchdog_tick(self, *, now: datetime | None = None) -> str | None:
        current = now or datetime.now(timezone.utc)
        if not self.armed:
            return None
        if self.local_operation is not None:
            if self.local_operation_deadline is None or current >= self.local_operation_deadline:
                if self.local_operation == "FORWARD_UNTIL_DISTANCE":
                    return self.finish_local_operation(
                        "FORWARD_UNTIL_DISTANCE", "V2_FORWARD_DURATION_COMPLETED"
                    )
                if self.local_operation == "FORWARD_TIMED_CONTINUOUS":
                    return self.finish_local_operation(
                        "FORWARD_TIMED_CONTINUOUS", "V2_FORWARD_TIMED_COMPLETED"
                    )
                return self.disarm("LOCAL_OPERATION_DEADLINE")
        elif self.last_heartbeat is None or current - self.last_heartbeat > self.heartbeat_timeout:
            if self.last_heartbeat is not None:
                self.heartbeat_timeout_gap_ms = round(
                    max(0.0, (current - self.last_heartbeat).total_seconds() * 1000.0),
                    3,
                )
            return self.disarm("HEARTBEAT_TIMEOUT")
        if (
            self.active_command_expires_at is not None
            and current >= self.active_command_expires_at
        ):
            self.motor.stop()
            self.active_command_expires_at = None
            return "COMMAND_TTL_EXPIRED"
        return None

    def close(self) -> None:
        try:
            self.disarm("CLOSED")
        finally:
            self.motor.close()

    def _reject(self, command: MotorCommand, now: datetime, reason: str) -> CommandAck:
        self.motor.stop()
        self.active_command_expires_at = None
        return self._ack(command, now, False, reason, 0.0, 0.0)

    @staticmethod
    def _ack(
        command: MotorCommand,
        now: datetime,
        accepted: bool,
        reason: str,
        left: float,
        right: float,
    ) -> CommandAck:
        return CommandAck(
            command_id=command.command_id,
            sequence=command.sequence,
            accepted=accepted,
            reason=reason,
            applied_left_speed=left,
            applied_right_speed=right,
            timestamp=now,
        )
