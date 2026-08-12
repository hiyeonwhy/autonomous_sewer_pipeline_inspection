"""Visual stall detection for robot motion verification."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image

LOGGER = logging.getLogger(__name__)


@dataclass
class StallState:
    """Current stall detection state."""

    is_moving: bool  # True if robot is detected moving
    stuck_retry_count: int = 0  # Number of STUCK detections in sequence
    max_stuck_retries: int = 2  # Maximum STUCK retries before disarm


def default_frame_comparator(
    pre_command_frame: bytes,
    post_command_frame: bytes,
    threshold: float = 5.0,
) -> bool:
    """
    Perceptual frame comparison using downscaled grayscale MAD.

    Args:
        pre_command_frame: Image bytes before accepted motion command
        post_command_frame: Image bytes after accepted motion command
        threshold: Mean absolute difference threshold (0-255 scale)

    Returns:
        True if frames differ significantly (motion detected), False if stalled
    """
    try:
        # Decode both frames
        pre_img = Image.open(io.BytesIO(pre_command_frame)).convert("L")  # Grayscale
        post_img = Image.open(io.BytesIO(post_command_frame)).convert("L")

        # Downscale to 40x30 for faster comparison and noise reduction
        scale = (40, 30)
        pre_small = pre_img.resize(scale, Image.Resampling.LANCZOS)
        post_small = post_img.resize(scale, Image.Resampling.LANCZOS)

        # Convert to numpy arrays
        pre_arr = np.asarray(pre_small, dtype=np.float32)
        post_arr = np.asarray(post_small, dtype=np.float32)

        # Compute mean absolute difference
        mad = np.mean(np.abs(pre_arr - post_arr))

        # Motion detected if MAD exceeds threshold
        return bool(mad >= threshold)

    except Exception as error:
        LOGGER.error(f"Frame comparison failed: {error}")
        # Fail closed: an unreadable comparison cannot prove that the robot moved.
        return False


class VisualStallDetector:
    """Detect robot visual stall (no motion despite accepted command)."""

    def __init__(
        self,
        comparator: Callable[[bytes, bytes, float], bool] | None = None,
        threshold: float = 5.0,
    ):
        """
        Initialize detector.

        Args:
            comparator: Custom frame comparison function, defaults to MAD
            threshold: Motion detection threshold (0-255 scale for default)
        """
        self.comparator = comparator or default_frame_comparator
        self.threshold = threshold
        self._pre_command_frame: bytes | None = None
        self.state = StallState(is_moving=True)

    def record_pre_command_frame(self, frame_bytes: bytes) -> None:
        """
        Record frame immediately before accepting motion command.

        Args:
            frame_bytes: Raw frame image bytes
        """
        self._pre_command_frame = frame_bytes

    def check_post_command_motion(self, frame_bytes: bytes) -> bool:
        """
        Check if motion occurred after accepted command.

        Must be called after record_pre_command_frame and before reset.

        Args:
            frame_bytes: Raw frame image bytes after command execution

        Returns:
            True if significant visual motion detected, False if stalled
        """
        if self._pre_command_frame is None:
            # No pre-command baseline, assume moving to be safe
            return True

        try:
            motion_detected = self.comparator(
                self._pre_command_frame,
                frame_bytes,
                self.threshold,
            )
            self.state.is_moving = motion_detected
            return motion_detected
        except Exception as error:
            LOGGER.error(f"Motion check failed: {error}")
            # Fail closed: an errored check cannot prove motion.
            return False

    def reset(self) -> None:
        """Reset detector state for new mission."""
        self._pre_command_frame = None
        self.state = StallState(is_moving=True)

    def is_stuck(self) -> bool:
        """Check if robot is stuck (accepted command but no visual motion)."""
        return not self.state.is_moving and self.state.stuck_retry_count > 0

    def increment_stuck_count(self) -> int:
        """Increment stuck detection count, return new count."""
        self.state.stuck_retry_count += 1
        return self.state.stuck_retry_count

    def should_disarm_on_stuck(self) -> bool:
        """Check if stuck retry limit reached and should disarm."""
        return self.state.stuck_retry_count > self.state.max_stuck_retries

    def reset_stuck_count(self) -> None:
        """Reset stuck count after successful motion."""
        self.state.stuck_retry_count = 0
        self.state.is_moving = True
        self._pre_command_frame = None
