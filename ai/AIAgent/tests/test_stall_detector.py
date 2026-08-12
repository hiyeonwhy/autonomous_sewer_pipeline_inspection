"""Tests for visual stall detector with perceptual frame comparison."""
from __future__ import annotations

import io
from unittest.mock import Mock

from PIL import Image

from pipecheckkk_ai.vision.stall_detector import (
    VisualStallDetector,
    default_frame_comparator,
)


def create_test_jpeg(color: int = 128) -> bytes:
    """Create a simple test JPEG with uniform color."""
    img = Image.new("L", (320, 240), color)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


def create_shifted_jpeg(offset: int = 20) -> bytes:
    """Create a JPEG with shifted brightness."""
    img = Image.new("L", (320, 240), 128 + offset)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


class TestDefaultFrameComparator:
    """Test default MAD-based frame comparison."""

    def test_identical_frames_no_motion(self) -> None:
        """Identical decoded frames produce no motion."""
        frame = create_test_jpeg(color=128)
        motion = default_frame_comparator(frame, frame, threshold=5.0)
        assert motion is False

    def test_significant_shift_detects_motion(self) -> None:
        """Large brightness shift is detected as motion."""
        frame1 = create_test_jpeg(color=50)
        frame2 = create_test_jpeg(color=200)
        motion = default_frame_comparator(frame1, frame2, threshold=5.0)
        assert motion is True

    def test_tiny_noise_below_threshold(self) -> None:
        """Tiny encoding noise below threshold is not motion."""
        # Same color but potentially different JPEG encoding noise
        frame1 = create_test_jpeg(color=128)
        frame2 = create_test_jpeg(color=128)
        # Very tight threshold to detect real changes only
        motion = default_frame_comparator(frame1, frame2, threshold=1.0)
        assert motion is False

    def test_small_change_above_threshold(self) -> None:
        """Small but meaningful change above threshold is motion."""
        frame1 = create_test_jpeg(color=128)
        frame2 = create_shifted_jpeg(offset=15)  # 15-point shift
        motion = default_frame_comparator(frame1, frame2, threshold=5.0)
        assert motion is True

    def test_custom_threshold(self) -> None:
        """Custom threshold adjusts sensitivity."""
        frame1 = create_test_jpeg(color=128)
        frame2 = create_shifted_jpeg(offset=3)  # Small shift

        # High threshold: no motion
        motion_high = default_frame_comparator(frame1, frame2, threshold=10.0)
        assert motion_high is False

        # Low threshold: motion detected
        motion_low = default_frame_comparator(frame1, frame2, threshold=1.0)
        assert motion_low is True

    def test_invalid_frame_data_fails_closed_as_stalled(self) -> None:
        """Invalid image data cannot prove motion and therefore fails closed."""
        bad_frame = b"not an image"
        motion = default_frame_comparator(bad_frame, bad_frame, threshold=5.0)
        assert motion is False


class TestVisualStallDetectorSemantics:
    """Test stall detector semantics: pre/post frame comparison."""

    def test_no_pre_command_frame_returns_moving(self) -> None:
        """Without pre-command baseline, assume moving."""
        detector = VisualStallDetector()
        frame = create_test_jpeg()

        # Check motion without recording pre-command frame
        motion = detector.check_post_command_motion(frame)
        assert motion is True

    def test_unchanged_post_command_frame_detects_stall(self) -> None:
        """Unchanged post-command frame triggers STUCK."""
        detector = VisualStallDetector(threshold=5.0)
        frame = create_test_jpeg(color=128)

        # Record pre-command
        detector.record_pre_command_frame(frame)

        # Same frame after command = no motion = stalled
        motion = detector.check_post_command_motion(frame)
        assert motion is False
        assert detector.state.is_moving is False

    def test_changed_post_command_frame_detects_motion(self) -> None:
        """Changed post-command frame indicates motion."""
        detector = VisualStallDetector(threshold=5.0)
        pre_frame = create_test_jpeg(color=100)
        post_frame = create_test_jpeg(color=180)

        detector.record_pre_command_frame(pre_frame)
        motion = detector.check_post_command_motion(post_frame)

        assert motion is True
        assert detector.state.is_moving is True

    def test_stuck_count_only_increments_on_stall(self) -> None:
        """Stuck count increments only when motion=False."""
        detector = VisualStallDetector(threshold=5.0)
        frame = create_test_jpeg()

        # Record pre-command
        detector.record_pre_command_frame(frame)

        # No motion (stalled)
        motion = detector.check_post_command_motion(frame)
        assert motion is False

        # Increment stuck count
        count = detector.increment_stuck_count()
        assert count == 1
        assert detector.is_stuck() is True

    def test_motion_resets_stuck_count(self) -> None:
        """Successful motion resets stuck count."""
        detector = VisualStallDetector(threshold=5.0)

        # Simulate two stuck detections
        detector.increment_stuck_count()
        detector.increment_stuck_count()
        assert detector.state.stuck_retry_count == 2

        # Reset on motion
        detector.reset_stuck_count()
        assert detector.state.stuck_retry_count == 0
        assert detector.is_stuck() is False

    def test_stuck_retry_limit_triggers_disarm(self) -> None:
        """Exceeding stuck retry limit triggers disarm."""
        detector = VisualStallDetector()
        detector.state.max_stuck_retries = 2

        detector.increment_stuck_count()  # 1
        assert detector.should_disarm_on_stuck() is False

        detector.increment_stuck_count()  # 2
        assert detector.should_disarm_on_stuck() is False

        detector.increment_stuck_count()  # 3 - exceeds max
        assert detector.should_disarm_on_stuck() is True

    def test_reset_clears_pre_command_frame(self) -> None:
        """Reset clears pre-command baseline."""
        detector = VisualStallDetector()
        frame = create_test_jpeg()

        detector.record_pre_command_frame(frame)
        assert detector._pre_command_frame is not None

        detector.reset()
        assert detector._pre_command_frame is None

    def test_custom_comparator_injectable(self) -> None:
        """Custom frame comparator can be injected."""
        mock_comparator = Mock(return_value=True)

        detector = VisualStallDetector(comparator=mock_comparator, threshold=5.0)
        pre_frame = create_test_jpeg()
        post_frame = create_test_jpeg()

        detector.record_pre_command_frame(pre_frame)
        motion = detector.check_post_command_motion(post_frame)

        assert motion is True
        mock_comparator.assert_called_once_with(pre_frame, post_frame, 5.0)

    def test_noop_command_does_not_record_frame(self) -> None:
        """NOOP commands have no pre-frame to compare (not tested here)."""
        # Semantics: NOOP should not call record_pre_command_frame
        detector = VisualStallDetector()
        frame = create_test_jpeg()

        # Never recorded a pre-frame
        motion = detector.check_post_command_motion(frame)
        # Should assume moving (no stall)
        assert motion is True
