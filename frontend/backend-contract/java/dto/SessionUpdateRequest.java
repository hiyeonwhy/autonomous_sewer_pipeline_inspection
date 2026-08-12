package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.SessionStatus;
import java.time.OffsetDateTime;
import java.util.Map;

public record SessionUpdateRequest(
    SessionStatus status,
    OffsetDateTime endedAt,
    Map<String, Object> defectSummary
) {}

