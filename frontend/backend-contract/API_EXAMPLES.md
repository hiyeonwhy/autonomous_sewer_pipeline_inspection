# API 요청·응답 JSON 예시

기본 경로는 `/api`로 가정한다.

## 세션 생성

```http
POST /api/sessions
```

```json
{
  "district_id": 101,
  "priority_score_id": 1,
  "robot_id": 1,
  "approved_by": 1,
  "mode": "AUTO"
}
```

```json
{
  "id": 10,
  "district_id": 101,
  "priority_score_id": 1,
  "robot_id": 1,
  "approved_by": 1,
  "mode": "AUTO",
  "status": "RUNNING",
  "started_at": "2026-07-28T10:00:00+09:00",
  "ended_at": null,
  "defect_summary": {
    "total": 0,
    "critical": 0,
    "warning": 0,
    "normal": 0
  }
}
```

## 결함 등록

```http
POST /api/sessions/10/captures
```

```json
{
  "defect_type": "BLOCKAGE",
  "severity": "CRITICAL",
  "confidence": 0.94,
  "blockage_ratio": 0.72,
  "approx_position_m": 25.8,
  "snapshot_path": "/media/defects/blockage-01.jpg",
  "detected_at": "2026-07-28T10:30:00+09:00"
}
```

## 결함 목록 조회

```http
GET /api/sessions/10/defects
```

```json
[
  {
    "id": 31,
    "session_id": 10,
    "defect_type": "BLOCKAGE",
    "severity": "CRITICAL",
    "confidence": 0.94,
    "blockage_ratio": 0.72,
    "approx_position_m": 25.8,
    "snapshot_path": "/media/defects/blockage-01.jpg",
    "repair_status": "BEFORE",
    "action_type": "HIGH_PRESSURE_CLEANING",
    "detected_at": "2026-07-28T10:30:00+09:00"
  }
]
```

## 보수 상태 변경

```http
PATCH /api/defects/31
```

```json
{
  "repair_status": "IN_PROGRESS",
  "action_type": "HIGH_PRESSURE_CLEANING"
}
```

## 주행 로그 조회

```http
GET /api/sessions/10/drive-logs
```

```json
[
  {
    "id": 100,
    "session_id": 10,
    "ultrasonic_distance": 0.8,
    "imu_reading": {
      "roll": 0.1,
      "pitch": 0.2,
      "yaw": 1.4
    },
    "command": "FORWARD",
    "command_source": "AGENT",
    "agent_state": "DRIVING",
    "note": "정상 주행 중",
    "logged_at": "2026-07-28T10:29:30+09:00"
  }
]
```

## 보고서 목록 조회

```http
GET /api/reports
```

```json
[
  {
    "id": 7,
    "session_id": 10,
    "region": "점검 구역 101",
    "robot_name": "ROBO-01",
    "robot_model": "Explorer-V2",
    "operator_name": "김철수",
    "operator_role": "OPERATOR",
    "inspection_date": "2026-07-28",
    "duration": "00:45:00",
    "total_defect_count": 3,
    "critical_defect_count": 1,
    "summary": "균열 1건, 표면손상 1건, 막힘 1건",
    "generated_at": "2026-07-28T10:45:00+09:00"
  }
]
```

## 로봇 및 운영자 목록

```http
GET /api/robots
GET /api/operators
```

```json
{
  "robots": [
    {
      "id": 1,
      "name": "ROBO-01",
      "model": "Explorer-V2",
      "status": "READY"
    }
  ],
  "operators": [
    {
      "id": 1,
      "name": "김철수",
      "role": "OPERATOR"
    }
  ]
}
```

