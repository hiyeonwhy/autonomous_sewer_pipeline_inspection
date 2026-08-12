import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo import DESCENDING, ReturnDocument

from app.core.config import get_settings
from app.core.agent_auth import require_agent_publish_key
from app.db.sequence import get_next_sequence
from app.domain.defects.cropping import (
    Box,
    appearance_hash,
    box_iou,
    expand_without_others,
    hamming_distance,
    image_size,
    parse_detection_text,
    render_defect_photo,
)
from app.domain.defects.severity import severity_from_bbox
from app.domain.defects.schema import (
    DefectCaptureRequest,
    DefectResponse,
    DefectReviewUpdateRequest,
    DefectType,
    DetectionCaptureRequest,
    DetectionItem,
    RepairStatus,
    ReviewStatus,
    Severity,
)
from app.domain.reports.document import resolve_media_file
from app.domain.reports.summary.prereading import schedule_preread
from app.domain.sessions.schema import DefectSummary

router = APIRouter(prefix="/api", tags=["defects"])

CROP_DIRECTORY = "crops"

# ---------------------------------------------------------------- 중복 탐지 정리
#
# YOLO 는 프레임마다 결과를 내고, 로봇이 천천히 지나가면 **같은 결함이 여러 프레임에
# 걸쳐 잡힌다.** 실측한 3분 12초짜리 점검에서 결함이 113건 나왔는데, 1.5초 사이에만
# 균열 10건이 찍혀 있었다 — 로봇이 거의 안 움직였으니 같은 균열이다.
#
# Agent 에도 중복 방지가 있지만 **프레임 전체를 묶어** 해시해서, 박스가 하나만
# 늘거나 줄어도 새 이벤트가 되어 그 프레임의 결함이 통째로 다시 올라온다.
#
# 그래서 받는 쪽에서 한 번 더 거른다. Agent 는 손대지 않는다.

# 같은 유형이 이 시간 안에 다시 잡히면 같은 결함으로 본다.
#
# 5초인 이유: 이 점검의 주행이 168초였고, 3초면 33건 / 5초면 25건 / 10초면 7건이
# 남았다. 10초는 73초 구간을 균열 하나로 세어 서로 다른 결함이 섞였다. 5초는
# 프레임 연속 중복을 걷어내면서 그 정도까지 뭉개지 않는 선이다.
DETECTION_MERGE_WINDOW_S = 5.0

# 이 신뢰도 아래는 잡음으로 본다.
#
# 0.5 로 올린 이유: 사진 판독이 붙으면서 「탐지는 됐지만 사진에 없는」 결함은
# 판독이 걸러 준다. 그래서 하한은 그 앞단에서 명백한 잡음만 쳐내면 된다.
# 실측(세션 217, 30건)에서 0.5 미만이 9건이었다.
DETECTION_CONFIDENCE_FLOOR = 0.5

# **심각 등급에도 같은 하한을 건다.**
#
# 심각도는 박스 면적으로 정하고 신뢰도는 YOLO 의 확신도라 서로 다른 축이다. 그래서
# 「큰 균열인데 확신이 낮은」 것을 살리려 심각 등급만 하한을 풀어 줬었다. 면제 →
# 0.4 로 두 번 조였는데도 계속 새어 나왔다. 심각도가 박스 면적에서 나오는 이상
# 완화는 곧 「큰 박스는 통과」라서, 정작 걸러야 할 것만 통과시킨다.
#
#   세션 218  면제  → 0.29 0.31 0.31 0.38 이 실림
#   세션 219  0.4   → 0.47 0.48 0.48 이 실림
#
# 완화를 없앤다. 「탐지는 됐지만 사진에 없는」 결함은 이제 사진 판독이 걸러 주므로
# (`prereading`), 하한이 심각 결함까지 떠받칠 이유가 없어졌다.
SEVERITY_RANK = {Severity.NORMAL: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}

# 박스가 이만큼 겹치면 같은 자리를 두 번 잡은 것으로 본다. 한 프레임 안에서 YOLO 가
# 같은 균열에 박스를 두세 개 씌우는 일이 실제로 있었다(IoU 0.97 = 1픽셀 차이).
DETECTION_IOU_THRESHOLD = 0.5

# 프레임을 건너뛰며 같은 결함을 잇기 위한 느슨한 겹침 기준.
#
# 세션 218 을 사람이 직접 훑어 「같은 결함」이라고 짚은 쌍과 아닌 쌍의 IoU 를 재 봤다.
#
#   같은 결함:   2-5 = 0.104   7-13 = 0.529   12-14 = 0.361
#   다른 결함:   1-4, 1-6, 4-6, 2-8, 8-11, 1-7, 6-7 — **전부 정확히 0.000**
#
# 서로 다른 결함은 박스가 단 한 픽셀도 겹치지 않는다. 관 안에서 결함들이 서로 떨어져
# 있으니 당연한 결과다. 그래서 「조금이라도 겹치면 같은 것」으로 봐도 안전하다.
# 0.5 로는 0.104·0.361 을 놓쳐 중복 3쌍이 그대로 보고서에 실렸다.
DETECTION_TRACK_IOU_THRESHOLD = 0.1

# 그 겹침을 견주는 시간 범위. 로봇이 한 자리에 오래 머물면 같은 결함이 39초 뒤에 다시
# 잡힌다(세션 218 의 7-13). 20초로는 못 이어서 45초로 둔다. 시간만으로는 아무것도
# 묶지 않고 **겹침이 있을 때만** 쓰므로 넓혀도 과병합하지 않는다.
DETECTION_TRACK_WINDOW_S = 45.0

# 사진 생김새가 이만큼까지 비슷하면 같은 결함으로 본다 (64비트 중 다른 비트 수).
#
# 8 이었다. 너무 좁아서 박스가 안 겹치는 중복을 하나도 못 잡았다. 사람이 짚어 준
# 「같은 결함」과 「다른 결함」의 거리를 두 세션에서 재 보니 값이 갈렸다
# (신뢰도 하한 0.5 를 통과한 것끼리만 — 어차피 나머지는 저장되지 않는다).
#
#   세션 218   같은 결함 최대 21   다른 결함 최소 22
#   세션 219   같은 결함 최대 15   다른 결함 최소 30
#
# 세션 220 을 더해 **비교 창을 45초로 맞춘 뒤** 다시 재니 간극이 벌어졌다. 예전에
# 218 의 다른 결함이 22 로 붙어 보였던 것은 48.8초 떨어진 쌍을 견주고 있어서였다 —
# 그 정도 시간이면 로봇이 딴 데를 보고 있어 애초에 비교 대상이 아니다.
#
#   세션 218   같은 결함 최대 21   다른 결함 최소 28
#   세션 219   같은 결함 최대 15   다른 결함 최소 30
#   세션 220   같은 결함 최대 22   다른 결함 최소 27
#
# 22 와 27 사이인 24 로 둔다. 양쪽에 여유가 2 와 3 이다.
DETECTION_HASH_DISTANCE = 24

# 생김새를 견주는 시간 범위. 박스 위치는 로봇이 전진하면 크게 달라지므로 시간 창을
# 넉넉히 두고 사진으로 판단한다.
#
# 20초였다. 세션 220 에서 같은 표면 손상이 40.8초에 걸쳐 세 번 잡혔는데 창 밖이라
# 견주지도 못했다. 겹침 규칙과 같은 45초로 맞춘다. 창을 넓히면 오히려 간극이
# 벌어진다 — 위 DETECTION_HASH_DISTANCE 의 실측을 보라.
DETECTION_APPEARANCE_WINDOW_S = 45.0

# **원본 프레임 전체**가 이만큼 닮았으면 로봇이 사실상 안 움직인 것으로 본다.
#
# 결함 박스만 견주면 같은 결함이라도 다가갈수록 크기·자리가 달라져 못 잇는다.
# 프레임을 통째로 견주면 「로봇이 그 자리에 머물렀다」가 드러나므로, 그 사이 같은
# 유형으로 잡힌 것은 같은 결함으로 볼 수 있다. 실측(세션 216)에서 41건이 27건으로
# 줄었다.
#
# 4로 좁게 잡는 이유: 프레임이 닮았다는 것은 「같은 결함」이 아니라 「안 움직였다」는
# 뜻이라, 넓히면 한 화면에 있던 서로 다른 결함까지 하나로 뭉갠다. 10까지 올리면
# 19건이 되지만 그 위험이 커진다.
DETECTION_FRAME_HASH_DISTANCE = 4


# 사진 판독을 기다려 주는 시간. 이 시간이 지나면 판독이 안 왔어도 「끝난 것」으로
# 쳐서 `photo_reviewed` 를 참으로 내보낸다.
#
# 결함은 탐지 즉시 저장되지만 사진 판독은 11.7초쯤 뒤에 온다. 결함 **목록**은 그동안에도
# 보여 준다 — 로봇이 지금 무엇을 보고 있는지가 점검 중 화면의 핵심이다. 하지만 결함
# **통계**까지 먼저 세면, 판독이 「사진에 없다」고 할 때마다 숫자가 줄어들어 계산이
# 틀린 것처럼 보인다. 그래서 통계는 판독이 끝난 것만 센다.
#
# 45초로 두는 이유: 판독은 동시 2건씩 도므로 대기열이 길어지면 한 건이 20~30초까지
# 밀린다. 그보다 넉넉해야 정상 상황에서 이 안전장치가 안 걸린다. 반대로 판독 서버가
# 죽으면 통계가 영영 0 으로 남으므로 무한정 길게 둘 수도 없다.
PREREAD_GRACE_PERIOD = timedelta(seconds=45)


def as_utc(value: datetime | None) -> datetime:
    """Mongo 가 돌려주는 시간대 없는 시각을 UTC 로 못박는다.

    드라이버는 UTC 로 저장한 값을 **naive** 로 돌려준다. 그걸 요청에 실려 온
    시간대 있는 시각과 그대로 빼면 `TypeError` 로 수집이 통째로 500 이 된다
    (실제로 한 점검의 결함이 전부 유실됐다).
    """
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def convert_defect(document: dict, *, settled_before: datetime | None = None) -> DefectResponse:
    """결함 문서를 응답으로 옮기고 **사진 판독이 끝났는지**를 함께 실어 준다.

    결함 목록은 탐지 즉시 보여 줘야 한다 — 로봇이 지금 무엇을 보고 있는지가 점검 중
    화면의 핵심이다. 반면 결함 통계는 판독이 끝난 것만 세야 한다. 판독이 「사진에
    없다」고 하면 그 결함은 목록에서 빠지는데, 통계가 먼저 세어 두면 **숫자가
    줄어들어** 계산이 틀린 것처럼 보인다.

    그래서 거르지 않고 표시만 해서 보낸다. 무엇을 세고 무엇을 보여 줄지는 화면이
    정한다.
    """
    document.pop("_id", None)
    response = DefectResponse.model_validate(document)
    if settled_before is not None:
        detected_at = as_utc(response.detected_at)
        response.photo_reviewed = bool(document.get("photo_description")) or (
            # 판독 서버가 죽어도 통계가 영영 0 으로 남지는 않게 한다.
            detected_at <= settled_before
        )
    return response


async def update_session_defect_summary(database, session_id: int, severity: Severity) -> None:
    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        return

    summary_data = session_document.get("defect_summary") or DefectSummary().model_dump()
    summary = DefectSummary.model_validate(summary_data)

    if severity == Severity.CRITICAL:
        summary.critical += 1
    elif severity == Severity.WARNING:
        summary.warning += 1
    else:
        summary.normal += 1

    summary.total += 1

    await database.sessions.update_one(
        {"id": session_id},
        {"$set": {"defect_summary": summary.model_dump()}},
    )


async def account_detection_summary_once(
    database,
    session_id: int,
    event_filter: dict[str, object],
    severity: Severity,
) -> None:
    """Atomically count a stable detection identity exactly once in its session."""
    identity = json.dumps(
        [event_filter[key] for key in ("operation_id", "event_id", "detection_index")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    severity_field = {
        Severity.CRITICAL: "defect_summary.critical",
        Severity.WARNING: "defect_summary.warning",
    }.get(severity, "defect_summary.normal")
    await database.sessions.update_one(
        {"id": session_id, "counted_detection_keys": {"$ne": identity}},
        {
            "$addToSet": {"counted_detection_keys": identity},
            "$inc": {"defect_summary.total": 1, severity_field: 1},
        },
    )


@router.get("/sessions/{session_id}/defects", response_model=list[DefectResponse])
async def get_session_defects(session_id: int, request: Request) -> list[DefectResponse]:
    database = request.app.state.database
    cursor = (
        # 사진에서 확인되지 않은 결함(REJECTED)은 목록에 내보내지 않는다.
        database.defects.find({"session_id": session_id, "review_status": {"$ne": ReviewStatus.REJECTED.value}})
        .sort("detected_at", DESCENDING)
        .limit(100)
    )
    documents = await cursor.to_list(length=100)
    settled_before = datetime.now(timezone.utc) - PREREAD_GRACE_PERIOD
    return [convert_defect(document, settled_before=settled_before) for document in documents]


@router.post(
    "/sessions/{session_id}/captures",
    response_model=DefectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_agent_publish_key)],
)
async def create_capture_defect(
    session_id: int,
    payload: DefectCaptureRequest,
    request: Request,
) -> DefectResponse:
    database = request.app.state.database

    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    operation_id = payload.operation_id or "legacy-capture"
    event_id = payload.event_id or json.dumps(
        [payload.snapshot_path, payload.defect_type.value],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    detection_index = payload.detection_index if payload.detection_index is not None else 0
    event_filter = {
        "session_id": session_id,
        "operation_id": operation_id,
        "event_id": event_id,
        "detection_index": detection_index,
    }
    existing = await database.defects.find_one(event_filter)
    defect_id = (
        existing["id"]
        if existing is not None
        else await get_next_sequence(database=database, sequence_name="defects")
    )
    document = {
        "id": defect_id,
        "session_id": session_id,
        "operation_id": operation_id,
        "event_id": event_id,
        "detection_index": detection_index,
        "defect_type": payload.defect_type.value,
        "severity": payload.severity.value,
        "confidence": payload.confidence,
        "approx_position_m": payload.approx_position_m,
        "snapshot_path": payload.snapshot_path,
        "repair_status": RepairStatus.BEFORE.value,
        "action_type": None,
        "review_status": ReviewStatus.PENDING.value,
        "detected_at": payload.detected_at or datetime.now(timezone.utc),
    }

    await database.defects.update_one(
        event_filter,
        {"$setOnInsert": document},
        upsert=True,
    )
    persisted = await database.defects.find_one(event_filter)
    if persisted is None:
        raise HTTPException(status_code=500, detail="결함 저장 확인에 실패했습니다.")
    await account_detection_summary_once(
        database, session_id, event_filter, Severity(persisted["severity"])
    )
    return convert_defect(persisted)


def resolve_detections(payload: DetectionCaptureRequest) -> list[DetectionItem]:
    """구조화 목록이든 YOLO 텍스트든 하나의 형태로 맞춘다."""
    if payload.detections:
        return payload.detections

    items: list[DetectionItem] = []
    for label, confidence, box in parse_detection_text(payload.detections_text or ""):
        try:
            defect_type = DefectType(label)
        except ValueError:
            # 모르는 라벨을 OTHER로 삼키면 결함점수가 0이 되어 등급이 낮게 나온다.
            raise HTTPException(
                status_code=422, detail=f"알 수 없는 결함 종류입니다: {label}"
            ) from None
        items.append(
            DetectionItem(
                defect_type=defect_type,
                confidence=confidence,
                bbox={"x1": box.x1, "y1": box.y1, "x2": box.x2, "y2": box.y2},
            )
        )
    return items


async def keep_after_dedup(
    database,
    session_id: int,
    payload: DetectionCaptureRequest,
    items: list[DetectionItem],
    source_bytes: bytes,
    image_width: int,
    image_height: int,
    frame_digest: str,
) -> list[tuple[int, DetectionItem, Severity, str]]:
    """저장할 탐지만 골라 (원래 순번, 항목, 심각도) 로 돌려준다.

    순번을 함께 돌려주는 이유는 `detection_index` 가 **요청 안에서의 위치**여야
    하기 때문이다. 거른 뒤 다시 매기면 같은 요청을 재전송했을 때 다른 번호가 붙어
    멱등성이 깨진다.

    거르는 규칙은 넷이다. 뒤로 갈수록 넓게 잡는다.

      1. 신뢰도가 하한 미만이면 버린다.
      2. **같은 프레임 안에서** 박스가 겹치면 한 번만 남긴다. YOLO 가 같은 균열에
         박스를 두세 개 씌우는 일이 있다(실측 IoU 0.97 = 1픽셀 차이).
      3. 앞서 저장된 결함과 박스가 겹치면 같은 자리를 다시 잡은 것으로 본다.
      4. 박스가 안 겹쳐도 **사진 생김새가 닮았으면** 같은 결함으로 본다. 로봇이
         전진하면 같은 결함도 화면에서 자리와 크기가 달라져 박스만으로는 못 잇는다.

    2~4 에서 같은 결함으로 판단했는데 이번 관측이 **더 심하면**, 둘 다 남기지 않고
    앞서 남긴 쪽의 심각도를 올린다. 심각도는 그 결함의 속성이지 별개 결함의 근거가
    아니다. 상태등급은 구간 내 최대값으로 정해지므로 이렇게 해도 등급은 지켜진다.
    """
    detected_at = payload.detected_at or datetime.now(timezone.utc)
    # 사진 비교는 시간 창을 넓게 본다. 로봇이 같은 결함을 오래 바라볼 수 있다.
    # 규칙마다 창이 다르므로 **가장 긴 것**까지 가져와야 한다 — 20초만 가져오면
    # 45초짜리 겹침 규칙이 견줄 대상 자체를 못 받아 조용히 놀게 된다.
    lookback_window = max(DETECTION_APPEARANCE_WINDOW_S, DETECTION_TRACK_WINDOW_S)
    lookback = await database.defects.find(
        {
            "session_id": session_id,
            "detected_at": {"$gte": detected_at - timedelta(seconds=lookback_window)},
        }
    ).to_list(length=None)

    kept: list[tuple[int, DetectionItem, Severity, str]] = []
    for index, item in enumerate(items):
        severity = item.severity or severity_from_bbox(
            item.bbox, image_width=image_width, image_height=image_height
        )
        if item.confidence < DETECTION_CONFIDENCE_FLOOR:
            continue

        box = Box(item.bbox.x1, item.bbox.y1, item.bbox.x2, item.bbox.y2)
        digest = appearance_hash(source_bytes, box)
        rank = SEVERITY_RANK.get(severity, 0)

        # (2) 같은 프레임 안에서 이미 남긴 것과 겹치는가
        overlaps_in_frame = False
        for slot, (kept_index, other, other_severity, other_hash) in enumerate(kept):
            if other.defect_type is not item.defect_type:
                continue
            if (
                box_iou(box, Box(other.bbox.x1, other.bbox.y1, other.bbox.x2, other.bbox.y2))
                < DETECTION_IOU_THRESHOLD
            ):
                continue
            # 겹치는 쪽이 더 심하면 둘 다 남기지 않고 **심각도만 올린다.** 아직
            # 저장 전이라 자리만 바꿔 끼우면 된다 (규칙 3·4 와 같은 판단이다).
            if rank > SEVERITY_RANK.get(other_severity, 0):
                kept[slot] = (kept_index, other, severity, other_hash)
            overlaps_in_frame = True
            break
        if overlaps_in_frame:
            continue

        # (3)(4) 앞서 저장된 것과 자리가 겹치거나 생김새가 닮았는가
        duplicate = False
        for stored in lookback:
            if stored.get("defect_type") != item.defect_type.value:
                continue
            if stored.get("event_id") == payload.event_id:
                continue
            elapsed = (detected_at - as_utc(stored.get("detected_at"))).total_seconds()
            stored_box = stored.get("bbox") or {}
            overlap = box_iou(
                box,
                Box(
                    stored_box.get("x1", 0.0),
                    stored_box.get("y1", 0.0),
                    stored_box.get("x2", 0.0),
                    stored_box.get("y2", 0.0),
                ),
            )
            same_place = (
                elapsed <= DETECTION_MERGE_WINDOW_S
                or overlap >= DETECTION_IOU_THRESHOLD
                # 로봇이 전진하면 같은 결함도 박스가 커지고 자리가 밀린다. 겹침이
                # 남아 있는 동안은 같은 것을 계속 보고 있는 것이다.
                or (elapsed <= DETECTION_TRACK_WINDOW_S and overlap >= DETECTION_TRACK_IOU_THRESHOLD)
            )
            # 생김새·프레임 비교는 원래 창(20초) 그대로 둔다. 위에서 조회 범위를
            # 45초로 넓힌 것은 겹침 규칙 때문이지 이 둘의 기준을 푼 게 아니다.
            within_appearance_window = elapsed <= DETECTION_APPEARANCE_WINDOW_S
            stored_hash = stored.get("appearance_hash")
            looks_same = (
                within_appearance_window
                and bool(stored_hash)
                and hamming_distance(digest, stored_hash) <= DETECTION_HASH_DISTANCE
            )
            # 프레임이 거의 같으면 로봇이 그 자리에 머문 것이다. 같은 유형이 다시
            # 잡혔다면 새 결함이 아니라 같은 것을 또 본 것으로 본다.
            stored_frame = stored.get("frame_hash")
            same_frame = (
                within_appearance_window
                and bool(stored_frame)
                and hamming_distance(frame_digest, stored_frame) <= DETECTION_FRAME_HASH_DISTANCE
            )
            if not (same_place or looks_same or same_frame):
                continue

            # 같은 결함인데 이번 관측이 **더 심하면**, 예전에는 둘 다 남겼다. 그러면
            # 보고서에 같은 결함이 「주의」와 「심각」으로 두 줄 실린다(세션 219 의
            # 5-7: 겹침 0.466 인데 심각도가 올라가 둘 다 남았다).
            #
            # 남기지 말고 **저장된 쪽을 올린다.** 같은 결함이라는 판단은 이미 끝났고,
            # 심각도는 그 결함의 속성이지 별개 결함의 근거가 아니다. 상태등급은 구간
            # 내 최대값으로 정해지므로 등급도 그대로 지켜진다.
            if rank > SEVERITY_RANK.get(Severity(stored["severity"]), 0):
                await database.defects.update_one(
                    {"id": stored["id"]}, {"$set": {"severity": severity.value}}
                )
                stored["severity"] = severity.value
            duplicate = True
            break
        if duplicate:
            continue

        kept.append((index, item, severity, digest))
    return kept


def build_detection_documents(
    session_id: int,
    payload: DetectionCaptureRequest,
    items: list[DetectionItem],
    source_bytes: bytes,
    crop_directory: Path,
    defect_ids: list[int],
    appearance_hashes: list[str],
    mask_boxes: list[Box],
    detection_indexes: list[int],
    frame_hash: str,
) -> list[dict]:
    """결함마다 사진을 만들고 저장할 문서를 조립한다.

    남길 영역은 **다른 결함이 안 보이는 선에서 가장 넓게** 잡는다. 박스에 딱 맞추면
    관 벽면·물 흐름 같은 맥락이 사라져 판독 품질이 떨어진다. 그 영역 밖은 검게
    덮되 원본 크기는 유지한다 — 보고서 사진 상자가 고정 크기라 비율이 일정해야 하고,
    결함의 위치와 관 둘레 대비 크기도 보여야 한다.

    심각도는 **결함마다 따로** 계산한다. YOLO가 내지 않으므로 박스 면적 비율로
    정하며(`severity_from_bbox`), 탐지 항목이 심각도를 직접 보내면 그 값을 쓴다.
    한 프레임의 결함에 같은 심각도를 일괄로 주면 상태등급이 왜곡된다 — 등급은
    구간 내 결함점수의 최대값이라 가장 심한 결함 하나가 구간 전체를 정한다.
    """
    width, height = image_size(source_bytes)
    boxes = [
        Box(item.bbox.x1, item.bbox.y1, item.bbox.x2, item.bbox.y2) for item in items
    ]
    detected_at = payload.detected_at or datetime.now(timezone.utc)

    documents: list[dict] = []
    for index, (item, box, defect_id, digest) in enumerate(
        zip(items, boxes, defect_ids, appearance_hashes)
    ):
        # 가려야 할 대상은 **이 프레임에서 탐지된 전부**다. 저장하는 것만 보고
        # 가리면, 중복으로 걸러진 결함이 남의 사진에 그대로 찍힌다 — 표면 손상
        # 사진에 균열이 같이 보이던 것이 이것이다.
        others = [
            other
            for other in mask_boxes
            if not (
                other.x1 == box.x1
                and other.y1 == box.y1
                and other.x2 == box.x2
                and other.y2 == box.y2
            )
        ]
        region = expand_without_others(
            box, others, image_width=width, image_height=height
        )
        filename, _photo_width, _photo_height = render_defect_photo(
            source_bytes, region, crop_directory
        )
        severity = item.severity or severity_from_bbox(
            item.bbox, image_width=width, image_height=height
        )

        documents.append(
            {
                "id": defect_id,
                "session_id": session_id,
                "operation_id": payload.operation_id,
                "event_id": payload.event_id,
                # **요청 안에서의 원래 순번**이어야 한다. 중복을 걸러 낸 뒤 다시 매기면
                # 조회 키(event_filter)와 어긋나 저장 확인이 실패한다 — 실제로 한
                # 프레임에 결함이 여럿이고 그중 하나가 걸러지면 500 이 났다.
                "detection_index": detection_indexes[index],
                "defect_type": item.defect_type.value,
                "severity": severity.value,
                "confidence": item.confidence,
                "approx_position_m": payload.approx_position_m,
                "snapshot_path": f"/media/{CROP_DIRECTORY}/{filename}",
                "source_snapshot_path": payload.snapshot_path,
                "bbox": item.bbox.model_dump(),
                # 다음 프레임이 「같은 결함을 또 찍었는지」 견줄 때 쓰는 생김새 요약.
                "appearance_hash": digest,
                # 원본 프레임 전체의 요약. 「로봇이 그 자리에 머물렀는지」를 본다.
                "frame_hash": frame_hash,
                "repair_status": RepairStatus.BEFORE.value,
                "action_type": None,
                "review_status": ReviewStatus.PENDING.value,
                "detected_at": detected_at,
            }
        )
    return documents


@router.post(
    "/sessions/{session_id}/detections",
    response_model=list[DefectResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_agent_publish_key)],
)
async def create_detection_defects(
    session_id: int,
    payload: DetectionCaptureRequest,
    request: Request,
) -> list[DefectResponse]:
    """한 프레임의 탐지 결과를 결함별 크롭과 함께 전부 등록한다.

    원본 프레임은 먼저 `POST /api/media/captures`로 올리고, 그 `snapshot_path`를
    여기에 넘긴다. 결함마다 크롭이 생기고 결함 문서가 하나씩 만들어진다.
    """
    database = request.app.state.database

    session_document = await database.sessions.find_one({"id": session_id})
    if session_document is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    try:
        items = resolve_detections(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # media_root 밖을 가리키는 경로를 막는다.
    resolved = resolve_media_file(payload.snapshot_path)
    if resolved is None:
        raise HTTPException(
            status_code=404, detail=f"원본 이미지를 찾을 수 없습니다: {payload.snapshot_path}"
        )
    source_bytes = resolved.read_bytes()
    width, height = image_size(source_bytes)

    # 프레임 전체 해시는 요청당 한 번만 구한다 — 한 요청의 결함들은 같은 사진이다.
    frame_digest = appearance_hash(source_bytes, Box(0.0, 0.0, float(width), float(height)))

    # 잡음과 연속 중복을 먼저 걸러 낸다. 남은 것만 사진을 만들고 저장한다.
    kept = await keep_after_dedup(
        database, session_id, payload, items, source_bytes, width, height, frame_digest
    )
    if not kept:
        return []
    # 사진에서 가릴 대상은 **거르기 전** 전부다. 저장은 남은 것만 하되, 마스킹은
    # 이 프레임에서 탐지된 모든 결함을 기준으로 해야 남의 결함이 안 찍힌다.
    mask_boxes = [Box(item.bbox.x1, item.bbox.y1, item.bbox.x2, item.bbox.y2) for item in items]
    items = [item for _index, item, _severity, _hash in kept]
    appearance_hashes = [digest for _index, _item, _severity, digest in kept]
    detection_indexes = [index for index, _item, _severity, _hash in kept]

    event_filters = [
        {
            "session_id": session_id,
            "operation_id": payload.operation_id,
            "event_id": payload.event_id,
            "detection_index": index,
        }
        for index, _item, _severity, _hash in kept
    ]
    existing_documents = [
        await database.defects.find_one(event_filter) for event_filter in event_filters
    ]
    if all(document is not None for document in existing_documents):
        for event_filter, document in zip(event_filters, existing_documents):
            assert document is not None
            await account_detection_summary_once(
                database,
                session_id,
                event_filter,
                Severity(document["severity"]),
            )
        return [convert_defect(document) for document in existing_documents if document is not None]

    defect_ids = [
        await get_next_sequence(database=database, sequence_name="defects")
        for _ in items
    ]
    crop_directory = Path(get_settings().media_root) / CROP_DIRECTORY

    try:
        documents = build_detection_documents(
            session_id,
            payload,
            items,
            source_bytes,
            crop_directory,
            defect_ids,
            appearance_hashes,
            mask_boxes,
            detection_indexes,
            frame_digest,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"크롭 생성 실패: {error}") from error

    persisted_documents: list[dict] = []
    for event_filter, document in zip(event_filters, documents):
        await database.defects.update_one(
            event_filter,
            {"$setOnInsert": document},
            upsert=True,
        )
        persisted = await database.defects.find_one(event_filter)
        if persisted is None:
            raise HTTPException(status_code=500, detail="결함 저장 확인에 실패했습니다.")
        persisted_documents.append(persisted)
        # 결함 저장 뒤 summary 갱신이 실패해도 재시도 시 atomic identity 조건으로 복구된다.
        await account_detection_summary_once(
            database,
            session_id,
            event_filter,
            Severity(persisted["severity"]),
        )

    # 사진 판독을 **여기서 미리** 걸어 둔다. 점검이 끝난 뒤에 몰아 읽으면 결함
    # 20건에 4분이 걸려 보고서를 바로 못 낸다. 응답은 기다리지 않는다.
    schedule_preread(database, persisted_documents)
    return [convert_defect(document) for document in persisted_documents]


@router.patch("/defects/{defect_id}", response_model=DefectResponse)
async def update_defect_review(
    defect_id: int,
    payload: DefectReviewUpdateRequest,
    request: Request,
) -> DefectResponse:
    database = request.app.state.database

    update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "repair_status" in update_data and isinstance(update_data["repair_status"], str):
        update_data["repair_status"] = update_data["repair_status"].upper()
    if "review_status" in update_data and isinstance(update_data["review_status"], str):
        update_data["review_status"] = update_data["review_status"].upper()

    document = await database.defects.find_one_and_update(
        {"id": defect_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )

    if document is None:
        raise HTTPException(status_code=404, detail="결함을 찾을 수 없습니다.")

    return convert_defect(document)


@router.get("/defects/{defect_id}/snapshot")
async def get_defect_snapshot(defect_id: int, request: Request) -> dict[str, str]:
    database = request.app.state.database
    document = await database.defects.find_one({"id": defect_id})

    if document is None:
        raise HTTPException(status_code=404, detail="결함을 찾을 수 없습니다.")

    return {"snapshot_path": str(document.get("snapshot_path", ""))}
