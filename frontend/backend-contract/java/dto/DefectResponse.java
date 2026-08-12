package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.DefectType;
import com.ssafy.robotinspection.api.enums.RepairStatus;
import com.ssafy.robotinspection.api.enums.Severity;
import java.time.OffsetDateTime;

public record DefectResponse(
    Long id,
    Long sessionId,
    DefectType defectType,
    Severity severity,
    Double confidence,
    Double blockageRatio,
    Double approxPositionM,
    String snapshotPath,
    RepairStatus repairStatus,
    String actionType,
    OffsetDateTime detectedAt
) {}

