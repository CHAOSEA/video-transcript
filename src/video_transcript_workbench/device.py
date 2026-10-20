"""Conservative CPU/NVIDIA CUDA selection for CTranslate2 inference."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class DeviceSelectionError(RuntimeError):
    """Raised when an explicitly requested inference device is unavailable."""


@dataclass(frozen=True, slots=True)
class DevicePlan:
    requested: str
    device: str
    compute_type: str
    selection: dict[str, Any]


def inspect_cuda() -> dict[str, Any]:
    """Query CTranslate2 itself; nvidia-smi alone is not sufficient."""
    try:
        import ctranslate2

        count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return {
            "available": False,
            "device_count": 0,
            "supported_compute_types": [],
            "reason": "ctranslate2_cuda_probe_failed",
            "error_type": type(exc).__name__,
        }
    if count < 1:
        return {
            "available": False,
            "device_count": 0,
            "supported_compute_types": [],
            "reason": "no_cuda_device_visible_to_ctranslate2",
        }
    try:
        compute_types = sorted(str(item) for item in ctranslate2.get_supported_compute_types("cuda"))
    except Exception as exc:
        return {
            "available": False,
            "device_count": count,
            "supported_compute_types": [],
            "reason": "ctranslate2_cuda_compute_probe_failed",
            "error_type": type(exc).__name__,
        }
    preferred = next((item for item in ("float16", "int8_float16") if item in compute_types), None)
    if preferred is None:
        return {
            "available": False,
            "device_count": count,
            "supported_compute_types": compute_types,
            "reason": "no_supported_cuda_compute_type",
        }
    return {
        "available": True,
        "device_count": count,
        "supported_compute_types": compute_types,
        "selected_compute_type": preferred,
        "reason": "ctranslate2_cuda_probe_succeeded",
    }


def resolve_device(requested: str) -> DevicePlan:
    """Select a runtime plan without changing the explicitly requested model."""
    if requested not in {"auto", "cpu", "cuda"}:
        raise DeviceSelectionError("device must be one of: auto, cpu, cuda")
    if requested == "cpu":
        return DevicePlan(
            requested="cpu",
            device="cpu",
            compute_type="int8",
            selection={"reason": "cpu_explicitly_requested"},
        )

    cuda = inspect_cuda()
    if cuda["available"]:
        return DevicePlan(
            requested=requested,
            device="cuda",
            compute_type=str(cuda["selected_compute_type"]),
            selection={"reason": "cuda_available_to_ctranslate2", "cuda": cuda},
        )
    if requested == "cuda":
        raise DeviceSelectionError(
            "当前 faster-whisper/CTranslate2 环境未检测到可用 CUDA 推理配置；"
            "请检查 NVIDIA 驱动、CUDA 12、cuBLAS 与 cuDNN 9，或改用 --device cpu。"
        )
    return DevicePlan(
        requested="auto",
        device="cpu",
        compute_type="int8",
        selection={"reason": "cuda_unavailable_cpu_selected", "cuda": cuda},
    )


def describe_cuda() -> dict[str, Any]:
    """Return a JSON-safe hardware/runtime diagnostic for the models command."""
    return asdict(resolve_device("auto")) | {"cuda_probe": inspect_cuda()}
