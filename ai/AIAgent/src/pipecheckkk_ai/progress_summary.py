from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from pipecheckkk_ai.models import (
    MissionState,
    ProgressAnnouncement,
    ProgressEventType,
    utc_now,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_PERIODIC_PROGRESS_INTERVAL_S = 10.0


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    event_type: ProgressEventType
    facts: dict[str, object]


class ProgressAnnouncementProvider(Protocol):
    def generate_progress_announcement(
        self,
        *,
        event_type: ProgressEventType,
        mission_id: str | None,
        mission_state: MissionState,
        facts: dict[str, object],
    ) -> ProgressAnnouncement: ...


AnnouncementPublisher = Callable[[ProgressAnnouncement], Awaitable[None]]


@dataclass(slots=True)
class PeriodicProgressTrigger:
    interval_s: float = DEFAULT_PERIODIC_PROGRESS_INTERVAL_S
    _last_requested_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.interval_s <= 0:
            raise ValueError("interval_s must be positive")

    def due(self, *, now: float, active: bool) -> bool:
        if not active:
            self._last_requested_at = None
            return False
        if (
            self._last_requested_at is not None
            and now - self._last_requested_at < self.interval_s
        ):
            return False
        self._last_requested_at = now
        return True


class ProgressAnnouncementService:
    def __init__(
        self,
        *,
        provider: ProgressAnnouncementProvider,
        publisher: AnnouncementPublisher,
    ) -> None:
        self.provider = provider
        self.publisher = publisher
        self.current = ProgressAnnouncement()
        self._lock = asyncio.Lock()
        self._epoch = 0
        self._revision = 0

    @property
    def generation_token(self) -> int:
        return self._epoch

    async def reset(self) -> ProgressAnnouncement:
        return await self.invalidate(
            mission_id=None,
            mission_state=MissionState.IDLE,
        )

    async def invalidate(
        self,
        *,
        mission_id: str | None,
        mission_state: MissionState,
    ) -> ProgressAnnouncement:
        self._epoch += 1
        self._revision += 1
        self.current = ProgressAnnouncement(
            revision=self._revision,
            mission_id=mission_id,
            mission_state=mission_state,
        )
        await self.publisher(self.current)
        return self.current

    async def generate(
        self,
        event: ProgressEvent,
        *,
        mission_id: str | None,
        mission_state: MissionState,
        generation_token: int | None = None,
        publish_pending: bool = False,
        preserve_on_error: bool = False,
    ) -> ProgressAnnouncement:
        epoch = self._epoch if generation_token is None else generation_token
        async with self._lock:
            if epoch != self._epoch:
                return self.current
            previous = self.current
            if publish_pending:
                self._revision += 1
                pending = ProgressAnnouncement(
                    revision=self._revision,
                    mission_id=mission_id,
                    mission_state=mission_state,
                    event_type=event.event_type,
                    generated_at=utc_now(),
                    status="pending",
                )
                self.current = pending
                await self.publisher(pending)
            try:
                announcement = await asyncio.to_thread(
                    self.provider.generate_progress_announcement,
                    event_type=event.event_type,
                    mission_id=mission_id,
                    mission_state=mission_state,
                    facts=event.facts,
                )
            except Exception:
                LOGGER.exception("Qwen progress announcement generation failed")
                if preserve_on_error:
                    if epoch == self._epoch:
                        self.current = previous
                    return self.current
                announcement = ProgressAnnouncement(
                    mission_id=mission_id,
                    mission_state=mission_state,
                    event_type=event.event_type,
                    generated_at=utc_now(),
                    status="error",
                )
            if epoch != self._epoch:
                return self.current
            self._revision += 1
            announcement = announcement.model_copy(update={"revision": self._revision})
            self.current = announcement
            await self.publisher(announcement)
            return announcement

    async def publish_stage_message(
        self,
        *,
        message: str,
        mission_id: str,
        mission_state: MissionState = MissionState.RUNNING,
    ) -> ProgressAnnouncement:
        """Immediately publish a display fallback and supersede older Qwen work."""
        self._epoch += 1
        self._revision += 1
        announcement = ProgressAnnouncement(
            revision=self._revision,
            mission_id=mission_id,
            mission_state=mission_state,
            message=message,
            generated_at=utc_now(),
            status="ready",
        )
        self.current = announcement
        await self.publisher(announcement)
        return announcement


class ProgressEventDetector:
    def __init__(
        self,
        *,
        obstacle_threshold: float = 0.60,
        clear_threshold: float = 0.35,
        clear_frames_required: int = 3,
    ) -> None:
        self.obstacle_threshold = obstacle_threshold
        self.clear_threshold = clear_threshold
        self.clear_frames_required = clear_frames_required
        self._obstacle_active = False
        self._clear_frames = 0
        self._exploration_unavailable_emitted = False

    def reset(self) -> None:
        self._obstacle_active = False
        self._clear_frames = 0
        self._exploration_unavailable_emitted = False

    def observe_drive(
        self,
        *,
        path_blocked_probability: float,
        safe_for_control: bool,
        plan_action: str,
        steering: float,
    ) -> ProgressEvent | None:
        if path_blocked_probability >= self.obstacle_threshold:
            self._clear_frames = 0
            if self._obstacle_active:
                return None
            self._obstacle_active = True
            direction = "RIGHT" if steering > 0 else "LEFT" if steering < 0 else "UNKNOWN"
            return ProgressEvent(
                event_type=ProgressEventType.OBSTACLE_DETECTED,
                facts={
                    "obstacleDetected": True,
                    "pathBlockedProbability": path_blocked_probability,
                    "avoidanceDirection": direction,
                },
            )

        can_count_as_clear = (
            self._obstacle_active
            and path_blocked_probability <= self.clear_threshold
            and safe_for_control
            and plan_action != "STOP"
        )
        if not can_count_as_clear:
            self._clear_frames = 0
            return None

        self._clear_frames += 1
        if self._clear_frames < self.clear_frames_required:
            return None
        self._obstacle_active = False
        self._clear_frames = 0
        return ProgressEvent(
            event_type=ProgressEventType.OBSTACLE_AVOIDANCE_COMPLETED,
            facts={"obstacleAvoidanceCompleted": True},
        )

    def observe_mission(
        self,
        *,
        mission_state: MissionState,
        reason: str,
    ) -> ProgressEvent | None:
        if mission_state is not MissionState.FAILED_BLOCKED:
            return None
        if self._exploration_unavailable_emitted:
            return None
        self._exploration_unavailable_emitted = True
        return ProgressEvent(
            event_type=ProgressEventType.EXPLORATION_UNAVAILABLE,
            facts={"explorationUnavailable": True, "reason": reason},
        )

    def report_defect(
        self,
        *,
        defect_type: str,
        confidence: float,
        location_m: float,
    ) -> ProgressEvent:
        return ProgressEvent(
            event_type=ProgressEventType.DEFECT_DETECTED,
            facts={
                "defectType": defect_type,
                "confidence": confidence,
                "locationM": location_m,
            },
        )
