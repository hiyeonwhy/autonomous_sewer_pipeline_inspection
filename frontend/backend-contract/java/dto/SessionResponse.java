package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.SessionMode;
import com.ssafy.robotinspection.api.enums.SessionStatus;
import java.time.OffsetDateTime;
import java.util.Map;

public record SessionResponse(
    Long id,
    Long districtId,
    Long priorityScoreId,
    Long robotId,
    Long approvedBy,
    SessionMode mode,
    SessionStatus status,
    OffsetDateTime startedAt,
    OffsetDateTime endedAt,
    Map<String, Object> defectSummary
) {}

