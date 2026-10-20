"""Local media inspection and human-readable output formatting."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import AudioMetadata, RunEvidence, Segment


SUPPORTED_MEDIA_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".opus",
}
DECODE_SAMPLE_RATE = 16_000
DEFAULT_MAX_AUDIO_DURATION_SECONDS = 30 * 60
MAX_AUDIO_DURATION_SECONDS = 4 * 60 * 60


def escape_markdown_text(value: str) -> str:
    """Render untrusted transcript text inertly while evidence keeps it raw."""
    escaped = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", escaped)


def _write_new_text_file(path: Path, text: str) -> None:
    """Create a text file without replacing an existing path, including a symlink.

    A private random temporary file is prepared first. Hard-link creation is an
    atomic no-replace operation on supported local filesystems; the O_EXCL
    fallback keeps the no-overwrite guarantee on filesystems without hard links.
    """
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            raise FileExistsError(f"输出文件已存在，拒绝覆盖：{path.name}") from None
        except OSError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            try:
                output_fd = os.open(path, flags, 0o600)
            except FileExistsError:
                raise FileExistsError(f"输出文件已存在，拒绝覆盖：{path.name}") from None
            try:
                with os.fdopen(output_fd, "wb") as destination, temporary_path.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
            except Exception:
                path.unlink(missing_ok=True)
                raise
    finally:
        temporary_path.unlink(missing_ok=True)


class AudioInputError(ValueError):
    """Input does not contain a supported, decodable local audio stream."""


@dataclass(frozen=True, slots=True)
class InspectedAudio:
    path: Path
    metadata: AudioMetadata
    samples: Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_audio(
    path: str | Path,
    *,
    max_duration_seconds: float = DEFAULT_MAX_AUDIO_DURATION_SECONDS,
) -> InspectedAudio:
    """Inspect and decode the first audio stream without writing media files.

    faster-whisper's own local decoder creates the in-memory array used by ASR.
    Duration is measured from that array. Container duration is stored separately
    and never used to validate ASR segment coverage.
    """
    candidate = Path(path).expanduser()
    if (
        isinstance(max_duration_seconds, bool)
        or not isinstance(max_duration_seconds, (int, float))
        or (
            isinstance(max_duration_seconds, float)
            and not math.isfinite(max_duration_seconds)
        )
        or max_duration_seconds <= 0
    ):
        raise ValueError("max_duration_seconds must be a finite positive number")
    if max_duration_seconds > MAX_AUDIO_DURATION_SECONDS:
        raise ValueError(
            f"max_duration_seconds must not exceed {MAX_AUDIO_DURATION_SECONDS}"
        )
    if not candidate.exists():
        raise AudioInputError(f"媒体文件不存在：{candidate}")
    if not candidate.is_file():
        raise AudioInputError(f"输入必须是本地媒体文件：{candidate}")
    if candidate.suffix.casefold() not in SUPPORTED_MEDIA_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_MEDIA_SUFFIXES))
        raise AudioInputError(f"不支持的媒体类型 `{candidate.suffix or '(无扩展名)'}`；支持：{allowed}")

    resolved = candidate.resolve(strict=True)
    try:
        with resolved.open("rb") as source:
            if not source.read(1):
                raise AudioInputError("媒体文件为空")
    except OSError as exc:
        raise AudioInputError(f"无法读取媒体文件：{exc}") from exc
    source_hash = _sha256(resolved)

    try:
        import av

        with av.open(str(resolved), mode="r", metadata_errors="ignore") as container:
            audio_streams = list(container.streams.audio)
            if not audio_streams:
                raise AudioInputError("媒体容器中没有音轨，无法转录")

            stream = audio_streams[0]
            codec_context = stream.codec_context
            codec_name = codec_context.codec.name if codec_context.codec else None
            source_sample_rate = int(codec_context.sample_rate or stream.rate or 0)
            layout = codec_context.layout
            source_channels = int(layout.nb_channels) if layout else 0
            source_sample_format = codec_context.format.name if codec_context.format else None
            sample_width = None
            if codec_name and codec_name.startswith("pcm_") and codec_context.format:
                sample_width = int(codec_context.format.bytes)

            container_duration = (
                float(container.duration / av.time_base)
                if container.duration is not None
                else None
            )
            stream_duration = (
                float(stream.duration * stream.time_base)
                if stream.duration is not None and stream.time_base is not None
                else None
            )
            declared_duration = stream_duration or container_duration
            if (
                max_duration_seconds is not None
                and declared_duration is not None
                and declared_duration > max_duration_seconds
            ):
                raise AudioInputError(
                    f"媒体声明时长 {declared_duration:.3f} 秒，超过限制 {max_duration_seconds:.3f} 秒"
                )
            if max_duration_seconds is not None:
                decoded_duration = 0.0
                for frame in container.decode(stream):
                    frame_rate = int(frame.sample_rate or source_sample_rate)
                    if frame_rate <= 0 or frame.samples < 0:
                        raise AudioInputError("下载媒体包含无效的音频帧时长")
                    decoded_duration += frame.samples / frame_rate
                    if decoded_duration > max_duration_seconds:
                        raise AudioInputError(
                            f"媒体实际音轨超过限制 {max_duration_seconds:.3f} 秒；已在完整解码前停止"
                        )
            has_video = bool(container.streams.video)
            container_format = container.format.name if container.format else None
            stream_index = int(stream.index)
        from faster_whisper.audio import decode_audio

        samples = decode_audio(str(resolved), sampling_rate=DECODE_SAMPLE_RATE)
        decoded_sample_count = int(samples.size)
        if decoded_sample_count == 0:
            raise AudioInputError("媒体包含音轨，但没有可解码的音频帧")
        audio_duration = decoded_sample_count / DECODE_SAMPLE_RATE
        if max_duration_seconds is not None and audio_duration > max_duration_seconds:
            raise AudioInputError(
                f"解码音频时长 {audio_duration:.3f} 秒，超过限制 {max_duration_seconds:.3f} 秒"
            )
        if _sha256(resolved) != source_hash:
            raise AudioInputError("音频解码期间输入媒体发生变化，请重试")
        metadata = AudioMetadata(
            file_name=resolved.name,
            sha256=source_hash,
            duration_seconds=audio_duration,
            sample_rate_hz=source_sample_rate,
            channels=source_channels,
            sample_width_bytes=sample_width,
            media_type="video" if has_video else "audio",
            container_format=container_format,
            container_duration_seconds=container_duration,
            stream_duration_seconds=stream_duration,
            audio_stream_index=stream_index,
            audio_codec=codec_name,
            audio_sample_format=source_sample_format,
            decoded_sample_rate_hz=DECODE_SAMPLE_RATE,
            decoded_channels=1,
            transcription_source=(
                f"faster_whisper.first_audio_stream(ordinal=0,container_index={stream_index});"
                "in_memory_float32_mono_16000hz"
            ),
        )
    except AudioInputError:
        raise
    except Exception as exc:
        raise AudioInputError(f"无法解码媒体中的音频流：{type(exc).__name__}: {exc}") from exc

    if metadata.duration_seconds <= 0 or metadata.sample_rate_hz <= 0:
        raise AudioInputError("媒体音轨没有有效时长或采样率")

    return InspectedAudio(path=resolved, metadata=metadata, samples=samples)


def format_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def render_markdown(
    audio: AudioMetadata,
    model_name: str,
    language: str,
    segments: Iterable[Segment],
    *,
    quality_notice: str | None = None,
) -> str:
    lines = [
        f"# {escape_markdown_text(audio.file_name)} 转录稿",
        "",
        f"- 模型：`{model_name.replace('`', 'ˋ')}`",
        f"- 语言：`{language}`",
        f"- 音频时长：`{audio.duration_seconds:.3f}` 秒",
        f"- 输入 SHA256：`{audio.sha256}`",
        "- 原始识别文本与参数以同目录 `evidence.json` 为准。",
        "",
        "---",
        "",
    ]
    if quality_notice:
        lines.extend([f"> 转录质量提示：{escape_markdown_text(quality_notice)}", ""])
    for segment in segments:
        lines.extend([
            f"### {format_timestamp(segment.start)} – {format_timestamp(segment.end)}",
            "",
            escape_markdown_text(segment.text),
            "",
        ])
    return "\n".join(lines)


def write_success_artifacts(
    output_dir: Path,
    markdown: str,
    evidence: RunEvidence,
) -> tuple[Path, Path]:
    """Write both files atomically enough for a local run and roll back partial success."""
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = output_dir / "transcript.md"
    evidence_path = output_dir / "evidence.json"
    if transcript_path.exists() or evidence_path.exists():
        raise FileExistsError(
            f"输出目录已包含转录结果，拒绝覆盖：{output_dir}。请指定一个新的空目录。"
        )

    created: list[Path] = []
    try:
        _write_new_text_file(transcript_path, markdown)
        created.append(transcript_path)
        _write_new_text_file(
            evidence_path,
            json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        created.append(evidence_path)
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return transcript_path, evidence_path


def write_failure_evidence(output_dir: Path, evidence: RunEvidence) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / "evidence.json"
    transcript_path = output_dir / "transcript.md"
    if evidence_path.exists() or transcript_path.exists():
        raise FileExistsError(
            f"输出目录已包含转录结果，拒绝覆盖：{output_dir}。请指定一个新的空目录。"
        )
    _write_new_text_file(
        evidence_path,
        json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    return evidence_path
