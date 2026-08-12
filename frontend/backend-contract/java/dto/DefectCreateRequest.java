package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.DefectType;
import com.ssafy.robotinspection.api.enums.Severity;
import java.time.OffsetDateTime;

public record DefectCreateRequest(
    DefectType defectType,
    Severity severity,
    Double confidence,
    Double blockageRatio,
    Double approxPositionM,
    String snapshotPath,
    OffsetDateTime detectedAt
) {}

