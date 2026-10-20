"""Small, dependency-free data structures for immutable ASR evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any


class SegmentValidationError(ValueError):
    """Raised when a segment cannot be trusted as a valid timeline item."""


@dataclass(frozen=True, slots=True)
class Segment:
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if not isfinite(self.start) or not isfinite(self.end):
            raise SegmentValidationError("segment times must be finite numbers")
        if self.start < 0 or self.end <= self.start:
            raise SegmentValidationError("segment must have 0 <= start < end")
        if not isinstance(self.text, str):
            raise SegmentValidationError("segment text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    file_name: str
    sha256: str
    duration_seconds: float
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int | None
    media_type: str = "audio"
    container_format: str | None = None
    container_duration_seconds: float | None = None
    stream_duration_seconds: float | None = None
    audio_stream_index: int = 0
    audio_codec: str | None = None
    audio_sample_format: str | None = None
    decoded_sample_rate_hz: int = 16_000
    decoded_channels: int = 1
    transcription_source: str = "local_media:first_audio_stream"


@dataclass(frozen=True, slots=True)
class RunConfig:
    model: str
    language: str = "zh"
    cpu_threads: int = 20
    beam_size: int = 5
    best_of: int = 5
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be explicitly selected")
        if self.language not in {"zh", "en", "ja", "auto"}:
            raise ValueError("language must be one of: zh, en, ja, auto")
        if self.cpu_threads < 1:
            raise ValueError("cpu_threads must be at least 1")
        if self.beam_size < 1 or self.best_of < 1:
            raise ValueError("beam_size and best_of must be at least 1")
        if self.temperature < 0:
            raise ValueError("temperature must not be negative")


@dataclass(slots=True)
class RunEvidence:
    schema_version: str
    run_id: str
    status: str
    started_at_utc: str
    finished_at_utc: str | None
    audio: AudioMetadata
    model_requested: str
    model_kind: str
    device: str
    compute_type: str
    cpu_model: str
    cpu_threads: int
    num_workers: int
    language_requested: str
    language_detected: str | None
    language_probability: float | None
    parameters: dict[str, Any]
    software: dict[str, Any]
    model_revision: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "audio": asdict(self.audio),
            "model": {
                "requested": self.model_requested,
                "kind": self.model_kind,
                "revision": self.model_revision,
                "local_files_only": True,
            },
            "runtime": {
                "device": self.device,
                "compute_type": self.compute_type,
                "cpu_model": self.cpu_model,
                "cpu_threads": self.cpu_threads,
                "num_workers": self.num_workers,
            },
            "language": {
                "requested": self.language_requested,
                "detected": self.language_detected,
                "probability": self.language_probability,
            },
            "parameters": self.parameters,
            "software": self.software,
            "timing": self.timing,
            "segments": [segment.to_dict() for segment in self.segments],
            "error": self.error,
        }


def validate_segment_sequence(
    segments: list[Segment],
    duration_seconds: float,
    *,
    tolerance_seconds: float = 0.05,
) -> None:
    """Validate existing ASR order; never sort or rewrite original segments."""
    previous_start = -1.0
    previous_end = -1.0
    for index, segment in enumerate(segments):
        if segment.start < previous_start:
            raise SegmentValidationError(f"segment {index} starts before the previous segment")
        if segment.end < previous_end:
            raise SegmentValidationError(f"segment {index} ends before the previous segment")
        if segment.end > duration_seconds + tolerance_seconds:
            raise SegmentValidationError(f"segment {index} ends beyond the audio duration")
        previous_start = segment.start
        previous_end = segment.end
