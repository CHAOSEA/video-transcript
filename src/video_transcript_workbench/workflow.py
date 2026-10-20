"""Subtitle-first online transcription with bounded local ASR fallback."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .artifacts import AudioInputError, _sha256, format_timestamp
from .download import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_DURATION_SECONDS,
    DownloadError,
    DownloadedMedia,
    _inspect_download,
    _safe_file_in_directory,
    download_audio_to_directory,
    runtime_evidence,
    validate_download_limits,
)
from .engine import (
    ModelSelectionError,
    resolve_model_reference,
    validate_managed_model_is_prepared,
    transcribe_to_artifacts,
)
from .models import RunConfig, Segment, validate_segment_sequence
from .online import (
    _carry_xhs_request_token,
    OnlineInputError,
    UnsafeNetworkTarget,
    _render_online_markdown,
    _escape_caption_for_markdown,
    _safe_title,
    _version,
    _write_online_artifacts,
    normalize_video_url,
    retrieve_subtitle_attempt,
    validate_proxy_url,
)


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
FALLBACK_STATUSES = {
    "no_available_subtitles",
    "no_matching_language",
    "empty_subtitles",
    "unsupported_subtitle_format",
}
SUSPECT_SUBTITLE_STATUSES = {"incomplete_subtitles", "subtitle_parse_failed"}


@dataclass(frozen=True, slots=True)
class FallbackDecision:
    allowed: bool
    reason: str
    policy: str


def decide_asr_fallback(
    subtitle_status: str,
    *,
    fallback_on_suspect: bool = False,
) -> FallbackDecision:
    """Return a deterministic policy decision; network/access errors never retry."""
    if subtitle_status == "subtitle_success":
        return FallbackDecision(False, "subtitle_success", "subtitle_only")
    if subtitle_status in FALLBACK_STATUSES:
        return FallbackDecision(True, subtitle_status, "safe_missing_subtitles")
    if subtitle_status in SUSPECT_SUBTITLE_STATUSES and fallback_on_suspect:
        return FallbackDecision(True, subtitle_status, "explicit_suspect_fallback")
    if subtitle_status in SUSPECT_SUBTITLE_STATUSES:
        return FallbackDecision(False, subtitle_status, "stop_on_suspect_subtitles")
    return FallbackDecision(False, subtitle_status, "stop_on_platform_or_input_failure")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _segment_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    segments: list[Segment] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("ASR returned a malformed segment record")
        segments.append(Segment(float(item["start"]), float(item["end"]), item["text"]))
    return [segment.to_dict() for segment in segments]


def _asr_evidence_from_result(result: Any, scratch_dir: Path) -> dict[str, Any]:
    evidence: Any = None
    if isinstance(result, tuple) and result:
        candidate = result[-1]
        evidence = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
    elif hasattr(result, "to_dict"):
        evidence = result.to_dict()
    elif isinstance(result, dict):
        evidence = result
    if not isinstance(evidence, dict):
        evidence_path = scratch_dir / "evidence.json"
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise ValueError("本地 ASR 没有返回结构化运行证据")
    return evidence


def _render_workflow_markdown(
    *,
    title: str | None,
    platform: str,
    video_id: str,
    language: str,
    source_label: str,
    subtitle_status: str,
    segments: list[Segment],
    model: dict[str, Any] | None,
    quality_notice: str | None = None,
) -> str:
    safe_title = (title or "（平台未提供标题）").replace("`", "ˋ")
    lines = [
        "# 视频转录稿",
        "",
        f"- 标题：`{safe_title}`",
        f"- 平台：`{platform}`",
        f"- 视频标识：`{video_id}`",
        f"- 最终来源：`{source_label}`",
        f"- 字幕尝试：`{subtitle_status}`",
        f"- 转录语言：`{language}`",
    ]
    if model:
        lines.extend([
            f"- 模型：`{model.get('requested', 'unknown')}`",
            f"- 设备/精度：`{model.get('device', 'unknown')} / {model.get('compute_type', 'unknown')}`",
        ])
    if quality_notice:
        lines.append(f"- 转录质量提示：`{quality_notice.replace('`', 'ˋ')}`")
        lines.append("- 原始 ASR 分段保留，质量信号需要人工复核。")
    lines.extend(["- 原始分段和各阶段详情以同目录 `evidence.json` 为准。", "", "---", ""])
    for segment in segments:
        lines.extend([
            f"### {format_timestamp(segment.start)} – {format_timestamp(segment.end)}",
            "",
            # Keep raw text in evidence.json; escape Markdown control characters here.
            _escape_caption_for_markdown(segment.text),
            "",
        ])
    return "\n".join(lines)


def _validate_private_media(
    media: DownloadedMedia,
    directory: Path,
    max_bytes: int,
    max_duration: int,
    *,
    allow_muxed_mp4: bool = False,
) -> dict[str, Any]:
    path = _safe_file_in_directory(
        Path(media.path), directory, allow_muxed_mp4=allow_muxed_mp4
    )
    size = path.stat().st_size
    if size <= 0 or size > max_bytes:
        raise DownloadError("下载媒体大小为空或超过配置上限", category="download_limit_exceeded")
    duration, container_format, codec_name, has_video_stream = _inspect_download(path)
    if path.suffix.casefold() == ".mp4" and not has_video_stream:
        raise DownloadError("MP4 回退结果不含视频流", category="unsupported_media")
    if duration > max_duration:
        raise DownloadError("实际音轨超过配置时长上限", category="download_limit_exceeded")
    digest = _sha256(path)
    if digest != media.sha256:
        raise DownloadError("下载媒体在校验后发生变化", category="download_integrity_error")
    return {
        "extension": path.suffix.casefold(),
        "size_bytes": size,
        "sha256": digest,
        "duration_seconds": duration,
        "container_format": container_format,
        "audio_codec": codec_name,
        "has_video_stream": has_video_stream,
        "media_kind": "video_with_audio" if has_video_stream else "audio_only",
        "extractor": media.extractor,
        "yt_dlp_version": media.yt_dlp_version,
        "download_strategy": media.download_strategy,
        "fallback_reason": media.fallback_reason,
    }


def run_link_transcription(
    url: str,
    *,
    language: str,
    config: RunConfig | None,
    output_dir: str | Path,
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    fallback_on_suspect: bool = False,
    subtitle_client_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    download_adapter: Callable[..., DownloadedMedia] | None = None,
    asr_runner: Callable[..., Any] | None = None,
    run_id: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Try subtitles first; download and run local ASR only for allowed states.

    All media and intermediate N1 outputs live in a private temporary directory.
    Only the final evidence/transcript pair is written to ``output_dir``.
    """
    if not isinstance(language, str) or language not in {"zh", "en", "ja"}:
        raise ValueError("language must be one of: zh, en, ja")
    proxy = validate_proxy_url(proxy)
    validate_download_limits(max_duration_seconds, max_download_bytes)
    current_run_id = uuid.uuid4().hex if run_id is None else run_id
    if not isinstance(current_run_id, str) or not _SAFE_RUN_ID.fullmatch(current_run_id):
        raise ValueError("run_id must contain only letters, numbers, underscores, or hyphens")

    destination = Path(output_dir).expanduser().resolve()
    if (destination / "evidence.json").exists() or (destination / "transcript.md").exists():
        raise FileExistsError(f"输出目录已包含结果，拒绝覆盖：{destination}。请指定新的空目录。")

    started_at = _utc_now()
    try:
        reference = normalize_video_url(url)
    except OnlineInputError:
        reference = None
    subtitle_evidence = retrieve_subtitle_attempt(
        url,
        language=language,
        client_factory=subtitle_client_factory,
        run_id=f"{current_run_id[:52]}-subtitle",
        proxy=proxy,
    )
    source_evidence = subtitle_evidence.get("source") or {}
    resolved_url = source_evidence.get("canonical_url")
    if isinstance(resolved_url, str):
        try:
            resolved_reference = normalize_video_url(resolved_url)
        except OnlineInputError:
            resolved_reference = None
        if resolved_reference is not None and resolved_reference.video_id is not None:
            if reference is None or reference.platform == resolved_reference.platform:
                if reference is None or reference.video_id is None or reference.video_id == resolved_reference.video_id:
                    request_url = _carry_xhs_request_token(
                        reference.request_url if reference is not None else None,
                        resolved_reference.canonical_url,
                    )
                    reference = replace(
                        reference,
                        video_id=resolved_reference.video_id,
                        canonical_url=resolved_reference.canonical_url,
                        extractor_key=resolved_reference.extractor_key,
                        request_url=request_url,
                    ) if reference is not None else resolved_reference
    subtitle_status = str(subtitle_evidence.get("status", "failed"))
    decision = decide_asr_fallback(
        subtitle_status,
        fallback_on_suspect=fallback_on_suspect,
    )
    asr_evidence: dict[str, Any] | None = None
    download_evidence: dict[str, Any] | None = None
    asr_executed = False
    final_source: str | None = None
    segments: list[Segment] = []
    model_info: dict[str, Any] | None = None
    status = "failed"
    error: dict[str, str] | None = None
    actual_language = language

    if subtitle_status == "subtitle_success":
        segments = [Segment(**item) for item in subtitle_evidence["segments"]]
        status = "success"
        final_source = f"{subtitle_evidence['subtitle']['source']}_subtitles"
        actual_language = subtitle_evidence["subtitle"].get("language") or language
    elif not decision.allowed:
        error = subtitle_evidence.get("error") or {
            "category": subtitle_status,
            "message": "字幕阶段未成功，当前回退策略已停止。",
        }
    elif config is None:
        error = {
            "category": "model_required",
            "message": (
                "当前视频没有可用字幕，需要本地 ASR 才能继续。请先运行 "
                "`video-transcript models recommend --intent quality` 选择模型，"
                "再用 `--model <模型名>` 重试；未选择模型时不会下载媒体或自动选模。"
            ),
        }
        asr_evidence = {"status": "not_started", "error": error}
    else:
        try:
            resolve_model_reference(config.model)
            if asr_runner is None:
                validate_managed_model_is_prepared(config.model)
        except (ModelSelectionError, ValueError) as exc:
            asr_evidence = {
                "status": "not_started",
                "error": {"category": "model_unavailable", "message": str(exc)},
            }
            error = asr_evidence["error"]
        else:
            try:
                if reference is None:
                    raise DownloadError("URL 不受支持或不符合安全规则", category="unsupported_url")
                adapter = download_adapter or download_audio_to_directory
                runner = asr_runner or transcribe_to_artifacts
                with tempfile.TemporaryDirectory(prefix="video-transcript-workbench-") as temp_name:
                    private_dir = Path(temp_name).resolve()
                    download_options = {
                        "directory": private_dir,
                        "max_duration_seconds": max_duration_seconds,
                        "max_download_bytes": max_download_bytes,
                    }
                    if proxy is not None:
                        download_options["proxy"] = proxy
                    downloaded = adapter(
                        reference.request_url or reference.canonical_url,
                        **download_options,
                    )
                    if not isinstance(downloaded, DownloadedMedia):
                        raise DownloadError("下载适配器没有返回受校验的媒体记录")
                    download_evidence = _validate_private_media(
                        downloaded,
                        private_dir,
                        max_download_bytes,
                        max_duration_seconds,
                        allow_muxed_mp4=reference.platform in {"xiaohongshu", "youtube"},
                    )
                    asr_executed = True
                    scratch_dir = private_dir / "n1-stage"
                    scratch_dir.mkdir()
                    try:
                        result = runner(
                            downloaded.path,
                            config=config,
                            output_dir=scratch_dir,
                            max_duration_seconds=max_duration_seconds,
                        )
                        asr_evidence = _asr_evidence_from_result(result, scratch_dir)
                    except Exception as exc:
                        try:
                            asr_evidence = _asr_evidence_from_result(None, scratch_dir)
                        except Exception:
                            asr_evidence = None
                        if asr_evidence is None:
                            if isinstance(exc, AudioInputError) and "超过限制" in str(exc):
                                category = "duration_limit_exceeded"
                                safe_message = "解码前或解码后的音轨时长超过配置上限。"
                            elif isinstance(exc, AudioInputError):
                                category = "media_decode_failed"
                                safe_message = "本机无法检查或解码下载音轨。"
                            else:
                                category = "local_asr_failed"
                                safe_message = f"本地 ASR 失败：{type(exc).__name__}。"
                            asr_evidence = {
                                "status": "failed",
                                "error": {"category": category, "message": safe_message},
                            }
                    asr_status = asr_evidence.get("status")
                    if asr_status in {"success", "uncertain"}:
                        try:
                            raw_segments = _segment_dicts(asr_evidence.get("segments"))
                            duration = float(asr_evidence.get("audio", {}).get("duration_seconds") or download_evidence["duration_seconds"])
                            if duration > max_duration_seconds:
                                raise ValueError("解码后的音频超过配置时长上限")
                            segments = [Segment(**item) for item in raw_segments]
                            validate_segment_sequence(segments, duration)
                            if not segments and asr_status == "success":
                                raise ValueError("本地 ASR 成功状态没有任何分段")
                        except Exception as validation_exc:
                            asr_evidence = {
                                **asr_evidence,
                                "status": "failed",
                                "error": {
                                    "category": "asr_evidence_invalid",
                                    "message": f"本地 ASR 输出证据校验失败：{type(validation_exc).__name__}。",
                                },
                            }
                            raise
                        model_data = asr_evidence.get("model") or {}
                        runtime_data = asr_evidence.get("runtime") or {}
                        model_info = {
                            "requested": model_data.get("requested", config.model if config else None),
                            "device": runtime_data.get("device", "cpu"),
                            "compute_type": runtime_data.get("compute_type", "int8"),
                        }
                        actual_language = (asr_evidence.get("language") or {}).get("detected") or language
                        final_source = "local_asr"
                        status = asr_status
                    elif asr_status == "no_speech_detected":
                        status = "no_speech_detected"
                    else:
                        asr_status = asr_status or "failed"
                        error = asr_evidence.get("error") or {
                            "category": "local_asr_failed",
                            "message": f"本地 ASR 未成功（{asr_status}）。",
                        }
            except Exception as exc:
                category = getattr(exc, "category", "local_asr_failed" if asr_executed else "download_failed")
                if isinstance(exc, UnsafeNetworkTarget):
                    category = "unsafe_redirect_or_resource"
                    message = "下载器返回了不安全或不匹配的网络目标，已停止。"
                else:
                    message = str(exc) if isinstance(exc, (DownloadError, ModelSelectionError, ValueError)) else f"处理失败：{type(exc).__name__}。"
                error = {"category": category, "message": message}
                if asr_evidence is None:
                    asr_evidence = {
                        "status": "not_started",
                        "error": error,
                    }

    title = subtitle_evidence.get("source", {}).get("title")
    platform = subtitle_evidence.get("source", {}).get("platform")
    video_id = subtitle_evidence.get("source", {}).get("video_id")
    canonical_url = subtitle_evidence.get("source", {}).get("canonical_url")
    if reference is not None:
        platform = reference.platform
        video_id = reference.video_id
        canonical_url = reference.canonical_url

    evidence = {
        "schema_version": "3.1",
        "run_id": current_run_id,
        "status": status,
        "started_at_utc": started_at,
        "finished_at_utc": _utc_now(),
        "source": {
            "type": "online_video",
            "platform": platform,
            "video_id": video_id,
            "canonical_url": canonical_url,
            "original_url_stored": False,
            "title": _safe_title(title),
            "duration_seconds": subtitle_evidence.get("source", {}).get("duration_seconds"),
        },
        "workflow": {
            "policy": "subtitle_first_with_local_asr_fallback",
            "subtitle_attempt_status": subtitle_status,
            "subtitle_attempt_id": subtitle_evidence.get("run_id"),
            "asr_executed": asr_executed,
            "fallback": {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "policy": decision.policy,
            },
            "final_transcription_source": final_source,
        },
        "subtitle_attempt": subtitle_evidence,
        "download": download_evidence,
        "asr_attempt": asr_evidence or {"status": "not_run"},
        "quality": (
            asr_evidence.get("quality")
            if asr_evidence is not None
            else {"assessment": "not_assessed", "reason": "subtitle_path_or_asr_not_run"}
        ),
        "model": model_info,
        "language": actual_language,
        "segments": [segment.to_dict() for segment in segments],
        "extractor": {
            "name": subtitle_evidence.get("extractor", {}).get("name"),
            "yt_dlp_version": _version("yt-dlp"),
        },
        "runtime_dependencies": runtime_evidence(),
        "software": {
            "application": "video-transcript-workbench",
            "application_version": __version__,
        },
        "error": error,
    }

    markdown = None
    if status in {"success", "uncertain"} and reference is not None:
        asr_quality = (asr_evidence or {}).get("quality") or {}
        markdown = _render_workflow_markdown(
            title=_safe_title(title),
            platform=platform or reference.platform,
            video_id=video_id or reference.video_id,
            language=actual_language,
            source_label=final_source or "unknown",
            subtitle_status=subtitle_status,
            segments=segments,
            model=model_info,
            quality_notice=(
                asr_quality.get("note")
                if status == "uncertain" and isinstance(asr_quality.get("note"), str)
                else None
            ),
        )
    _write_online_artifacts(destination, evidence, markdown)
    return evidence
