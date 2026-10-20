"""Offline CPU ASR orchestration; raw model segments remain the source of truth."""

from __future__ import annotations

import os

# This program never downloads models or contacts remote inference services.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import platform
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from faster_whisper import WhisperModel

from . import __version__
from .artifacts import (
    AudioInputError,
    DEFAULT_MAX_AUDIO_DURATION_SECONDS,
    inspect_audio,
    render_markdown,
    write_failure_evidence,
    write_success_artifacts,
)
from .models import RunConfig, RunEvidence, Segment, SegmentValidationError, validate_segment_sequence


SUPPORTED_CACHED_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
    "turbo",
}

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class EngineError(RuntimeError):
    """Expected user-facing N0 error."""


class ModelSelectionError(EngineError):
    pass


class EngineExecutionError(EngineError):
    pass


class OutputConflictError(EngineError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def resolve_model_reference(model: str) -> tuple[str, str]:
    """Accept a standard cached model alias or an existing local model directory only."""
    value = model.strip()
    if not value:
        raise ModelSelectionError("必须显式指定本地 faster-whisper 模型")
    candidate = Path(value).expanduser()
    if candidate.exists():
        if not candidate.is_dir():
            raise ModelSelectionError(f"模型路径不是目录：{candidate}")
        return str(candidate.resolve(strict=True)), "local_directory"
    if value in SUPPORTED_CACHED_MODELS:
        return value, "local_hub_cache_alias"
    if "/" in value or "\\" in value or candidate.is_absolute():
        raise ModelSelectionError(f"本地模型目录不存在：{candidate}")
    raise ModelSelectionError(
        f"不支持模型标识 `{value}`。请使用已缓存的标准模型名（例如 small）或已存在的本地模型目录。"
    )


def _model_revision(reference: str, model_kind: str) -> dict[str, Any]:
    """Resolve a locally pinned Hugging Face snapshot without contacting the Hub."""
    if model_kind == "local_directory":
        local_path = Path(reference)
        if (
            local_path.parent.name == "snapshots"
            and re.fullmatch(r"[0-9a-f]{40}", local_path.name)
            and (local_path / "model.bin").is_file()
        ):
            return {
                "status": "resolved",
                "value": local_path.name,
                "source": "local_huggingface_snapshot_path",
            }
        return {
            "status": "unknown",
            "value": None,
            "source": "local_model_directory",
            "reason": "local_directory_has_no_reliable_revision_marker",
        }

    try:
        from huggingface_hub import constants

        repo_dir = Path(constants.HF_HUB_CACHE) / f"models--Systran--faster-whisper-{reference}"
        ref_path = repo_dir / "refs" / "main"
        revision = ref_path.read_text(encoding="utf-8").strip()
        snapshot_dir = repo_dir / "snapshots" / revision
        if (
            re.fullmatch(r"[0-9a-f]{40}", revision)
            and snapshot_dir.is_dir()
            and (snapshot_dir / "model.bin").is_file()
        ):
            return {
                "status": "resolved",
                "value": revision,
                "source": "local_huggingface_cache_ref_main",
            }
    except (ImportError, OSError, ValueError):
        pass
    return {
        "status": "unknown",
        "value": None,
        "source": "local_huggingface_cache",
        "reason": "no_complete_cached_main_snapshot_revision",
    }


def _model_factory(
    reference: str,
    *,
    cpu_threads: int,
    revision: str | None = None,
) -> WhisperModel:
    options: dict[str, Any] = {
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": cpu_threads,
        "num_workers": 1,
        "local_files_only": True,
    }
    if revision:
        options["revision"] = revision
    return WhisperModel(reference, **options)


def _cpu_model() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
                if value:
                    return value
        except (ImportError, OSError):
            pass
    return platform.processor() or platform.uname().processor or platform.machine() or "unknown"


def _parameters(config: RunConfig) -> dict[str, Any]:
    return {
        "task": "transcribe",
        "language": None if config.language == "auto" else config.language,
        "beam_size": config.beam_size,
        "best_of": config.best_of,
        "temperature": config.temperature,
        "vad_filter": False,
        "initial_prompt": None,
        "condition_on_previous_text": False,
        "word_timestamps": False,
        "without_timestamps": False,
    }


def _new_evidence(
    *,
    audio: Any,
    requested_model: str,
    model_kind: str,
    config: RunConfig,
    run_id: str,
    started_at: str,
) -> RunEvidence:
    return RunEvidence(
        schema_version="1.1",
        run_id=run_id,
        status="running",
        started_at_utc=started_at,
        finished_at_utc=None,
        audio=audio.metadata,
        model_requested=requested_model,
        model_kind=model_kind,
        device="cpu",
        compute_type="int8",
        cpu_model=_cpu_model(),
        cpu_threads=config.cpu_threads,
        num_workers=1,
        language_requested=config.language,
        language_detected=None,
        language_probability=None,
        parameters=_parameters(config),
        software={
            "application": "video-transcript-workbench",
            "application_version": __version__,
            "python": sys.version.split()[0],
            "faster_whisper": _package_version("faster-whisper"),
            "ctranslate2": _package_version("ctranslate2"),
            "pyav": _package_version("av"),
        },
        model_revision={},
    )


def transcribe_to_artifacts(
    audio_path: str | Path,
    *,
    config: RunConfig,
    output_dir: str | Path,
    max_duration_seconds: float = DEFAULT_MAX_AUDIO_DURATION_SECONDS,
    model_factory: Callable[..., Any] | None = None,
    run_id: str | None = None,
) -> tuple[Path, Path, RunEvidence]:
    """Transcribe one local media file and write transcript.md plus evidence.json."""
    current_run_id = uuid.uuid4().hex if run_id is None else run_id
    if not isinstance(current_run_id, str) or not _SAFE_RUN_ID.fullmatch(current_run_id):
        raise ValueError("run_id must contain only 1–64 letters, numbers, underscores, or hyphens")

    run_started = time.perf_counter()
    destination = Path(output_dir).expanduser().resolve()
    transcript_path = destination / "transcript.md"
    evidence_path = destination / "evidence.json"
    if transcript_path.exists() or evidence_path.exists():
        raise OutputConflictError(
            f"输出目录已包含 N0 结果，拒绝覆盖：{destination}。请指定一个新的空目录。"
        )

    decode_started = time.perf_counter()
    audio = inspect_audio(audio_path, max_duration_seconds=max_duration_seconds)
    media_decode_seconds = time.perf_counter() - decode_started
    model_reference, model_kind = resolve_model_reference(config.model)
    destination.mkdir(parents=True, exist_ok=True)
    if transcript_path.exists() or evidence_path.exists():
        raise OutputConflictError(
            f"输出目录已包含 N0 结果，拒绝覆盖：{destination}。请指定一个新的空目录。"
        )
    if transcript_path.resolve() == audio.path or evidence_path.resolve() == audio.path:
        raise OutputConflictError("输出路径不能覆盖输入音频")

    started_at = _utc_now()
    evidence = _new_evidence(
        audio=audio,
        requested_model=config.model,
        model_kind=model_kind,
        config=config,
        run_id=current_run_id,
        started_at=started_at,
    )
    evidence.parameters["input_max_duration_seconds"] = max_duration_seconds
    revision_info = _model_revision(model_reference, model_kind)
    evidence.model_revision = revision_info
    evidence.timing["media_decode_seconds"] = round(media_decode_seconds, 3)
    factory = model_factory or _model_factory
    raw_segments: list[Segment] = []
    model_load_started = time.perf_counter()
    try:
        if factory is _model_factory:
            model = factory(
                model_reference,
                cpu_threads=config.cpu_threads,
                revision=revision_info.get("value"),
            )
        else:
            model = factory(
                model_reference,
                device="cpu",
                compute_type="int8",
                cpu_threads=config.cpu_threads,
                num_workers=1,
                local_files_only=True,
                revision=revision_info.get("value"),
            )
        model_load_seconds = time.perf_counter() - model_load_started
        inference_started = time.perf_counter()
        generated_segments, info = model.transcribe(
            audio.samples,
            **_parameters(config),
        )
        for generated in generated_segments:
            raw_segments.append(
                Segment(
                    start=float(generated.start),
                    end=float(generated.end),
                    text=generated.text,
                )
            )
        inference_seconds = time.perf_counter() - inference_started
        validate_segment_sequence(raw_segments, audio.metadata.duration_seconds)
        if not raw_segments:
            raise EngineExecutionError("模型完成运行但没有返回任何转录分段")

        if _sha256_after_transcription(audio.path) != audio.metadata.sha256:
            raise EngineExecutionError("转录期间输入媒体发生变化；拒绝生成成功结果")

        evidence.status = "success"
        evidence.finished_at_utc = _utc_now()
        evidence.language_detected = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        evidence.language_probability = float(probability) if probability is not None else None
        evidence.segments = raw_segments
        evidence.timing = {
            "media_decode_seconds": round(media_decode_seconds, 3),
            "model_load_seconds": round(model_load_seconds, 3),
            "inference_seconds": round(inference_seconds, 3),
            "total_seconds": round(time.perf_counter() - run_started, 3),
            "rtf": round(inference_seconds / audio.metadata.duration_seconds, 4),
        }
        markdown = render_markdown(
            audio.metadata,
            config.model,
            evidence.language_detected or config.language,
            raw_segments,
        )
        try:
            transcript_path, evidence_path = write_success_artifacts(
                destination,
                markdown,
                evidence,
            )
        except FileExistsError as exc:
            raise OutputConflictError(str(exc)) from exc
        return transcript_path, evidence_path, evidence
    except Exception as exc:
        if isinstance(exc, OutputConflictError):
            raise
        # No error path may leave success or running status in the evidence.
        evidence.status = "failed"
        evidence.finished_at_utc = _utc_now()
        evidence.segments = raw_segments
        evidence.error = {"type": type(exc).__name__, "message": str(exc)}
        evidence.timing = {
            "media_decode_seconds": round(media_decode_seconds, 3),
            "model_load_seconds": round(time.perf_counter() - model_load_started, 3),
            "total_seconds": round(time.perf_counter() - run_started, 3),
        }
        saved_path: Path | None = None
        try:
            saved_path = write_failure_evidence(destination, evidence)
        except FileExistsError as save_exc:
            raise OutputConflictError(
                f"输出目录已有结果，拒绝覆盖：{destination}"
            ) from save_exc
        except Exception as save_exc:
            raise EngineExecutionError(
                f"转录失败：{exc}。失败证据也无法保存：{save_exc}"
            ) from exc
        if isinstance(exc, (AudioInputError, ModelSelectionError)):
            raise
        raise EngineExecutionError(
            f"转录失败：{exc}。详情已写入 `{saved_path}`；未生成 transcript.md。"
        ) from exc


def _sha256_after_transcription(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
