package com.ssafy.robotinspection.api.dto;

import com.ssafy.robotinspection.api.enums.RepairStatus;

public record DefectRepairUpdateRequest(
    RepairStatus repairStatus,
    String actionType
) {}

