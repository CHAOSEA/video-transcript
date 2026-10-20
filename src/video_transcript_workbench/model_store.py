"""Explicit preparation and verification of public faster-whisper models."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PREPARABLE_MODELS = ("tiny", "base", "small", "medium", "large-v3")
_REQUIRED_EXACT_FILES = ("config.json", "model.bin", "tokenizer.json")
_HF_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True, slots=True)
class ModelStatus:
    name: str
    status: str
    path: str | None
    revision: str | None
    size_bytes: int | None
    missing_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check_snapshot(name: str, path: str | Path) -> ModelStatus:
    root = Path(path).expanduser()
    missing = [item for item in _REQUIRED_EXACT_FILES if not (root / item).is_file()]
    vocab_files = tuple(
        item.name for item in root.glob("vocabulary.*") if item.is_file() and item.stat().st_size > 0
    )
    if not vocab_files:
        missing.append("vocabulary.*")
    if not missing:
        for filename in (*_REQUIRED_EXACT_FILES, *vocab_files):
            if (root / filename).stat().st_size <= 0:
                missing.append(filename)
    revision = root.name if root.parent.name == "snapshots" else None
    size = sum(item.stat().st_size for item in root.iterdir() if item.is_file()) if root.is_dir() else None
    return ModelStatus(
        name=name,
        status="ready" if not missing else "incomplete",
        path=str(root.resolve()) if root.exists() else None,
        revision=revision,
        size_bytes=size,
        missing_files=tuple(sorted(set(missing))),
    )


def inspect_model(name: str) -> ModelStatus:
    if name not in PREPARABLE_MODELS:
        raise ValueError(f"可管理模型仅支持：{', '.join(PREPARABLE_MODELS)}")
    # Query the Hugging Face cache without allowing an implicit download.
    from faster_whisper.utils import download_model

    try:
        path = download_model(name, local_files_only=True, use_auth_token=False)
    except Exception:
        try:
            from huggingface_hub import constants

            cache_root = Path(constants.HF_HUB_CACHE).resolve(strict=True)
            cache_entry = cache_root / f"models--Systran--faster-whisper-{name}"
            resolved_entry = cache_entry.resolve(strict=True)
            resolved_entry.relative_to(cache_root)
            revision_ref = cache_entry / "refs" / "main"
            revision = revision_ref.read_text(encoding="utf-8").strip()
            if not _HF_REVISION_RE.fullmatch(revision):
                return ModelStatus(name, "incomplete", str(resolved_entry), None, None, ("refs/main",))
            snapshot = cache_entry / "snapshots" / revision
            resolved_snapshots = (cache_entry / "snapshots").resolve(strict=True)
            resolved_snapshot = snapshot.resolve(strict=True)
            resolved_snapshot.relative_to(resolved_snapshots)
            if resolved_snapshot.is_dir():
                return _check_snapshot(name, resolved_snapshot)
        except (ImportError, OSError, RuntimeError, ValueError):
            pass
        return ModelStatus(name, "missing", None, None, None)
    return _check_snapshot(name, path)


def prepare_model(name: str) -> ModelStatus:
    if name not in PREPARABLE_MODELS:
        raise ValueError(f"可管理模型仅支持：{', '.join(PREPARABLE_MODELS)}")
    # Preparing is an explicit network operation. Respect the user's process
    # network policy, and never remove an externally configured offline flag.
    from faster_whisper.utils import download_model

    path = download_model(name, local_files_only=False, use_auth_token=False)
    status = _check_snapshot(name, path)
    if status.status != "ready":
        raise RuntimeError(
            f"模型下载后校验不完整：缺少或为空的文件 {', '.join(status.missing_files)}"
        )
    return status


def model_cache_path(name: str) -> Path:
    if name not in PREPARABLE_MODELS:
        raise ValueError(f"可管理模型仅支持：{', '.join(PREPARABLE_MODELS)}")
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE) / f"models--Systran--faster-whisper-{name}"
