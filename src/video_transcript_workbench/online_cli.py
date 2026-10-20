"""CLI entry point for metadata and subtitle-only retrieval."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .online import retrieve_subtitle_to_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-transcript-online",
        description="只获取 YouTube、Bilibili、抖音或小红书视频元信息和匹配字幕；不会下载媒体或启动 ASR。",
    )
    parser.add_argument("url", help="HTTPS YouTube、Bilibili、抖音或小红书单视频链接")
    parser.add_argument("--language", default="zh", choices=("zh", "en", "ja"))
    parser.add_argument("--output-dir", required=True, type=Path, help="一个新的输出目录")
    parser.add_argument(
        "--proxy",
        help="仅本次运行使用的 HTTP(S) 代理；不得在地址中填写用户名或密码",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence = retrieve_subtitle_to_artifacts(
            args.url,
            language=args.language,
            output_dir=args.output_dir,
            proxy=args.proxy,
        )
    except (OSError, ValueError) as exc:
        # Never echo the supplied URL or yt-dlp exception text to the console.
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"状态：{evidence['status']}")
    print(f"证据：{args.output_dir / 'evidence.json'}")
    if evidence["status"] == "subtitle_success":
        print(f"字幕：{args.output_dir / 'transcript.md'}")
        return 0
    print(evidence.get("error", {}).get("message", "未取得可用字幕。"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
