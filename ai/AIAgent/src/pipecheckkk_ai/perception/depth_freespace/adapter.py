from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import numpy as np

from pipecheckkk_ai.models import PerceptionResult


class DepthFreeSpaceProvider:
    name = "depth-freespace"

    def __init__(
        self,
        *,
        depth_estimator: Callable[[np.ndarray], np.ndarray] | None = None,
        model_dir: Path | None = None,
    ) -> None:
        self._depth_estimator = depth_estimator
        self.model_dir = model_dir

    def load(self) -> None:
        if self._depth_estimator is not None:
            return
        if self.model_dir is None:
            raise RuntimeError("depth model directory is not configured")

        import cv2
        import torch
        import torch.nn.functional as functional
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        processor = AutoImageProcessor.from_pretrained(self.model_dir, local_files_only=True)
        model = AutoModelForDepthEstimation.from_pretrained(
            self.model_dir,
            local_files_only=True,
        ).to(device)
        model.eval()

        def estimate(frame: np.ndarray) -> np.ndarray:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            inputs = processor(images=rgb, return_tensors="pt")
            inputs = {name: tensor.to(device) for name, tensor in inputs.items()}
            with torch.inference_mode():
                predicted = model(**inputs).predicted_depth
            resized = functional.interpolate(
                predicted.unsqueeze(1),
                size=frame.shape[:2],
                mode="bicubic",
                align_corners=False,
            ).squeeze()
            return resized.detach().float().cpu().numpy()

        self._depth_estimator = estimate

    def analyze(self, image_bytes: bytes) -> PerceptionResult:
        import cv2

        self.load()
        assert self._depth_estimator is not None
        started = perf_counter()
        encoded = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("snapshot is not a decodable image")

        depth = np.asarray(self._depth_estimator(frame), dtype=np.float32)
        if depth.ndim != 2 or depth.size == 0 or not np.isfinite(depth).all():
            raise ValueError("depth estimator returned an invalid map")
        if depth.shape != frame.shape[:2]:
            depth = cv2.resize(depth, (frame.shape[1], frame.shape[0]))

        low, high = np.percentile(depth, (5.0, 95.0))
        scale = max(float(high - low), 1e-6)
        normalized_depth = np.clip((depth - low) / scale, 0.0, 1.0)
        closeness = normalized_depth

        height, width = closeness.shape
        roi = closeness[int(height * 0.28) : int(height * 0.95), :]
        left, center, right = np.array_split(roi, 3, axis=1)

        def free_score(zone: np.ndarray) -> float:
            near_score = float(np.percentile(zone, 65.0))
            near_occupancy = float(np.mean(zone >= 0.72))
            risk = 0.70 * near_occupancy + 0.30 * near_score
            return min(1.0, max(0.0, 1.0 - risk))

        free_space_profile = [
            free_score(sector)
            for sector in np.array_split(roi, 9, axis=1)
        ]
        left_free = free_score(left)
        center_free = free_score(center)
        right_free = free_score(right)
        blocked = 1.0 - center_free
        center_error = min(1.0, max(-1.0, right_free - left_free))

        return PerceptionResult(
            model_name=self.name,
            backend="relative-depth-freespace",
            center_error=center_error,
            confidence=0.85,
            latency_ms=(perf_counter() - started) * 1000.0,
            image_width=width,
            image_height=height,
            safe_for_control=True,
            path_blocked_probability=blocked,
            left_free_score=left_free,
            center_free_score=center_free,
            right_free_score=right_free,
            free_space_profile=free_space_profile,
            note="상대 깊이로 9방향 조향 후보와 좌·중앙·우 자유공간을 추정했습니다.",
        )