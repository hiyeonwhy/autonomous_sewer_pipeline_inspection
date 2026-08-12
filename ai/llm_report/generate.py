"""CLI entry point for generating one session's report summary.

Usage (from the `ai/` directory, with the venv active):

    python -m llm_report.generate --session-id 4
    python -m llm_report.generate --session-id 4 --no-model     # rule-based only
    python -m llm_report.generate --session-id 4 --output out.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .cloud_client import HttpCloudClient, cloud_api_base_url
from .qwen_provider import QwenReportProvider
from .service import ReportSummaryService, to_cloud_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm_report.generate",
        description="탐사 세션의 결함 데이터로 보고서 종합 평가를 생성한다.",
    )
    parser.add_argument("--session-id", type=int, required=True, help="Cloud API 세션 ID")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Cloud API 주소 (기본: PIPECHECKKK_CLOUD_API_URL 또는 {cloud_api_base_url()})",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Qwen을 로드하지 않고 규칙 기반 요약만 생성한다 (GPU 불필요)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="결함 사진 판독을 건너뛴다 (문단 요약만 Qwen으로 생성)",
    )
    parser.add_argument(
        "--report-id",
        type=int,
        default=None,
        help="생성한 요약을 이 보고서에 저장한다 (PATCH /api/reports/{id})",
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="결과 JSON을 저장할 경로 (기본: stdout)"
    )
    parser.add_argument("--verbose", action="store_true", help="진행 로그를 출력한다")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    provider = None
    if not args.no_model:
        candidate = QwenReportProvider()
        if not candidate.is_downloaded():
            print(
                f"[warn] Qwen weights not found at {candidate.model_dir} — "
                "falling back to the rule-based summary. Run either:\n"
                "         python scripts/download_models.py --model qwen-report   # 7B, 판독 품질\n"
                "         python scripts/download_models.py --model qwen          # 3B, Agent와 공용",
                file=sys.stderr,
            )
        else:
            provider = candidate
            print(f"[info] 판독 모델: {candidate.name}", file=sys.stderr)

    service = ReportSummaryService(
        provider=provider,
        client=HttpCloudClient(args.base_url) if args.base_url else HttpCloudClient(),
        describe_images=not args.no_images,
    )

    try:
        summary = service.build_from_cloud(args.session_id)
    except RuntimeError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1

    if args.report_id is not None:
        try:
            service.client.write_report_summary(args.report_id, to_cloud_payload(summary))
        except RuntimeError as error:
            print(f"[error] 보고서 {args.report_id} 저장 실패: {error}", file=sys.stderr)
            return 1
        print(
            f"saved to report {args.report_id} — "
            f"{service.client.base_url}/api/reports/{args.report_id}/document",
            file=sys.stderr,
        )

    rendered = summary.model_dump_json(by_alias=True, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"saved: {args.output}", file=sys.stderr)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
