package com.ssafy.robotinspection.api.dto;

import java.time.OffsetDateTime;
import java.util.Map;

public record DriveLogResponse(
    Long id,
    Long sessionId,
    Double ultrasonicDistance,
    Map<String, Double> imuReading,
    String command,
    String commandSource,
    String agentState,
    String note,
    OffsetDateTime loggedAt
) {}

