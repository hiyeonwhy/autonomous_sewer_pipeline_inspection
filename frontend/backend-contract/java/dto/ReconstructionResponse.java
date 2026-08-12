package com.ssafy.robotinspection.api.dto;

import java.time.OffsetDateTime;

public record ReconstructionResponse(
    Long id,
    Long sessionId,
    String status,
    String stage,
    Integer progressPercent,
    Integer imageCount,
    String modelPath,
    String thumbnailPath,
    OffsetDateTime startedAt,
    OffsetDateTime completedAt,
    String errorMessage
) {}

