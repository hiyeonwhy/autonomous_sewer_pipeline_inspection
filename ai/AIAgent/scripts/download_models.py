#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = AI_ROOT / "models"


HF_MODELS = {
    "depth": {
        "repo_id": "depth-anything/Depth-Anything-V2-Small-hf",
        "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99",
        "local_dir": MODEL_ROOT / "depth-anything-v2-small" / "weights",
    },
    "qwen": {
        "repo_id": "Qwen/Qwen2.5-VL-3B-Instruct-AWQ",
        "revision": "e7b623934290c5a4da0ee3c6e1e57bfb6b5abbf2",
        "local_dir": MODEL_ROOT / "qwen2.5-vl-3b-instruct-awq" / "weights",
    },
}


def download_huggingface(name: str) -> None:
    from huggingface_hub import snapshot_download

    spec = HF_MODELS[name]
    target = Path(spec["local_dir"])
    target.mkdir(parents=True, exist_ok=True)
    print(f"[download] {spec['repo_id']} -> {target}")
    snapshot_download(
        repo_id=str(spec["repo_id"]),
        revision=str(spec["revision"]),
        local_dir=target,
    )


def print_models() -> None:
    print("depth: depth-anything/Depth-Anything-V2-Small-hf (~0.09 GiB)")
    print("qwen : Qwen/Qwen2.5-VL-3B-Instruct-AWQ (~3.18 GiB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="PipeCheckkk AI model downloader")
    parser.add_argument(
        "--model",
        choices=("all", "depth", "qwen"),
        default="all",
        help="download target (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="show model IDs and exit without downloading",
    )
    args = parser.parse_args()

    if args.list:
        print_models()
        return
    if args.model in {"all", "depth"}:
        download_huggingface("depth")
    if args.model in {"all", "qwen"}:
        download_huggingface("qwen")



if __name__ == "__main__":
    main()
