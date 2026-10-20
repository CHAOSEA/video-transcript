"""Command line interface for one local WAV transcription."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .artifacts import DEFAULT_MAX_AUDIO_DURATION_SECONDS, MAX_AUDIO_DURATION_SECONDS


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
        description="用本地 faster-whisper 模型转录媒体；设备可选择自动、CPU 或 NVIDIA CUDA。",
        epilog=(
            "模型必须显式选择。环境检查：`video-transcript doctor`（Agent 可用 `--json`）；"
            "模型列表/设备/选择：`video-transcript models list|device|recommend`。"
            "需要质量候选时运行 `video-transcript models recommend --intent quality`；"
            "缺少模型时按提示明确运行 `video-transcript models prepare <模型名>`。"
        ),
    )
    parser.add_argument("media", type=Path, help="本地 WAV、MP3、M4A 或含音轨的 MP4 文件")
    parser.add_argument("--model", help="已缓存模型名（如 small）或本地模型目录；不确定时先运行 models recommend")
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "cuda"),
        help="auto 先尝试 CTranslate2 可用的 CUDA，否则使用 CPU/int8；默认 auto",
    )
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
    parser.add_argument(
        "--vad-filter",
        action="store_true",
        help="启用 faster-whisper 内置 Silero VAD；无语音时可减少幻觉，但低音量语音可能被漏检",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    provided = list(sys.argv[1:] if argv is None else argv)
    if provided[:1] == ["doctor"]:
        from .doctor import main as doctor_main

        return doctor_main(provided[1:])
    if provided[:1] == ["models"]:
        from .model_cli import main as models_main

        return models_main(provided[1:])

    parser = build_parser()
    args = parser.parse_args(provided)
    if not args.model:
        print(
            "请先明确选择模型。运行 `video-transcript models recommend --intent quality` "
            "查看候选，再将选定名称传给 `--model`。",
            file=sys.stderr,
        )
        return 2
    # Importing the inference engine intentionally enables Hugging Face offline
    # mode. Keep that behavior out of the explicit model-preparation command.
    from .engine import EngineError, transcribe_to_artifacts, validate_managed_model_is_prepared
    from .models import RunConfig

    try:
        config = RunConfig(
            model=args.model,
            language=args.language,
            cpu_threads=args.cpu_threads,
            beam_size=args.beam_size,
            device=args.device,
            vad_filter=args.vad_filter,
        )
        validate_managed_model_is_prepared(config.model)
        if config.device == "auto":
            print(f"开始离线转录：model={config.model}, device=auto, threads={config.cpu_threads}")
        else:
            print(f"开始离线转录：model={config.model}, device={config.device}, threads={config.cpu_threads}")
        transcript_path, evidence_path, evidence = transcribe_to_artifacts(
            args.media,
            config=config,
            output_dir=args.output_dir,
            max_duration_seconds=args.max_duration_seconds,
        )
    except (EngineError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if evidence.status == "no_speech_detected":
        print(
            "VAD 未检测到语音；这是检测结果，不等于证明音频中绝对没有讲话。"
            "请确认输入音频并复核相关片段；低音量语音也可能被 VAD 漏掉。"
            "已保存状态说明和 evidence.json，没有生成逐字稿文本。"
        )
    elif evidence.status == "uncertain":
        quality = evidence.quality or {}
        affected_count = quality.get("affected_segment_count", 0)
        if quality.get("review_scope") == "segments" and affected_count:
            print(
                f"已生成 {len(evidence.segments)} 个原始分段；质量提示涉及 {affected_count} 个分段，"
                "请优先复核 Evidence 指出的片段。"
            )
        else:
            print(f"已生成 {len(evidence.segments)} 个原始分段；整体质量信号提示需要人工复核。")
    else:
        print(f"完成：{len(evidence.segments)} 个原始分段")
    runtime = evidence.to_dict()["runtime"]
    print(f"实际设备：{runtime['device']} / {runtime['compute_type']}")
    if runtime.get("device_selection", {}).get("fallback"):
        print("提示：CUDA 不可用或运行失败，已使用同一模型回退到 CPU/int8。")
    print(f"逐字稿：{transcript_path}")
    print(f"证据：{evidence_path}")
    return 0
