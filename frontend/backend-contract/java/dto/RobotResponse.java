package com.ssafy.robotinspection.api.dto;

public record RobotResponse(
    Long id,
    String name,
    String model,
    String status
) {}

