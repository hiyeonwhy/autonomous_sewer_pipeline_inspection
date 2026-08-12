package com.ssafy.robotinspection.api.dto;

import java.time.LocalDate;
import java.time.OffsetDateTime;

public record ReportResponse(
    Long id,
    Long sessionId,
    String region,
    String robotName,
    String robotModel,
    String operatorName,
    String operatorRole,
    LocalDate inspectionDate,
    String duration,
    Integer totalDefectCount,
    Integer criticalDefectCount,
    String summary,
    OffsetDateTime generatedAt
) {}

