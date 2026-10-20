"""Command line interface for one local WAV transcription."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .engine import EngineError, transcribe_to_artifacts
from .models import RunConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-transcript",
        description="Transcribe one local WAV, MP3, M4A, or MP4 file with a cached model on CPU/int8.",
    )
    parser.add_argument("media", type=Path, help="本地 WAV、MP3、M4A 或含音轨的 MP4 文件")
    parser.add_argument("--model", required=True, help="已缓存模型名（如 small）或本地模型目录")
    parser.add_argument("--output-dir", required=True, type=Path, help="一个新的输出目录")
    parser.add_argument("--language", default="zh", choices=("zh", "en", "ja", "auto"))
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=os.cpu_count() or 1,
        help="CPU 线程数；默认使用逻辑处理器数量",
    )
    parser.add_argument("--beam-size", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = RunConfig(
            model=args.model,
            language=args.language,
            cpu_threads=args.cpu_threads,
            beam_size=args.beam_size,
        )
        print(
            f"开始离线转录：model={config.model}, device=cpu, compute_type=int8, "
            f"threads={config.cpu_threads}"
        )
        transcript_path, evidence_path, evidence = transcribe_to_artifacts(
            args.media,
            config=config,
            output_dir=args.output_dir,
        )
    except (EngineError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"完成：{len(evidence.segments)} 个原始分段")
    print(f"逐字稿：{transcript_path}")
    print(f"证据：{evidence_path}")
    return 0
