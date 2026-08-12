from __future__ import annotations

from datetime import datetime, timezone
from threading import Event
from typing import Protocol

from edge.motor.local_safety import LocalSafetySupervisor


class DistanceSensor(Protocol):
    def measure(self) -> float | None: ...


class EdgeRuntime:
    def __init__(
        self,
        safety: LocalSafetySupervisor,
        sensor: DistanceSensor,
        *,
        poll_interval_s: float = 0.06,
    ) -> None:
        self.safety = safety
        self.sensor = sensor
        self.poll_interval_s = poll_interval_s
        self.last_reason = "STARTING"

    def poll_once(self, *, now: datetime | None = None) -> str | None:
        current = now or datetime.now(timezone.utc)
        distance = self.sensor.measure()
        self.safety.update_ultrasonic(distance)
        if distance is not None and distance <= self.safety.ultrasonic_stop_m:
            self.last_reason = "ULTRASONIC_HARD_STOP"
            return self.last_reason
        watchdog_reason = self.safety.watchdog_tick(now=current)
        if watchdog_reason is not None:
            self.last_reason = watchdog_reason
        else:
            self.last_reason = "READY"
        return watchdog_reason

    def run(self, stop_event: Event) -> None:
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                self.safety.disarm("RUNTIME_EXCEPTION")
                self.last_reason = "RUNTIME_EXCEPTION"
            stop_event.wait(self.poll_interval_s)

    def close(self) -> None:
        self.safety.close()
