"""User-facing commands for explicitly managing local model cache entries."""

from __future__ import annotations

import argparse
import json
import sys

from .device import describe_cuda
from .model_store import PREPARABLE_MODELS, inspect_model, model_cache_path, prepare_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-transcript models",
        description="检查本地 faster-whisper 模型；只有 prepare 子命令会访问网络下载模型。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="列出已准备的模型和可管理模型")
    for command, help_text in (
        ("prepare", "明确下载一个公开模型到 Hugging Face 缓存"),
        ("verify", "检查模型文件是否完整"),
        ("path", "显示模型缓存目录和当前快照路径"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        sub.add_argument("model", choices=PREPARABLE_MODELS)
    subparsers.add_parser("device", help="检查 CTranslate2 可用的 CUDA 推理能力")
    recommend = subparsers.add_parser(
        "recommend",
        help="根据资源、速度、质量或现有缓存提供透明的模型候选；不会下载或切换模型",
    )
    recommend.add_argument(
        "--intent",
        required=True,
        choices=("resources", "speed", "quality", "cached"),
        help="resources=较少资源，speed=较快，quality=更重视文本质量，cached=只看已缓存模型",
    )
    recommend.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="只用于解释设备证据；命令仍显式保留此值，不自动回退或改模型",
    )
    recommend.add_argument("--json", action="store_true", help="输出供 Agent 读取的 JSON")
    return parser


def _format_size(size: int | None) -> str:
    if size is None:
        return "未知"
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units[:-1]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} GiB"


def build_recommendation(
    intent: str,
    requested_device: str,
    *,
    cache_probe=inspect_model,
    cuda_probe=describe_cuda,
) -> dict[str, object]:
    """Return an evidence-scoped suggestion without changing cache or config."""
    if intent not in {"resources", "speed", "quality", "cached"}:
        raise ValueError("unsupported model recommendation intent")
    if requested_device not in {"auto", "cpu", "cuda"}:
        raise ValueError("unsupported device")

    cache = {name: cache_probe(name).status == "ready" for name in PREPARABLE_MODELS}
    cuda_report = cuda_probe()
    cuda_available = bool(cuda_report.get("cuda_probe", {}).get("available"))
    device_context = (
        requested_device
        if requested_device != "auto"
        else ("cuda" if cuda_available else "cpu")
    )

    if intent == "resources":
        candidate = "base"
        rationale = (
            "Q0 单次 CPU/int8 测试中，base 处理 209.4 秒样本约 21.2 秒，进程峰值 RSS 约 716 MiB；"
            "只代表该机器和该样本，RSS 包含 Python 与依赖，不是模型最低内存保证。"
        )
        evidence = {
            "kind": "single_cpu_observation",
            "device": "cpu/int8",
            "sample_duration_seconds": 209.4,
            "inference_seconds": 21.2,
            "process_peak_rss_mib": 716,
        }
    elif intent == "speed":
        candidate = "small" if device_context == "cuda" else "base"
        if candidate == "small":
            rationale = (
                "small 有本机 CPU 与 RTX 3060 CUDA 的真实运行记录；其中一次 209.5 秒样本的 CUDA/float16 推理为 9.3 秒。"
                "没有跨显卡速度保证，且该计时不与 base 的 CPU 结果直接比较。"
            )
            evidence = {
                "kind": "single_cuda_observation",
                "device": "RTX 3060 / CUDA float16",
                "sample_duration_seconds": 209.5,
                "inference_seconds": 9.3,
            }
        else:
            rationale = (
                "当前探测不到 CTranslate2 CUDA，按单次 CPU/int8 样本记录选择 base 作为速度候选；"
                "不能据此保证其他音频或 CPU 的速度。"
            )
            evidence = {
                "kind": "single_cpu_observation",
                "device": "CPU/int8",
                "sample_duration_seconds": 209.4,
                "inference_seconds": 21.2,
                "process_peak_rss_mib": 716,
            }
    elif intent == "quality":
        candidate = "large-v3"
        rationale = (
            "30 条 FLEURS 简体普通话朗读样本中，large-v3 加权 CER 为 7.89%，small 为 16.78%；"
            "同一 CUDA/float16 评测中 large-v3 推理约慢 3.26 倍。只适用于该朗读语料，不能推断所有视频。"
        )
        evidence = {
            "kind": "paired_cer_benchmark",
            "dataset": "FLEURS cmn_hans_cn",
            "utterances": 30,
            "device": "CUDA/float16",
            "large_v3_weighted_cer_percent": 7.89,
            "small_weighted_cer_percent": 16.78,
            "large_v3_relative_time": 3.26,
        }
    else:
        cached = [name for name, ready in cache.items() if ready]
        preference = ("small", "base", "large-v3", "medium", "tiny")
        candidate = next((name for name in preference if name in cached), None)
        rationale = (
            "此选项只复用本地已完整缓存的模型；Doctor 不知道你过去偏好的模型。"
            "若有多个缓存，请按本次任务需要显式选择，不会替你改变模型。"
        )
        evidence = {"kind": "local_cache_inventory", "cached_models": cached}

    cached_models = [name for name, ready in cache.items() if ready]
    needs_prepare = candidate is not None and not cache[candidate]
    commands: dict[str, str] = {}
    if candidate:
        commands = {
            "local_transcription": (
                f"video-transcript <媒体路径> --model {candidate} --device {requested_device} "
                "--language zh --output-dir <新输出目录>"
            ),
            "online_transcription": (
                f'video-transcript-link "<视频链接>" --model {candidate} --device {requested_device} '
                "--language zh --output-dir <新输出目录>"
            ),
        }
        if needs_prepare:
            commands["prepare"] = f"video-transcript models prepare {candidate}"
            commands["verify"] = f"video-transcript models verify {candidate}"
    notes: list[str] = []
    if requested_device == "cuda" and not cuda_available:
        notes.append(
            "当前 CTranslate2 CUDA 探测不可用；显式 --device cuda 会失败。可自行决定改为 --device cpu 或先按官方说明配置 CUDA。"
        )
    elif requested_device == "auto" and not cuda_available:
        notes.append("当前 CUDA 探测不可用；实际命令保留 --device auto，运行时会使用 CPU/int8。")
    elif requested_device == "auto" and cuda_available:
        notes.append("当前 CUDA 探测成功，但这只表示运行时能力可见；未验证所选模型在这台设备上加载或推理。")
    if intent == "cached" and candidate is None:
        notes.append("没有可复用的完整缓存；请改选其他意图并自行决定是否运行显示的 prepare 命令。")
    if needs_prepare:
        notes.append(f"候选模型尚未缓存；如决定使用，请先明确运行 `{commands['prepare']}`，本命令不会下载。")
    return {
        "schema_version": "1.0",
        "intent": intent,
        "requested_device": requested_device,
        "device_probe": {
            "cuda_available_to_ctranslate2": cuda_available,
            "probe_device_context": device_context,
            "model_load_verified": False,
            "inference_verified": False,
        },
        "cached_models": cached_models,
        "candidate_model": candidate,
        "candidate_is_cached": bool(candidate and cache.get(candidate)),
        "requires_explicit_prepare": needs_prepare,
        "runtime_evidence": {
            "project_cpu_int8_run_exists": candidate in {"base", "small", "large-v3"},
            "project_nvidia_cuda_run_exists": candidate in {"small", "large-v3"},
            "current_machine_model_load_verified": False,
            "current_machine_inference_verified": False,
            "current_ctranslate2_cuda_probe": "available" if cuda_available else "unavailable",
        },
        "rationale": rationale,
        "evidence": evidence,
        "commands": commands,
        "notes": notes,
        "automatic_download": False,
        "automatic_model_change": False,
    }


def _render_recommendation(report: dict[str, object]) -> str:
    lines = [
        f"模型引导（意图：{report['intent']}）",
        f"候选模型：{report['candidate_model'] or '无已缓存候选'}",
        f"已缓存：{'是' if report['candidate_is_cached'] else '否'}",
        f"设备参数：--device {report['requested_device']}",
        f"依据：{report['rationale']}",
        "说明：设备探测不等于模型加载或真实推理；此引导不会下载模型或替换选择。",
    ]
    commands = report["commands"]
    if commands:
        if report["requires_explicit_prepare"]:
            lines.append(f"如决定采用，先准备：{commands['prepare']}")
            lines.append(f"然后验证：{commands['verify']}")
        lines.extend([
            f"本地媒体命令：{commands['local_transcription']}",
            f"在线视频命令：{commands['online_transcription']}",
        ])
    lines.extend(f"提示：{note}" for note in report["notes"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            print("模型缓存状态（只有 prepare 会下载）：")
            for name in PREPARABLE_MODELS:
                status = inspect_model(name)
                print(f"{name:10} {status.status:10} {_format_size(status.size_bytes)}")
            print("\n当前识别仍要求显式 --model；模型大小不等于已验证的准确率排名。")
            return 0
        if args.command == "device":
            result = describe_cuda()
            plan = result["device"]
            print(f"CTranslate2 当前选择：{plan} / {result['compute_type']}")
            print(f"CUDA 可用：{'是' if result['cuda_probe']['available'] else '否'}")
            print(f"设备数：{result['cuda_probe']['device_count']}")
            print(f"支持精度：{', '.join(result['cuda_probe']['supported_compute_types']) or '无'}")
            print(f"检测结果：{result['cuda_probe']['reason']}")
            return 0
        if args.command == "recommend":
            report = build_recommendation(args.intent, args.device)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            else:
                print(_render_recommendation(report))
            return 0
        if args.command == "path":
            status = inspect_model(args.model)
            print(f"缓存目录：{model_cache_path(args.model)}")
            print(f"模型状态：{status.status}")
            print(f"当前快照：{status.path or '尚未准备'}")
            return 0 if status.status == "ready" else 1
        if args.command == "verify":
            status = inspect_model(args.model)
            if status.status == "ready":
                print(f"{args.model} 完整：revision={status.revision}, size={_format_size(status.size_bytes)}")
                print(f"路径：{status.path}")
                return 0
            if status.status == "incomplete":
                print(f"{args.model} 文件不完整：缺少 {', '.join(status.missing_files)}", file=sys.stderr)
            else:
                print(f"{args.model} 尚未下载。请显式运行：video-transcript models prepare {args.model}", file=sys.stderr)
            return 1
        if args.command == "prepare":
            print(f"开始下载公开模型 {args.model}。模型权重较大，文件仅进入本机 Hugging Face 缓存。")
            status = prepare_model(args.model)
            print(f"准备完成：{status.name} revision={status.revision}, size={_format_size(status.size_bytes)}")
            print(f"缓存快照：{status.path}")
            return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: 模型操作未完成。请检查网络、磁盘空间和公开模型仓库状态。", file=sys.stderr)
        return 1
    return 2
