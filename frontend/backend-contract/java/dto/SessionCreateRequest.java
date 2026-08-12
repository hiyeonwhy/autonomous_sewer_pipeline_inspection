package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.SessionMode;

public record SessionCreateRequest(
    Long districtId,
    Long priorityScoreId,
    Long robotId,
    Long approvedBy,
    SessionMode mode
) {}

