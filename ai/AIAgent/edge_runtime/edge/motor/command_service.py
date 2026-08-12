from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from hmac import compare_digest, new as hmac_new
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from edge.motor.local_safety import CommandAck, LocalSafetySupervisor, MotorCommand


class EdgeCommandService:
    MAX_CONSUMED_REFERENCE_NONCES = 1024

    def __init__(
        self,
        safety: LocalSafetySupervisor,
        *,
        token: str,
        imu: Any | None = None,
        reference_nonce_store: str | os.PathLike[str] = "/run/user/1000/pipecheckkk-reference-nonces.json",
    ) -> None:
        if not token:
            raise ValueError("edge command token must not be empty")
        self.safety = safety
        self._token = token
        self._reference_nonce_store = Path(reference_nonce_store)
        self.imu = imu

    def _active_operation_rejection(self) -> tuple[int, dict[str, object]] | None:
        with self.safety.safety_lock:
            operation = self.safety.local_operation
            if operation is None:
                return None
            return 409, {
                "ok": False,
                "accepted": False,
                "reason": "LOCAL_OPERATION_ACTIVE",
                "localOperation": operation,
            }

    def health(self) -> dict[str, object]:
        with self.safety.safety_lock:
            left_output, right_output = self.safety.motor.current_speeds
            payload: dict[str, object] = {
                "ok": True,
                "service": "pipecheckkk-edge-motor",
                "controlMode": "real-motor",
                "armed": self.safety.armed,
                "emergencyStop": self.safety.emergency_stop,
                "maxSpeed": self.safety.max_speed,
                "frontDistanceM": self.safety.front_distance_m,
                "ultrasonicStopM": self.safety.ultrasonic_stop_m,
                "lastSequence": self.safety.last_sequence,
                "localOperation": self.safety.local_operation,
                "lastTerminalReason": self.safety.last_terminal_reason,
                "localOperationTargetDistanceM": (
                    self.safety.local_operation_target_distance_m
                ),
                "motorOutput": {
                    "left": left_output,
                    "right": right_output,
                },
                "heartbeat": {
                    "count": self.safety.heartbeat_count,
                    "lastReceivedAt": (
                        self.safety.last_heartbeat.isoformat()
                        if self.safety.last_heartbeat is not None
                        else None
                    ),
                    "lastGapMs": self.safety.last_heartbeat_gap_ms,
                    "maxGapMs": self.safety.max_heartbeat_gap_ms,
                    "timeoutGapMs": self.safety.heartbeat_timeout_gap_ms,
                },
            }
            payload["imu"] = (
                self.imu.status()
                if self.imu is not None
                else {"available": False, "calibrated": False, "phase": "UNAVAILABLE"}
            )
            return payload

    def imu_calibrate(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        if self.safety.armed:
            imu_phase = self.imu.status().get("phase")
            if imu_phase != "COMPLETED":
                return 409, {"ok": False, "reason": "DISARM_BEFORE_IMU_CALIBRATION"}
        raw_samples = payload.get("samples", 100)
        if not isinstance(raw_samples, int) or not 2 <= raw_samples <= 500:
            return 400, {"ok": False, "reason": "INVALID_CALIBRATION_SAMPLES"}
        try:
            return 200, dict(self.imu.calibrate(samples=raw_samples))
        except Exception as error:
            self.safety.disarm("IMU_CALIBRATION_FAILED")
            return 409, {
                "ok": False,
                "reason": "IMU_CALIBRATION_FAILED",
                "detail": str(error),
            }

    def imu_start_yaw_step(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        direction = payload.get("direction")
        if direction not in {"LEFT", "RIGHT"}:
            self.safety.stop_motion("IMU_YAW_STEP_INVALID_DIRECTION")
            return 400, {"ok": False, "reason": "IMU_YAW_STEP_INVALID_DIRECTION"}
        try:
            return 200, dict(self.imu.start_yaw_step(direction))
        except Exception as error:
            self.safety.stop_motion("IMU_YAW_STEP_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_YAW_STEP_REJECTED",
                "detail": str(error),
            }

    def imu_start_current_yaw_step(
        self, token: str, payload: dict[str, Any]
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        direction = payload.get("direction")
        degrees = payload.get("degrees", 10.0)
        if direction not in {"LEFT", "RIGHT"}:
            self.safety.stop_motion("IMU_CURRENT_YAW_STEP_INVALID_DIRECTION")
            return 400, {
                "ok": False,
                "reason": "IMU_CURRENT_YAW_STEP_INVALID_DIRECTION",
            }
        if (
            not isinstance(degrees, (int, float))
            or isinstance(degrees, bool)
            or not math.isfinite(degrees)
            or not 1.0 <= float(degrees) <= 30.0
        ):
            self.safety.stop_motion("IMU_CURRENT_YAW_STEP_INVALID_DEGREES")
            return 400, {
                "ok": False,
                "reason": "IMU_CURRENT_YAW_STEP_INVALID_DEGREES",
            }
        try:
            return 200, dict(
                self.imu.start_current_yaw_step(direction, step_degrees=float(degrees))
            )
        except Exception as error:
            self.safety.stop_motion("IMU_CURRENT_YAW_STEP_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_CURRENT_YAW_STEP_REJECTED",
                "detail": str(error),
            }

    def imu_capture_temporary_reference(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        if self.safety.armed:
            return 409, {"ok": False, "reason": "DISARM_BEFORE_TEMP_REFERENCE_CAPTURE"}
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            return 400, {"ok": False, "reason": "INVALID_TEMP_REFERENCE_NAME"}
        try:
            return 200, dict(self.imu.capture_temporary_reference(name))
        except Exception as error:
            return 409, {
                "ok": False,
                "reason": "IMU_TEMP_REFERENCE_CAPTURE_REJECTED",
                "detail": str(error),
            }

    def imu_clear_temporary_reference(
        self, token: str
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        try:
            return 200, dict(self.imu.clear_temporary_reference())
        except Exception as error:
            self.safety.stop_motion("IMU_TEMP_REFERENCE_CLEAR_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_TEMP_REFERENCE_CLEAR_REJECTED",
                "detail": str(error),
            }

    def imu_restore_temporary_reference(
        self, token: str, payload: dict[str, Any]
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        try:
            return 200, dict(
                self.imu.restore_temporary_reference(
                    str(payload.get("name", "")),
                    yaw_deg=float(payload["yawDeg"]),
                    roll_deg=float(payload["rollDeg"]),
                    pitch_deg=float(payload["pitchDeg"]),
                )
            )
        except Exception as error:
            self.safety.stop_motion("TEMP_REFERENCE_RESTORE_REJECTED")
            return 409, {
                "ok": False,
                "reason": "TEMP_REFERENCE_RESTORE_REJECTED",
                "detail": str(error),
            }

    def imu_start_relative_yaw_step(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        direction = payload.get("direction")
        if direction not in {"LEFT", "RIGHT"}:
            self.safety.stop_motion("IMU_RELATIVE_YAW_STEP_INVALID_DIRECTION")
            return 400, {"ok": False, "reason": "IMU_RELATIVE_YAW_STEP_INVALID_DIRECTION"}
        try:
            return 200, dict(self.imu.start_relative_yaw_step(direction))
        except Exception as error:
            self.safety.stop_motion("IMU_RELATIVE_YAW_STEP_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_RELATIVE_YAW_STEP_REJECTED",
                "detail": str(error),
            }

    def imu_start_pitch_guard(self, token: str) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        try:
            return 200, dict(self.imu.start_pitch_guard())
        except Exception as error:
            self.safety.stop_motion("IMU_PITCH_GUARD_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_PITCH_GUARD_REJECTED",
                "detail": str(error),
            }

    def imu_start_recovery(self, token: str) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        try:
            return 200, dict(self.imu.start_recovery())
        except Exception as error:
            self.safety.stop_motion("IMU_RECOVERY_REJECTED")
            return 409, {
                "ok": False,
                "reason": "IMU_RECOVERY_REJECTED",
                "detail": str(error),
            }

    def forward_timed_continuous(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        try:
            self.safety.begin_continuous_timed_forward(
                speed=float(payload.get("speed", 0.40)),
                duration_s=float(payload.get("durationSeconds", 10.0)),
                now=now,
            )
        except Exception as error:
            self.safety.disarm("FORWARD_TIMED_START_FAILED")
            return 409, {
                "ok": False,
                "reason": "FORWARD_TIMED_START_FAILED",
                "detail": str(error),
            }
        return 202, {
            "ok": True,
            "accepted": True,
            "reason": "V2_FORWARD_TIMED_CONTINUOUS",
            "localOperation": self.safety.local_operation,
            "durationSeconds": float(payload.get("durationSeconds", 10.0)),
        }

    def forward_until_distance(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        try:
            self.safety.begin_continuous_forward(
                speed=float(payload.get("speed", 0.40)),
                target_distance_m=float(payload.get("targetDistanceM", 0.20)),
                deadline_s=float(payload.get("deadlineSeconds", 60.0)),
                now=now,
            )
        except Exception as error:
            self.safety.disarm("FORWARD_UNTIL_DISTANCE_START_FAILED")
            return 409, {
                "ok": False,
                "reason": "FORWARD_UNTIL_DISTANCE_START_FAILED",
                "detail": str(error),
            }
        return 202, {
            "ok": True,
            "accepted": True,
            "reason": "V2_FORWARD_UNTIL_DISTANCE",
            "localOperation": self.safety.local_operation,
            "targetDistanceM": self.safety.local_operation_target_distance_m,
        }

    def recover_path(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            self.safety.disarm("RECOVER_PATH_IMU_UNAVAILABLE")
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        deadline_s = payload.get("deadlineSeconds", 60.0)
        if (
            not isinstance(deadline_s, (int, float))
            or isinstance(deadline_s, bool)
            or not math.isfinite(deadline_s)
            or not 0 < deadline_s <= 60.0
        ):
            self.safety.disarm("INVALID_RECOVER_PATH_DEADLINE")
            return 400, {"ok": False, "reason": "INVALID_RECOVER_PATH_DEADLINE"}
        try:
            status = dict(
                self.imu.start_local_recovery(
                    now=now or datetime.now(timezone.utc),
                    deadline_s=float(deadline_s),
                )
            )
        except Exception as error:
            self.safety.disarm("RECOVER_PATH_START_FAILED")
            return 409, {
                "ok": False,
                "reason": "RECOVER_PATH_START_FAILED",
                "detail": str(error),
            }
        if str(status.get("phase")) != "PRIMARY_YAW":
            self.safety.disarm(str(status.get("reason", "RECOVER_PATH_START_FAILED")))
            return 409, status
        return 202, status

    def yaw_scan_360(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if rejection := self._active_operation_rejection():
            return rejection
        if self.imu is None:
            self.safety.disarm("YAW_SCAN_360_IMU_UNAVAILABLE")
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        deadline_s = payload.get("deadlineSeconds", 60.0)
        temporary_reference_name = payload.get("temporaryReferenceName")
        if temporary_reference_name is not None and not isinstance(
            temporary_reference_name, str
        ):
            self.safety.disarm("INVALID_TEMPORARY_REFERENCE_NAME")
            return 400, {"ok": False, "reason": "INVALID_TEMPORARY_REFERENCE_NAME"}
        if (
            not isinstance(deadline_s, (int, float))
            or isinstance(deadline_s, bool)
            or not math.isfinite(deadline_s)
            or not 0 < deadline_s <= 60.0
        ):
            self.safety.disarm("INVALID_YAW_SCAN_360_DEADLINE")
            return 400, {"ok": False, "reason": "INVALID_YAW_SCAN_360_DEADLINE"}
        try:
            start_kwargs: dict[str, object] = {
                "now": now or datetime.now(timezone.utc),
                "deadline_s": float(deadline_s),
            }
            if temporary_reference_name is not None:
                start_kwargs["temporary_reference_name"] = temporary_reference_name
            status = dict(self.imu.start_yaw_scan_360(**start_kwargs))
        except Exception as error:
            self.safety.disarm("YAW_SCAN_360_START_FAILED")
            return 409, {
                "ok": False,
                "reason": "YAW_SCAN_360_START_FAILED",
                "detail": str(error),
            }
        expected_operation = (
            "YAW_TEMP_180" if temporary_reference_name is not None else "YAW_SCAN_360"
        )
        if (
            str(status.get("phase")) != "YAW_SCAN_360"
            or status.get("localOperation") != expected_operation
        ):
            self.safety.disarm(str(status.get("reason", "YAW_SCAN_360_START_FAILED")))
            return 409, status
        return 202, status

    def imu_restore_reference(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if not self._valid_reference_auth(payload):
            return 400, {"ok": False, "reason": "INVALID_REFERENCE_AUTH"}
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        with self.safety.safety_lock:
            if self.safety.armed or str(self.imu.status().get("phase")) != "UNCALIBRATED":
                return 409, {
                    "ok": False,
                    "reason": "REFERENCE_RESTORE_REQUIRES_UNCALIBRATED_DISARMED",
                }
            nonce = payload.get("nonce")
            if not isinstance(nonce, str) or not nonce.strip() or len(nonce) > 128:
                return 400, {"ok": False, "reason": "INVALID_REFERENCE_NONCE"}
            try:
                consumed = self._consume_reference_nonce(nonce)
            except Exception:
                self.safety.disarm("REFERENCE_NONCE_PERSISTENCE_FAILED")
                return 409, {
                    "ok": False,
                    "reason": "REFERENCE_NONCE_PERSISTENCE_FAILED",
                }
            if not consumed:
                return 409, {"ok": False, "reason": "REFERENCE_NONCE_REPLAYED"}
            try:
                status = dict(
                    self.imu.restore_reference_state(
                        payload,
                        now=now or datetime.now(timezone.utc),
                        max_age_s=300.0,
                    )
                )
            except Exception as error:
                self.safety.disarm("IMU_REFERENCE_RESTORE_FAILED")
                return 409, {
                    "ok": False,
                    "reason": "IMU_REFERENCE_RESTORE_FAILED",
                    "detail": str(error),
                }
        return 200, status

    def imu_reset(self, token: str) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if self.imu is None:
            return 409, {"ok": False, "reason": "IMU_UNAVAILABLE"}
        if self.safety.armed:
            return 409, {"ok": False, "reason": "DISARM_BEFORE_IMU_RESET"}
        return 200, dict(self.imu.reset())

    def arm(self, token: str) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        try:
            self.safety.arm()
        except RuntimeError as error:
            return 409, {"armed": False, "reason": str(error)}
        return 200, {"armed": True, "reason": "ARMED"}

    def disarm(self, token: str) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        reason = self.safety.disarm()
        return 200, {"armed": False, "reason": reason}

    def heartbeat(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        self.safety.heartbeat(now=now)
        return 200, {"ok": True, "armed": self.safety.armed}

    def command(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            return self._unauthorized()
        if payload.get("action") != "STOP":
            if rejection := self._active_operation_rejection():
                return rejection
        current = now or datetime.now(timezone.utc)
        try:
            command = self._parse_command(payload)
        except (KeyError, TypeError, ValueError):
            self.safety.disarm("MALFORMED_COMMAND")
            return 400, {
                "commandId": str(payload.get("commandId", "unknown")),
                "sequence": int(payload.get("sequence", -1))
                if isinstance(payload.get("sequence", -1), int)
                else -1,
                "accepted": False,
                "reason": "MALFORMED_COMMAND",
                "appliedLeftSpeed": 0.0,
                "appliedRightSpeed": 0.0,
                "timestamp": current.isoformat(),
            }
        with self.safety.safety_lock:
            if command.action.startswith("V2_"):
                if self.imu is None:
                    self.safety.disarm("V2_IMU_UNAVAILABLE")
                    return 409, self._rejected_command(
                        command, current, "V2_IMU_UNAVAILABLE"
                    )
                imu_phase = str(self.imu.status().get("phase", "UNAVAILABLE"))
                if command.action in {"V2_PIVOT_LEFT", "V2_PIVOT_RIGHT"}:
                    if imu_phase != "READY":
                        self.safety.disarm("V2_PIVOT_INTERLOCK")
                        return 409, self._rejected_command(
                            command, current, "V2_PIVOT_INTERLOCK"
                        )
                elif command.action == "V2_PITCH_ADVANCE":
                    if imu_phase in {"PITCH_LIMIT_REACHED", "ROLL_LIMIT_REACHED"}:
                        command = MotorCommand(
                            command_id=command.command_id,
                            mission_id=command.mission_id,
                            sequence=command.sequence,
                            action="STOP",
                            left_speed=0.0,
                            right_speed=0.0,
                            issued_at=command.issued_at,
                            expires_at=command.expires_at,
                        )
                    elif imu_phase != "PITCH_GUARD":
                        self.safety.disarm("V2_PITCH_GUARD_INTERLOCK")
                        return 409, self._rejected_command(
                            command,
                            current,
                            "V2_PITCH_GUARD_INTERLOCK",
                        )
            ack = self.safety.apply(command, now=current)
        return (200 if ack.accepted else 409), self._ack_dict(ack)

    def stop(self, token: str, *, sequence: int = 0) -> tuple[int, dict[str, object]]:
        if not self._authorized(token):
            self.safety.disarm("UNAUTHORIZED")
            return self._unauthorized()
        now = datetime.now(timezone.utc)
        command = MotorCommand(
            command_id=f"stop-{sequence}",
            mission_id="safety-stop",
            sequence=sequence,
            action="STOP",
            left_speed=0.0,
            right_speed=0.0,
            issued_at=now,
            expires_at=now,
        )
        return 200, self._ack_dict(self.safety.apply(command, now=now))

    def set_emergency_stop(self, active: bool) -> None:
        self.safety.set_emergency_stop(active)

    def watchdog_tick(self, *, now: datetime | None = None) -> str | None:
        return self.safety.watchdog_tick(now=now)

    def _authorized(self, token: str) -> bool:
        return compare_digest(token, self._token)

    def _valid_reference_auth(self, payload: dict[str, Any]) -> bool:
        auth_tag = payload.get("authTag")
        if not isinstance(auth_tag, str):
            return False
        unsigned = {key: value for key, value in payload.items() if key != "authTag"}
        try:
            canonical = json.dumps(
                unsigned, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            return False
        expected = hmac_new(
            self._token.encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()
        return compare_digest(auth_tag, expected)

    def _consume_reference_nonce(self, nonce: str) -> bool:
        consumed = self._read_reference_nonces()
        if nonce in consumed:
            return False
        consumed = consumed[-(self.MAX_CONSUMED_REFERENCE_NONCES - 1) :] + [nonce]
        self._write_reference_nonces(consumed)
        return True

    def _read_reference_nonces(self) -> list[str]:
        try:
            descriptor = os.open(
                self._reference_nonce_store, os.O_RDONLY | os.O_NOFOLLOW
            )
        except FileNotFoundError:
            return []
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("reference nonce store is not a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise RuntimeError("reference nonce store mode is not 0600")
            raw = handle.read(262_145)
        if len(raw) > 262_144:
            raise RuntimeError("reference nonce store is too large")
        values = json.loads(raw)
        if (
            not isinstance(values, list)
            or len(values) > self.MAX_CONSUMED_REFERENCE_NONCES
            or not all(
                isinstance(value, str) and value.strip() and len(value) <= 128
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            raise RuntimeError("reference nonce store is invalid")
        return values

    def _write_reference_nonces(self, consumed: list[str]) -> None:
        parent = self._reference_nonce_store.parent
        descriptor, temporary_name = tempfile.mkstemp(
            dir=parent, prefix=f".{self._reference_nonce_store.name}.", suffix=".tmp"
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(consumed, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self._reference_nonce_store)
            temporary_name = ""
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _unauthorized() -> tuple[int, dict[str, object]]:
        return 401, {"ok": False, "reason": "UNAUTHORIZED"}

    @staticmethod
    def _parse_command(payload: dict[str, Any]) -> MotorCommand:
        issued_at = datetime.fromisoformat(str(payload["issuedAt"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00"))
        if issued_at.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError("timestamps must include timezone")
        return MotorCommand(
            command_id=str(payload["commandId"]),
            mission_id=str(payload["missionId"]),
            sequence=int(payload["sequence"]),
            action=str(payload["action"]),
            left_speed=float(payload["leftSpeed"]),
            right_speed=float(payload["rightSpeed"]),
            issued_at=issued_at,
            expires_at=expires_at,
        )

    @staticmethod
    def _ack_dict(ack: CommandAck) -> dict[str, object]:
        return {
            "commandId": ack.command_id,
            "sequence": ack.sequence,
            "accepted": ack.accepted,
            "reason": ack.reason,
            "appliedLeftSpeed": ack.applied_left_speed,
            "appliedRightSpeed": ack.applied_right_speed,
            "timestamp": ack.timestamp.isoformat(),
        }

    @staticmethod
    def _rejected_command(
        command: MotorCommand,
        now: datetime,
        reason: str,
    ) -> dict[str, object]:
        return {
            "commandId": command.command_id,
            "sequence": command.sequence,
            "accepted": False,
            "reason": reason,
            "appliedLeftSpeed": 0.0,
            "appliedRightSpeed": 0.0,
            "timestamp": now.isoformat(),
        }
