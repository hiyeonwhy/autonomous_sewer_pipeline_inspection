"""보고서와 거기 딸린 기록을 지운다.

지우는 것: reports · sessions · defects · drive_logs · reconstructions,
그리고 결함 사진 파일까지.

**기본은 예행 실행이다.** 무엇이 지워질지 세어서 보여 주기만 하고 아무것도 건드리지
않는다. 실제로 지우려면 `--apply` 를 붙인다.

    python scripts/purge_reports.py                     # 예행 — 목록만 확인
    python scripts/purge_reports.py --apply             # 실제 삭제

대상은 아래 두 가지다. 코드를 고쳐 쓰기보다 인자로 주는 편이 안전하다.

    --report-no 20260804-001        발번으로 콕 집어서 (여러 번 줄 수 있다)
    --inspection-date 2026-08-08    점검일자로 (여러 번 줄 수 있다)

사진은 **살아남는 결함이 아무도 안 쓰는 파일만** 지운다. 시드 데이터처럼 여러 결함이
사진 하나를 나눠 쓰는 경우가 있어, 참조를 세어 보지 않고 지우면 남은 보고서의 사진이
깨진다. 경로가 media_root 밖을 가리키면 건너뛴다 — 값이 잘못됐을 때 엉뚱한 파일을
지우지 않기 위해서다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import AsyncMongoClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def resolve_inside_media(media_root: Path, snapshot_path: str) -> Path | None:
    """`/media/...` 를 실제 파일 경로로 바꾼다. 밖을 가리키면 None."""
    if not snapshot_path:
        return None
    relative = snapshot_path.removeprefix("/media/").lstrip("/")
    if not relative:
        return None
    try:
        candidate = (media_root / relative).resolve()
        candidate.relative_to(media_root.resolve())
    except (ValueError, OSError):
        return None
    return candidate


async def main() -> int:
    parser = argparse.ArgumentParser(description="보고서와 딸린 기록 삭제")
    parser.add_argument("--report-no", action="append", default=[], help="지울 보고서 발번")
    parser.add_argument("--inspection-date", action="append", default=[], help="지울 점검일자 (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true", help="실제로 지운다 (없으면 예행)")
    args = parser.parse_args()

    if not args.report_no and not args.inspection_date:
        print("[!] --report-no 나 --inspection-date 중 하나는 있어야 합니다.")
        return 1

    settings = get_settings()
    media_root = Path(settings.media_root)
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]

    try:
        selector: dict = {"$or": []}
        if args.report_no:
            selector["$or"].append({"report_no": {"$in": args.report_no}})
        if args.inspection_date:
            selector["$or"].append({"inspection_date": {"$in": args.inspection_date}})

        reports = await database.reports.find(selector).to_list(length=None)
        if not reports:
            print("[!] 조건에 맞는 보고서가 없습니다.")
            return 1

        report_ids = [r["id"] for r in reports]
        session_ids = sorted({r["session_id"] for r in reports if r.get("session_id") is not None})

        print(f"보고서 {len(reports)}건")
        for r in sorted(reports, key=lambda x: str(x.get("report_no") or "")):
            print(f"  {r.get('report_no')}  점검일자 {r.get('inspection_date')}  세션 {r.get('session_id')}  {r.get('region') or ''}")
        print(f"\n세션 {len(session_ids)}건: {session_ids}")

        defects = await database.defects.find({"session_id": {"$in": session_ids}}).to_list(length=None)
        drive_logs = await database.drive_logs.count_documents({"session_id": {"$in": session_ids}})
        reconstructions = await database.reconstructions.count_documents({"session_id": {"$in": session_ids}})
        print(f"결함 {len(defects)}건 · 주행로그 {drive_logs}건 · 3D 재구성 {reconstructions}건")

        # 지울 사진: 대상 결함이 쓰는 경로 중, **남는 결함이 아무도 안 쓰는** 것만.
        doomed_paths = {str(d.get("snapshot_path") or "") for d in defects}
        doomed_paths.discard("")
        survivors = await database.defects.find(
            {"session_id": {"$nin": session_ids}, "snapshot_path": {"$in": list(doomed_paths)}}
        ).to_list(length=None)
        shared = {str(d.get("snapshot_path")) for d in survivors}
        removable = sorted(doomed_paths - shared)

        files: list[Path] = []
        outside = 0
        for path in removable:
            resolved = resolve_inside_media(media_root, path)
            if resolved is None:
                outside += 1
                continue
            if resolved.exists():
                files.append(resolved)

        print(f"\n사진 {len(doomed_paths)}개 중")
        print(f"  남는 결함과 공유 → 보존 {len(shared)}개")
        print(f"  media_root 밖 → 건너뜀 {outside}개")
        print(f"  실제로 지울 파일 {len(files)}개")
        for f in files[:10]:
            print(f"    {f}")
        if len(files) > 10:
            print(f"    … 외 {len(files) - 10}개")

        if not args.apply:
            print("\n=== 예행 실행입니다. 아무것도 지우지 않았습니다. ===")
            print("실제로 지우려면 같은 명령에 --apply 를 붙이세요.")
            return 0

        print("\n삭제를 시작합니다…")
        r1 = await database.reports.delete_many({"id": {"$in": report_ids}})
        r2 = await database.defects.delete_many({"session_id": {"$in": session_ids}})
        r3 = await database.drive_logs.delete_many({"session_id": {"$in": session_ids}})
        r4 = await database.reconstructions.delete_many({"session_id": {"$in": session_ids}})
        r5 = await database.sessions.delete_many({"id": {"$in": session_ids}})

        removed_files = 0
        for f in files:
            try:
                f.unlink()
                removed_files += 1
            except OSError as error:
                print(f"  [!] 파일 삭제 실패 {f}: {error}")

        print(f"  보고서 {r1.deleted_count} · 결함 {r2.deleted_count} · 주행로그 {r3.deleted_count}")
        print(f"  3D 재구성 {r4.deleted_count} · 세션 {r5.deleted_count} · 사진 파일 {removed_files}")
        print("완료.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
