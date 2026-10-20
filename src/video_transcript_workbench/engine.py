"""Offline ASR orchestration; raw model segments remain the source of truth."""

from __future__ import annotations

import os
from math import isfinite

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
from .device import DeviceSelectionError, resolve_device
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
_NO_SPEECH_THRESHOLD = 0.6


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


def validate_managed_model_is_prepared(model: str) -> None:
    """Fail early with explicit next steps for managed aliases, without network access."""
    from .model_store import PREPARABLE_MODELS, inspect_model

    if model not in PREPARABLE_MODELS:
        return
    try:
        status = inspect_model(model)
    except Exception:
        raise ModelSelectionError(
            "无法读取本地模型缓存。请检查安装环境，再运行 `video-transcript doctor`。"
        ) from None
    if status.status == "ready":
        return
    verify = f"video-transcript models verify {model}"
    prepare = f"video-transcript models prepare {model}"
    if status.status == "incomplete":
        raise ModelSelectionError(
            f"模型 `{model}` 的本地缓存不完整。请先运行 `{verify}`；如仍缺少文件，再明确运行 `{prepare}`。"
        )
    raise ModelSelectionError(
        f"模型 `{model}` 尚未准备。请先明确运行 `{prepare}`，然后运行 `{verify}`；此命令不会自动下载模型。"
    )


def _cuda_failure_category(error: Exception) -> str:
    message = str(error).casefold()
    if "out of memory" in message or "cuda_error_out_of_memory" in message:
        return "cuda_out_of_memory"
    if any(token in message for token in ("cuda", "cudnn", "cublas", "gpu", "no kernel image", "compute type")):
        return "cuda_runtime_or_kernel_failure"
    return "cuda_inference_failure"


def _is_cuda_runtime_failure(error: Exception) -> bool:
    message = str(error).casefold()
    return any(
        token in message
        for token in (
            "cuda",
            "cudnn",
            "cublas",
            "out of memory",
            "no kernel image",
            "compute type",
            "gpu",
        )
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
    device: str,
    compute_type: str,
    cpu_threads: int,
    num_workers: int = 1,
    local_files_only: bool = True,
    revision: str | None = None,
) -> WhisperModel:
    options: dict[str, Any] = {
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": cpu_threads,
        "num_workers": num_workers,
        "local_files_only": local_files_only,
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
        "vad_filter": config.vad_filter,
        "initial_prompt": None,
        "condition_on_previous_text": False,
        "no_speech_threshold": _NO_SPEECH_THRESHOLD,
        "word_timestamps": False,
        "without_timestamps": False,
    }


def _warning_segment_indices(warning: dict[str, Any]) -> set[int]:
    indices = {int(index) for index in warning.get("segment_indices", [])}
    for group in warning.get("segment_index_groups", []):
        indices.update(int(index) for index in group)
    for item in warning.get("segments", []):
        if isinstance(item, dict) and isinstance(item.get("segment_index"), int):
            indices.add(item["segment_index"])
    return indices


def _assess_transcription_quality(
    segments: list[Segment],
    no_speech_probabilities: list[float | None],
    *,
    vad_filter: bool,
    audio_duration_seconds: float,
) -> dict[str, Any]:
    """Describe model-native risk signals without editing or dropping raw text."""
    nonempty_indices = [index for index, item in enumerate(segments) if item.text.strip()]
    warnings: list[dict[str, Any]] = []
    if not nonempty_indices:
        if vad_filter:
            return {
                "assessment": "no_speech_detected",
                "method": "faster_whisper_silero_vad",
                "warnings": [{"code": "vad_returned_no_speech_segments"}],
                "review_scope": "run",
                "affected_segment_indices": [],
                "affected_segment_count": 0,
                "note": "VAD 未返回语音段；这表示未检测到语音，不证明音频中绝对没有语音。",
            }
        return {
            "assessment": "uncertain",
            "method": "asr_without_vad",
            "warnings": [{"code": "model_returned_no_usable_text_without_vad"}],
            "review_scope": "run",
            "affected_segment_indices": [],
            "affected_segment_count": 0,
            "note": "模型未返回可用文字；仅凭此结果无法区分无语音与漏识别。",
        }

    flagged_indices = [
        index
        for index, probability in enumerate(no_speech_probabilities)
        if probability is not None and probability >= _NO_SPEECH_THRESHOLD
    ]
    if flagged_indices:
        warnings.append({
            "code": "model_no_speech_probability_at_threshold",
            "threshold": _NO_SPEECH_THRESHOLD,
            "segment_indices": flagged_indices,
            "probabilities": [round(no_speech_probabilities[index], 4) for index in flagged_indices],
        })

    out_of_range = [
        {
            "segment_index": index,
            "start": round(item.start, 3),
            "end": round(item.end, 3),
        }
        for index, item in enumerate(segments)
        if item.start >= audio_duration_seconds or item.end > audio_duration_seconds
    ]
    if out_of_range:
        warnings.append({
            "code": "segment_timestamp_outside_audio",
            "audio_duration_seconds": round(audio_duration_seconds, 3),
            "segments": out_of_range,
        })

    duplicate_groups: dict[str, list[int]] = {}
    for index in nonempty_indices:
        comparison_text = " ".join(segments[index].text.split()).casefold()
        if comparison_text:
            duplicate_groups.setdefault(comparison_text, []).append(index)
    repeated_groups = [indices for indices in duplicate_groups.values() if len(indices) > 1]
    if repeated_groups:
        warnings.append({
            "code": "identical_segment_text_repeated",
            "segment_index_groups": repeated_groups,
        })

    assessment = "uncertain" if warnings else "text_available"
    affected_indices = sorted({
        index
        for warning in warnings
        for index in _warning_segment_indices(warning)
    })
    has_unlocated_warning = any(not _warning_segment_indices(warning) for warning in warnings)
    review_scope = "segments" if affected_indices and not has_unlocated_warning else (
        "run" if warnings else "none"
    )
    if warnings and review_scope == "segments":
        note = (
            "检测到局部分段信号；Evidence 的 affected_segment_indices 使用从 0 开始的索引。"
            "请优先复核这些分段；其他分段不会因此自动判错。原始文本和时间戳均已保留。"
        )
    elif warnings:
        note = "检测到需要复核的整体质量信号；请结合 Evidence 检查。原始分段已保留，不会自动删除或改写文本。"
    else:
        note = "模型返回非空文字；这不是准确率评估或人工校订结论。"
    return {
        "assessment": assessment,
        "method": "faster_whisper_segment_signals",
        "warnings": warnings,
        "review_scope": review_scope,
        "affected_segment_indices": affected_indices,
        "affected_segment_count": len(affected_indices),
        "note": note,
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
        schema_version="1.3",
        run_id=run_id,
        status="running",
        started_at_utc=started_at,
        finished_at_utc=None,
        audio=audio.metadata,
        model_requested=requested_model,
        model_kind=model_kind,
        device="unresolved",
        compute_type="unresolved",
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
        device_requested=config.device,
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
    model_load_seconds = 0.0

    def load_model(device: str, compute_type: str) -> Any:
        return factory(
            model_reference,
            device=device,
            compute_type=compute_type,
            cpu_threads=config.cpu_threads,
            num_workers=1,
            local_files_only=True,
            revision=revision_info.get("value"),
        )

    def select_cpu_fallback(error: Exception, *, phase: str) -> Any:
        nonlocal model_load_seconds
        category = _cuda_failure_category(error)
        evidence.device_selection = {
            **evidence.device_selection,
            "fallback": {
                "from_device": "cuda",
                "to_device": "cpu",
                "phase": phase,
                "reason": category,
                "error_type": type(error).__name__,
            },
        }
        evidence.device = "cpu"
        evidence.compute_type = "int8"
        print(
            "警告：CUDA 初始化或推理失败，将用同一模型回退到 CPU/int8；"
            "大模型可能耗时较长，且不会自动换成较小模型。",
            file=sys.stderr,
            flush=True,
        )
        started = time.perf_counter()
        try:
            fallback_model = load_model("cpu", "int8")
        except Exception as cpu_error:
            model_load_seconds += time.perf_counter() - started
            evidence.device_selection["fallback"]["cpu_fallback_error_type"] = type(cpu_error).__name__
            raise EngineExecutionError(
                "CUDA 初始化或推理失败，且原模型的 CPU/int8 回退也失败；未更换模型。"
            ) from cpu_error
        model_load_seconds += time.perf_counter() - started
        return fallback_model

    try:
        try:
            plan = resolve_device(config.device)
        except DeviceSelectionError:
            evidence.device_selection = {"reason": "requested_device_unavailable"}
            raise
        evidence.device = plan.device
        evidence.compute_type = plan.compute_type
        evidence.device_selection = plan.selection
        try:
            model = load_model(plan.device, plan.compute_type)
        except Exception as exc:
            model_load_seconds += time.perf_counter() - model_load_started
            if config.device != "auto" or plan.device != "cuda" or not _is_cuda_runtime_failure(exc):
                if plan.device == "cuda":
                    raise EngineExecutionError(
                        "CUDA 模型初始化失败；详细运行环境信息已省略。"
                    ) from exc
                raise
            model = select_cpu_fallback(exc, phase="model_initialization")
        else:
            model_load_seconds += time.perf_counter() - model_load_started

        def transcribe_loaded_model() -> tuple[list[Segment], Any, float, list[float | None]]:
            collected: list[Segment] = []
            no_speech_probabilities: list[float | None] = []
            started = time.perf_counter()
            generated_segments, result_info = model.transcribe(
                audio.samples,
                **_parameters(config),
            )
            for generated in generated_segments:
                collected.append(
                    Segment(
                        start=float(generated.start),
                        end=float(generated.end),
                        text=generated.text,
                    )
                )
                probability = getattr(generated, "no_speech_prob", None)
                try:
                    probability = float(probability) if probability is not None else None
                except (TypeError, ValueError, OverflowError):
                    probability = None
                if probability is not None and (
                    not isfinite(probability) or not 0.0 <= probability <= 1.0
                ):
                    probability = None
                no_speech_probabilities.append(probability)
            return collected, result_info, time.perf_counter() - started, no_speech_probabilities

        try:
            raw_segments, info, inference_seconds, no_speech_probabilities = transcribe_loaded_model()
        except Exception as exc:
            if config.device != "auto" or plan.device != "cuda" or not _is_cuda_runtime_failure(exc):
                if plan.device == "cuda":
                    raise EngineExecutionError(
                        "CUDA 推理失败；详细运行环境信息已省略。"
                    ) from exc
                raise
            import gc

            del model
            gc.collect()
            model = select_cpu_fallback(exc, phase="inference")
            raw_segments, info, inference_seconds, no_speech_probabilities = transcribe_loaded_model()
        # Keep every raw model timestamp. An overrun is a quality warning, not
        # a decoding failure; ordering and numeric validity still remain strict.
        validation_duration = max(
            audio.metadata.duration_seconds,
            max((segment.end for segment in raw_segments), default=audio.metadata.duration_seconds),
        )
        validate_segment_sequence(raw_segments, validation_duration)

        if _sha256_after_transcription(audio.path) != audio.metadata.sha256:
            raise EngineExecutionError("转录期间输入媒体发生变化；拒绝生成成功结果")

        evidence.quality = _assess_transcription_quality(
            raw_segments,
            no_speech_probabilities,
            vad_filter=config.vad_filter,
            audio_duration_seconds=audio.metadata.duration_seconds,
        )
        assessment = evidence.quality["assessment"]
        evidence.status = "success" if assessment == "text_available" else assessment
        evidence.finished_at_utc = _utc_now()
        evidence.language_detected = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        try:
            normalized_probability = float(probability) if probability is not None else None
        except (TypeError, ValueError, OverflowError):
            normalized_probability = None
        if normalized_probability is not None and (
            not isfinite(normalized_probability)
            or not 0.0 <= normalized_probability <= 1.0
        ):
            normalized_probability = None
        evidence.language_probability = normalized_probability
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
            quality_notice=(
                evidence.quality.get("note")
                if assessment in {"uncertain", "no_speech_detected"}
                else None
            ),
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
        evidence.quality = {
            "assessment": "model_or_decode_failure",
            "warnings": [],
            "note": "模型初始化、推理或媒体处理未完成；请查看 error 字段。",
        }
        evidence.segments = raw_segments
        safe_error_message = str(exc)
        if _is_cuda_runtime_failure(exc):
            safe_error_message = "CUDA 初始化或推理失败；详细运行环境信息已省略。"
        evidence.error = {"type": type(exc).__name__, "message": safe_error_message}
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
            f"转录失败：{safe_error_message}。详情已写入 `{saved_path}`；未生成 transcript.md。"
        ) from exc


def _sha256_after_transcription(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
