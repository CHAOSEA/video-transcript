"""Offline, read-only diagnostics intended for users and local agents."""

from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

from . import __version__
from .device import inspect_cuda
from .model_store import PREPARABLE_MODELS, inspect_model


DOCTOR_SCHEMA_VERSION = "1.0"
_DEPENDENCIES = {
    "faster_whisper": ("faster-whisper", True),
    "av": ("av", True),
    "ctranslate2": ("ctranslate2", True),
    "huggingface_hub": ("huggingface-hub", True),
    "yt_dlp": ("yt-dlp", False),
    "yt_dlp_ejs": ("yt-dlp-ejs", False),
}


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _dependency_probe() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, (distribution, required) in _DEPENDENCIES.items():
        installed_version = _package_version(distribution)
        if key == "yt_dlp_ejs":
            result[key] = {
                "status": "available" if installed_version else "unavailable",
                "version": installed_version,
                "required_for_local_asr": required,
            }
            continue
        try:
            importlib.import_module(key)
            result[key] = {
                "status": "available",
                "version": installed_version,
                "required_for_local_asr": required,
            }
        except Exception:
            # Exception strings can contain local paths or environment details.
            result[key] = {
                "status": "unavailable",
                "version": installed_version,
                "required_for_local_asr": required,
                "reason": "import_failed",
            }
    return result


def _model_probe() -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for name in PREPARABLE_MODELS:
        try:
            status = inspect_model(name).status
            models[name] = {"status": status, "cached": status == "ready"}
        except Exception:
            models[name] = {
                "status": "check_unavailable",
                "cached": False,
                "reason": "cache_check_failed",
            }
    return models


def _cpu_probe() -> dict[str, Any]:
    try:
        import ctranslate2

        supported = sorted(str(value) for value in ctranslate2.get_supported_compute_types("cpu"))
        return {
            "status": "available" if "int8" in supported else "limited",
            "supported_compute_types": supported,
            "inference_verified": False,
        }
    except Exception:
        return {
            "status": "unavailable",
            "supported_compute_types": [],
            "inference_verified": False,
            "reason": "cpu_runtime_probe_failed",
        }


def _cuda_probe() -> dict[str, Any]:
    try:
        result = inspect_cuda()
        return {
            "status": "probe_available" if result.get("available") else "unavailable",
            "device_count": int(result.get("device_count", 0)),
            "supported_compute_types": list(result.get("supported_compute_types", [])),
            "reason": result.get("reason", "unknown"),
            "model_load_verified": False,
            "inference_verified": False,
        }
    except Exception:
        return {
            "status": "probe_failed",
            "device_count": 0,
            "supported_compute_types": [],
            "reason": "cuda_probe_failed",
            "model_load_verified": False,
            "inference_verified": False,
        }


def _node_probe() -> dict[str, Any]:
    executable = shutil.which("node")
    if not executable:
        return {"status": "not_installed", "version": None}
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        output = (completed.stdout or completed.stderr).strip()
        matched = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", output)
        safe_version = output if completed.returncode == 0 and matched else None
        major = int(matched.group(1)) if matched else None
        # EJS has a lower runtime floor, but online compatibility should only
        # be advertised for Node release lines that are still supported LTS.
        # As of 2026-10, 22 and 24 are supported LTS; 25 is EOL.
        supported_lts = major in {22, 24}
        lifecycle = (
            "lts" if supported_lts else
            "eol" if major is not None and major <= 25 else
            "non_lts" if major is not None else
            "unknown"
        )
        return {
            "status": "available" if safe_version and supported_lts else (
                "eol" if safe_version and lifecycle == "eol" else
                "unsupported_lifecycle" if safe_version else
                "unavailable"
            ),
            "version": safe_version,
            "lifecycle": lifecycle,
        }
    except Exception:
        return {"status": "unavailable", "version": None, "reason": "version_check_failed"}


def _cli_probe() -> dict[str, dict[str, Any]]:
    commands = ("video-transcript", "video-transcript-link", "video-transcript-online")
    return {command: {"available": shutil.which(command) is not None} for command in commands}


def collect_doctor_report(
    *,
    dependency_probe: Callable[[], dict[str, dict[str, Any]]] = _dependency_probe,
    model_probe: Callable[[], dict[str, dict[str, Any]]] = _model_probe,
    cpu_probe: Callable[[], dict[str, Any]] = _cpu_probe,
    cuda_probe: Callable[[], dict[str, Any]] = _cuda_probe,
    node_probe: Callable[[], dict[str, Any]] = _node_probe,
    cli_probe: Callable[[], dict[str, dict[str, Any]]] = _cli_probe,
) -> dict[str, Any]:
    """Collect local capability states without downloading or returning paths."""
    dependencies = dependency_probe()
    models = model_probe()
    cpu = cpu_probe()
    cuda = cuda_probe()
    node = node_probe()
    clis = cli_probe()

    core_dependencies_ready = all(
        dependencies.get(name, {}).get("status") == "available"
        for name in ("faster_whisper", "av", "ctranslate2", "huggingface_hub")
    )
    cached_models = [name for name, item in models.items() if item.get("cached") is True]
    main_cli_ready = clis.get("video-transcript", {}).get("available") is True
    cpu_ready = (
        core_dependencies_ready
        and main_cli_ready
        and cpu.get("status") == "available"
        and bool(cached_models)
    )
    if not core_dependencies_ready:
        asr_status = "dependencies_missing"
    elif not main_cli_ready:
        asr_status = "cli_entrypoint_missing"
    elif cpu.get("status") != "available":
        asr_status = "cpu_runtime_unavailable"
    elif not cached_models:
        asr_status = "model_not_prepared"
    else:
        asr_status = "ready"

    recommendations: list[dict[str, str]] = []
    if asr_status == "model_not_prepared":
        recommendations.append({
            "code": "prepare_model",
            "message": "先运行 video-transcript models recommend --intent quality，再按需运行 video-transcript models prepare <模型名>。",
        })
    elif asr_status == "dependencies_missing":
        recommendations.append({
            "code": "repair_installation",
            "message": "本地 ASR 依赖不完整；请在项目虚拟环境中重新安装发行包并运行 pip check。",
        })
    elif asr_status == "cli_entrypoint_missing":
        recommendations.append({
            "code": "install_cli_entrypoint",
            "message": "本地 ASR 依赖已安装，但 video-transcript 命令入口未找到；请在已激活的项目虚拟环境中重新安装本项目。",
        })
    elif asr_status == "cpu_runtime_unavailable":
        recommendations.append({
            "code": "check_cpu_runtime",
            "message": "CTranslate2 未报告可用的 CPU int8 运行能力；请检查当前 Python 环境和依赖安装。",
        })
    if cuda.get("status") != "probe_available":
        recommendations.append({
            "code": "cuda_optional",
            "message": "CUDA 未通过探测；仍可使用 CPU。若要使用 NVIDIA GPU，请按 faster-whisper 官方 GPU 安装说明配置驱动、CUDA 12、cuBLAS 与 cuDNN 9。",
        })
    if node.get("status") == "eol":
        recommendations.append({
            "code": "node_eol",
            "message": "当前 Node.js 版本已结束支持；建议安装受支持的 Node.js 24 LTS，再使用需要 JavaScript challenge 的在线视频路径。本地 ASR 不受影响。",
        })
    elif node.get("status") == "unsupported_lifecycle":
        recommendations.append({
            "code": "node_lts_recommended",
            "message": "当前 Node.js 版本不是受支持的 LTS 版本；建议使用 Node.js 24 LTS。本地 ASR 不受影响。",
        })
    elif node.get("status") != "available" or dependencies.get("yt_dlp_ejs", {}).get("status") != "available":
        recommendations.append({
            "code": "node_optional",
            "message": "Node.js 或 yt-dlp-ejs 未就绪；这只会影响部分需要 JavaScript challenge 的在线视频路径，不影响本地 ASR。",
        })

    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "application": {
            "name": "video-transcript-workbench",
            "version": __version__,
            "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "cli": clis,
        "dependencies": dependencies,
        "models": models,
        "compute": {
            "cpu": cpu,
            "cuda": cuda,
        },
        "online_javascript": {
            "node": node,
            "yt_dlp_ejs": dependencies.get("yt_dlp_ejs", {"status": "unavailable"}),
            "status": "available"
            if node.get("status") == "available"
            and dependencies.get("yt_dlp_ejs", {}).get("status") == "available"
            else "limited",
        },
        "capabilities": {
            "local_asr": {
                "status": asr_status,
                "ready": cpu_ready,
                "usable_cached_models": cached_models,
                "cpu_inference_verified": False,
            },
            "cuda": {
                "status": cuda.get("status"),
                "model_load_verified": False,
                "inference_verified": False,
            },
            "online_subtitles": {
                "status": "available"
                if dependencies.get("yt_dlp", {}).get("status") == "available"
                and clis.get("video-transcript-online", {}).get("available") is True
                else "limited",
            },
        },
        "recommendations": recommendations,
    }


def render_doctor_text(report: dict[str, Any]) -> str:
    asr = report["capabilities"]["local_asr"]
    cuda = report["compute"]["cuda"]
    node = report["online_javascript"]["node"]
    lines = [
        f"Video Transcript Doctor · {report['application']['version']}",
        f"Python：{report['application']['python_version']}",
        f"本地 ASR：{asr['status']}（{'可用' if asr['ready'] else '需处理'}）",
        f"已缓存模型：{', '.join(asr['usable_cached_models']) or '无'}",
        f"CPU int8 探测：{report['compute']['cpu']['status']}（尚未在 Doctor 中加载模型或实际推理）",
        f"CUDA：{cuda['status']}；类型：{', '.join(cuda['supported_compute_types']) or '无'}（仅探测，不代表模型加载或推理已验证）",
        f"Node.js：{node['status']}；yt-dlp-ejs：{report['online_javascript']['yt_dlp_ejs']['status']}",
    ]
    for command, state in report["cli"].items():
        lines.append(f"命令入口 {command}：{'可找到' if state['available'] else '未找到'}")
    if report["recommendations"]:
        lines.append("建议：")
        lines.extend(f"- {item['message']}" for item in report["recommendations"])
    return "\n".join(lines)


def doctor_json(report: dict[str, Any]) -> str:
    """Serialize the stable, path-free report for Agent consumers."""
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="video-transcript doctor",
        description="离线检查本机准备状态；不会下载模型、依赖或 CUDA 组件。",
    )
    parser.add_argument("--json", action="store_true", help="输出稳定、脱敏的 JSON 诊断结果")
    args = parser.parse_args(argv)
    report = collect_doctor_report()
    print(doctor_json(report) if args.json else render_doctor_text(report))
    # Doctor reports individual capabilities; optional Node/CUDA gaps do not
    # make a CPU-ready installation fail as a whole.
    return 0
