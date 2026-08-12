import asyncio
from datetime import datetime, timedelta, timezone

from pymongo import AsyncMongoClient

from app.core.config import get_settings
from app.db.sequence import get_next_sequence


async def seed() -> None:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]

    await database.drop_collection("sessions")
    await database.drop_collection("defects")
    await database.drop_collection("drive_logs")
    await database.drop_collection("reports")
    await database.drop_collection("reconstructions")
    await database.drop_collection("robots")
    await database.drop_collection("operators")
    await database.drop_collection("counters")

    robots = [
        {"id": 1, "name": "ROBO-01", "model": "Explorer-V2", "status": "READY"},
        {"id": 2, "name": "ROBO-02", "model": "Explorer-V2", "status": "MAINTENANCE"},
    ]
    operators = [
        {"id": 1, "name": "김철수", "role": "OPERATOR"},
        {"id": 2, "name": "이영희", "role": "SUPERVISOR"},
    ]

    await database.robots.insert_many(robots)
    await database.operators.insert_many(operators)

    # 시연 관로 실측값: PVC관 375mm, 길이 2m. 결함 위치도 이 구간 안에 있어야 한다.
    started_at = datetime.now(timezone.utc)
    ended_at = started_at + timedelta(minutes=3, seconds=20)

    session_id = await get_next_sequence(database=database, sequence_name="sessions")
    defect_id = await get_next_sequence(database=database, sequence_name="defects")
    drive_log_id = await get_next_sequence(database=database, sequence_name="drive_logs")
    report_id = await get_next_sequence(database=database, sequence_name="reports")
    reconstruction_id = await get_next_sequence(database=database, sequence_name="reconstructions")

    # 표시용 발번도 실제 카운터를 소비해야 API가 만드는 다음 보고서와 번호가 겹치지 않는다.
    report_day = started_at.strftime("%Y%m%d")
    report_seq = await get_next_sequence(
        database=database, sequence_name=f"reports:{report_day}"
    )
    report_no = f"R-{report_day}-{report_seq:03d}"

    session_document = {
        "id": session_id,
        "district_id": 101,
        "priority_score_id": 1,
        "robot_id": 1,
        "approved_by": 1,
        "mode": "AUTO",
        "status": "COMPLETED",
        "started_at": started_at,
        "ended_at": ended_at,
        "defect_summary": {"total": 1, "critical": 1, "warning": 0, "normal": 0},
        "pipe": {
            "pipe_no": "3021547",
            "pipe_category": "관로",
            "pipe_type": "PVC관(VG1)",
            "diameter_mm": 375,
            "drainage_type": "오수",
            "pipe_length_m": 2.0,
        },
        "survey": {
            "survey_location": "OO시 OO구 OO동 시연 구간",
            "total_distance_m": 2.0,
            "undriven_distance_m": 0.0,
        },
    }
    await database.sessions.insert_one(session_document)

    await database.defects.insert_one(
        {
            "id": defect_id,
            "session_id": session_id,
            "defect_type": "BLOCKAGE",
            "severity": "CRITICAL",
            "confidence": 0.94,
            "approx_position_m": 1.2,
            "snapshot_path": "/media/defects/blockage-01.jpg",
            "repair_status": "BEFORE",
            "action_type": "HIGH_PRESSURE_CLEANING",
            "review_status": "PENDING",
            "detected_at": started_at,
        }
    )

    await database.drive_logs.insert_one(
        {
            "id": drive_log_id,
            "session_id": session_id,
            "ultrasonic_distance": 0.8,
            "imu_reading": {"roll": 0.1, "pitch": 0.2, "yaw": 1.4},
            "command": "FORWARD",
            "command_source": "AGENT",
            "agent_state": "DRIVING",
            "note": "정상 주행 중",
            "logged_at": datetime.now(timezone.utc),
        }
    )

    await database.reports.insert_one(
        {
            "id": report_id,
            "report_no": report_no,
            "session_id": session_id,
            "region": "점검 구역 101",
            "survey_location": "OO시 OO구 OO동 시연 구간",
            "survey_purpose": "노후 하수관로 점검",
            "robot_name": "ROBO-01",
            "robot_model": "Explorer-V2",
            "operator_name": "김철수",
            "operator_role": "OPERATOR",
            "inspection_date": started_at.strftime("%Y-%m-%d"),
            "duration": "00:03:20",
            "pipe_no": "3021547",
            "pipe_category": "관로",
            "pipe_type": "PVC관(VG1)",
            "diameter_mm": 375,
            "drainage_type": "오수",
            "pipe_length_m": 2.0,
            "total_distance_m": 2.0,
            "undriven_distance_m": 0.0,
            "total_defect_count": 1,
            "critical_defect_count": 1,
            "summary": "막힘 1건",
            "generated_at": datetime.now(timezone.utc),
        }
    )

    await database.reconstructions.insert_one(
        {
            "id": reconstruction_id,
            "session_id": session_id,
            "status": "COMPLETED",
            "stage": "DONE",
            "progress_percent": 100,
            "image_count": 12,
            "model_path": "/media/reconstructions/model.glb",
            "thumbnail_path": "/media/reconstructions/thumb.png",
            "started_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
            "error_message": None,
        }
    )

    await client.close()


if __name__ == "__main__":
    asyncio.run(seed())
