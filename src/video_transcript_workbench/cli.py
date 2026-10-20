"""Command line interface for one local WAV transcription."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .artifacts import DEFAULT_MAX_AUDIO_DURATION_SECONDS, MAX_AUDIO_DURATION_SECONDS
from .engine import EngineError, transcribe_to_artifacts
from .models import RunConfig


def _parse_max_duration_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入整数秒数") from exc
    if not 1 <= seconds <= MAX_AUDIO_DURATION_SECONDS:
        raise argparse.ArgumentTypeError(
            f"时长限制必须在 1 到 {MAX_AUDIO_DURATION_SECONDS} 秒之间"
        )
    return seconds


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
        "--max-duration-seconds",
        type=_parse_max_duration_seconds,
        default=DEFAULT_MAX_AUDIO_DURATION_SECONDS,
        help=(
            f"本地媒体时长上限；默认 {DEFAULT_MAX_AUDIO_DURATION_SECONDS} 秒，"
            f"最高 {MAX_AUDIO_DURATION_SECONDS} 秒"
        ),
    )
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
            max_duration_seconds=args.max_duration_seconds,
        )
    except (EngineError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"完成：{len(evidence.segments)} 个原始分段")
    print(f"逐字稿：{transcript_path}")
    print(f"证据：{evidence_path}")
    return 0
