from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pipecheckkk_ai.models import (
    CommandBridgeStatus,
    DriveCommand,
    EdgeCommandAck,
)


class EdgeClient(Protocol):
    def health(self) -> dict[str, object]: ...

    def arm(self) -> dict[str, object]: ...

    def heartbeat(self) -> dict[str, object]: ...

    def command(self, payload: dict[str, object]) -> dict[str, object]: ...

    def stop(self, sequence: int) -> dict[str, object]: ...

    def disarm(self) -> dict[str, object]: ...

    def imu_calibrate(self, samples: int = 100) -> dict[str, object]: ...

    def imu_start_yaw_step(self, direction: str) -> dict[str, object]: ...

    def imu_start_pitch_guard(self) -> dict[str, object]: ...

    def imu_start_recovery(self) -> dict[str, object]: ...

    def imu_reset(self) -> dict[str, object]: ...


class CommandBridge:
    def __init__(self, edge: EdgeClient) -> None:
        self.edge = edge
        self.status = CommandBridgeStatus()
        self.latest_ack: EdgeCommandAck | None = None
        self._mission_id: str | None = None
        self._sequence = 0
        self._last_heartbeat_at: datetime | None = None

    def arm(self, mission_id: str) -> CommandBridgeStatus:
        health = self.edge.health()
        if not bool(health.get("ok")):
            raise RuntimeError("Edge health check failed")
        if bool(health.get("emergencyStop")):
            raise RuntimeError("Edge emergency stop is active")
        front_distance = health.get("frontDistanceM")
        stop_distance = health.get("ultrasonicStopM", 0.05)
        if not isinstance(stop_distance, (int, float)):
            raise RuntimeError("Edge ultrasonic stop threshold is malformed")
        if front_distance is not None:
            if not isinstance(front_distance, (int, float)):
                raise RuntimeError("Edge ultrasonic clearance is malformed")
            if float(stop_distance) > 0 and float(front_distance) <= float(stop_distance):
                raise RuntimeError("Edge ultrasonic clearance is blocked")
        arm_response = self.edge.arm()
        if not bool(arm_response.get("armed")):
            raise RuntimeError(f"Edge arm rejected: {arm_response.get('reason', 'unknown')}")
        heartbeat = self.edge.heartbeat()
        if not bool(heartbeat.get("ok")):
            self.edge.disarm()
            raise RuntimeError("Initial edge heartbeat failed")

        self._mission_id = mission_id
        self._sequence = 0
        self._last_heartbeat_at = datetime.now(timezone.utc)
        self.status = CommandBridgeStatus(
            connected=True,
            armed=True,
            emergency_stop=False,
            mission_id=mission_id,
            latest_sequence=0,
            heartbeat_age_ms=0.0,
            reason="ARMED",
        )
        return self.status.model_copy(deep=True)

    def heartbeat(self) -> CommandBridgeStatus:
        if not self.status.armed:
            raise RuntimeError("Edge is not armed")
        response = self.edge.heartbeat()
        if not bool(response.get("ok")) or not bool(response.get("armed")):
            self._fail_closed("HEARTBEAT_REJECTED")
            raise RuntimeError("Edge heartbeat was rejected")
        self._last_heartbeat_at = datetime.now(timezone.utc)
        self.status.heartbeat_age_ms = 0.0
        return self.status.model_copy(deep=True)

    def health(self) -> dict[str, object]:
        return self.edge.health()

    def calibrate_imu(self, samples: int = 100) -> dict[str, object]:
        if self.status.armed:
            raise RuntimeError("disarm Edge before IMU calibration")
        response = self.edge.imu_calibrate(samples)
        if not bool(response.get("calibrated")) or response.get("phase") != "READY":
            raise RuntimeError(f"Edge IMU calibration rejected: {response.get('reason', 'unknown')}")
        return response

    def recalibrate_imu_after_recovery(self, samples: int = 100) -> dict[str, object]:
        if not self.status.armed:
            raise RuntimeError("Edge must remain armed for V2 recovery cycle resume")
        health = self.edge.health()
        imu = health.get("imu")
        if not bool(health.get("armed")) or not isinstance(imu, dict):
            raise RuntimeError("Edge is not safely armed for V2 recovery cycle resume")
        if imu.get("phase") != "COMPLETED":
            raise RuntimeError("V2 recovery cycle is not completed")
        response = self.edge.imu_calibrate(samples)
        if not bool(response.get("calibrated")) or response.get("phase") != "READY":
            self._fail_closed("V2_RECOVERY_RECALIBRATION_REJECTED")
            raise RuntimeError(
                f"Edge V2 recovery recalibration rejected: {response.get('reason', 'unknown')}"
            )
        self.status.reason = "V2_RECOVERY_CYCLE_READY"
        return response

    def start_imu_yaw_step(self, direction: str) -> dict[str, object]:
        if not self.status.armed:
            raise RuntimeError("Edge is not armed")
        if direction not in {"LEFT", "RIGHT"}:
            raise ValueError("V2 yaw direction must be LEFT or RIGHT")
        response = self.edge.imu_start_yaw_step(direction)
        if response.get("phase") != "PIVOT_YAW":
            raise RuntimeError(f"Edge yaw step rejected: {response.get('reason', 'unknown')}")
        return response

    def start_imu_pitch_guard(self) -> dict[str, object]:
        if not self.status.armed:
            raise RuntimeError("Edge is not armed")
        response = self.edge.imu_start_pitch_guard()
        if response.get("phase") != "PITCH_GUARD":
            raise RuntimeError(f"Edge pitch guard rejected: {response.get('reason', 'unknown')}")
        return response

    def start_imu_recovery(self) -> dict[str, object]:
        if not self.status.armed:
            raise RuntimeError("Edge is not armed")
        response = self.edge.imu_start_recovery()
        if response.get("phase") != "PRIMARY_YAW":
            raise RuntimeError(f"Edge IMU recovery rejected: {response.get('reason', 'unknown')}")
        return response

    def reset_imu(self) -> dict[str, object]:
        if self.status.armed:
            raise RuntimeError("disarm Edge before IMU reset")
        return self.edge.imu_reset()

    def dispatch(self, command: DriveCommand) -> EdgeCommandAck:
        if not self.status.armed or self._mission_id is None:
            raise RuntimeError("Edge is not armed")
        if command.action != "STOP" and not command.safety_approved:
            self._send_stop()
            raise RuntimeError("Drive command is not safety-approved")

        self._sequence += 1
        payload: dict[str, object] = {
            "commandId": command.command_id,
            "missionId": self._mission_id,
            "sequence": self._sequence,
            "action": command.action,
            "leftSpeed": min(command.left_speed, 0.90),
            "rightSpeed": min(command.right_speed, 0.90),
            "issuedAt": command.issued_at.isoformat(),
            "expiresAt": command.expires_at.isoformat(),
        }
        response = self.edge.command(payload)
        try:
            ack = EdgeCommandAck.model_validate(response)
        except Exception as error:
            self._fail_closed("INVALID_ACK")
            raise RuntimeError("Edge ACK is invalid") from error
        if ack.command_id != command.command_id or ack.sequence != self._sequence:
            self._fail_closed("ACK_MISMATCH")
            raise RuntimeError("Edge ACK mismatch")
        if not ack.accepted:
            self._fail_closed(ack.reason)
            raise RuntimeError(f"Edge rejected command: {ack.reason}")

        self.latest_ack = ack
        self.status.latest_sequence = self._sequence
        self.status.reason = ack.reason
        return ack

    def stop_and_disarm(self, reason: str = "SERVER_STOP") -> CommandBridgeStatus:
        if self.status.armed:
            self._send_stop()
        self.edge.disarm()
        self._mission_id = None
        self.status.armed = False
        self.status.mission_id = None
        self.status.reason = reason
        return self.status.model_copy(deep=True)

    def reset_session(self) -> CommandBridgeStatus:
        if self.status.armed:
            raise RuntimeError("disarm Edge before session reset")
        self._mission_id = None
        self._sequence = 0
        self._last_heartbeat_at = None
        self.latest_ack = None
        self.status = CommandBridgeStatus(reason="SESSION_RESET")
        return self.status.model_copy(deep=True)

    def hold_stop(self, reason: str = "TRANSIENT_PERCEPTION_FAILURE") -> CommandBridgeStatus:
        if not self.status.armed:
            raise RuntimeError("Edge is not armed")
        self._send_stop()
        self.status.reason = reason
        return self.status.model_copy(deep=True)

    def _send_stop(self) -> None:
        self._sequence += 1
        self.edge.stop(self._sequence)
        self.status.latest_sequence = self._sequence

    def _fail_closed(self, reason: str) -> None:
        try:
            self._send_stop()
        finally:
            self.edge.disarm()
            self.status.armed = False
            self.status.reason = reason
