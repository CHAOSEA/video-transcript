"""CLI entry point for subtitle-first online transcription."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .download import DEFAULT_MAX_DOWNLOAD_BYTES, DEFAULT_MAX_DURATION_SECONDS
from .models import RunConfig
from .workflow import run_link_transcription


_LOCAL_MEDIA_FALLBACK_CATEGORIES = {
    "access_or_auth_restricted",
    "network_error",
    "proxy_unavailable",
    "extractor_error",
    "network_or_extractor_error",
}


def _local_media_fallback_message(category: str | None) -> str | None:
    if category not in _LOCAL_MEDIA_FALLBACK_CATEGORIES:
        return None
    return (
        "如果你已通过平台允许的方式保存了视频或音频，可将本地文件交给 "
        "`video-transcript` 转录；本工具不会读取登录凭据或绕过访问检查。"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-transcript-link",
        description="先获取 YouTube、Bilibili、抖音或小红书字幕；符合回退规则时再下载音轨并本机 CPU 转录。",
    )
    parser.add_argument("url", help="HTTPS YouTube、Bilibili、抖音或小红书单视频链接")
    parser.add_argument("--language", default="zh", choices=("zh", "en", "ja"))
    parser.add_argument("--model", required=True, help="已缓存模型名（例如 small）或本地模型目录")
    parser.add_argument("--output-dir", required=True, type=Path, help="一个新的输出目录")
    parser.add_argument(
        "--proxy",
        help="仅本次运行使用的 HTTP(S) 代理；不得在地址中填写用户名或密码",
    )
    parser.add_argument("--cpu-threads", type=int, default=20)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--max-duration-seconds",
        type=int,
        default=DEFAULT_MAX_DURATION_SECONDS,
        help=f"允许的最长媒体时长，默认 {DEFAULT_MAX_DURATION_SECONDS} 秒",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help=f"允许的最大音频下载量，默认 {DEFAULT_MAX_DOWNLOAD_BYTES} 字节",
    )
    parser.add_argument(
        "--fallback-on-suspect-subtitles",
        action="store_true",
        help="字幕解析损坏或覆盖疑似不完整时，明确允许尝试本地 ASR",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = RunConfig(
            model=args.model,
            language=args.language,
            cpu_threads=args.cpu_threads,
            beam_size=args.beam_size,
        )
        evidence = run_link_transcription(
            args.url,
            language=args.language,
            config=config,
            output_dir=args.output_dir,
            max_duration_seconds=args.max_duration_seconds,
            max_download_bytes=args.max_download_bytes,
            fallback_on_suspect=args.fallback_on_suspect_subtitles,
            proxy=args.proxy,
        )
    except (ValueError, OSError) as exc:
        # Do not echo the input URL, signed stream URL, or yt-dlp exception text.
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"状态：{evidence['status']}")
    print(f"证据：{args.output_dir / 'evidence.json'}")
    if evidence["status"] == "success":
        print(f"最终来源：{evidence['workflow']['final_transcription_source']}")
        print(f"逐字稿：{args.output_dir / 'transcript.md'}")
        return 0
    error = evidence.get("error") or {}
    print(error.get("message", "转录未成功；详情见 evidence.json。"), file=sys.stderr)
    guidance = _local_media_fallback_message(error.get("category"))
    if guidance and evidence.get("asr_attempt", {}).get("status") in {"not_run", "not_started"}:
        print(guidance, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
