"""Compare one ASR evidence file with a human-verified reference transcript.

The tool prints aggregate metrics only. It never echoes transcript or reference
text, and does not contact a network service.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from pathlib import Path
from typing import Any


MAX_NORMALIZED_CHARS = 20_000
MAX_EDIT_CELLS = 5_000_000
MAX_REFERENCE_FILE_BYTES = 1 * 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 32 * 1024 * 1024
_PUBLIC_TIMING_FIELDS = ("media_decode_seconds", "model_load_seconds", "inference_seconds", "total_seconds", "rtf")


def normalize_for_cer(text: str) -> str:
    """NFKC/casefold text and remove whitespace and Unicode punctuation."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "Z", "C"))
    )


def edit_distance(reference: str, hypothesis: str) -> int:
    """Return Levenshtein distance using O(min(n, m)) memory."""
    if len(reference) > MAX_NORMALIZED_CHARS or len(hypothesis) > MAX_NORMALIZED_CHARS:
        raise ValueError(
            f"单侧 CER 文本最多处理 {MAX_NORMALIZED_CHARS:,} 个归一化字符；请按短片段拆分"
        )
    if len(reference) * len(hypothesis) > MAX_EDIT_CELLS:
        raise ValueError(
            "该 CER 对照需要过多计算；请按较短音频片段分别运行后汇总"
        )
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, ref_char in enumerate(reference, start=1):
        current = [row]
        for column, hyp_char in enumerate(hypothesis, start=1):
            current.append(min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (ref_char != hyp_char),
            ))
        previous = current
    return previous[-1]


def _asr_payload(evidence: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if evidence.get("status") not in {"success", "uncertain"}:
        raise ValueError("只允许评测已完成且含文字分段的 ASR evidence")
    workflow = evidence.get("workflow")
    if isinstance(workflow, dict):
        if workflow.get("final_transcription_source") != "local_asr":
            raise ValueError("当前 evidence 不是本地 ASR 输出；不要将字幕路径混入模型 CER")
        nested = evidence.get("asr_attempt")
        if not isinstance(nested, dict):
            raise ValueError("在线 ASR evidence 缺少 asr_attempt")
        if nested.get("status") not in {"success", "uncertain"}:
            raise ValueError("在线 ASR evidence 中没有已完成且含文字分段的 ASR 结果")
        evidence = nested
    segments = evidence.get("segments")
    model = evidence.get("model", {})
    runtime = evidence.get("runtime", {})
    if not isinstance(model, dict) or not isinstance(runtime, dict):
        raise ValueError("evidence 中的 model/runtime 字段格式无效")
    if not isinstance(segments, list) or not segments:
        raise ValueError("ASR evidence 没有可评测的原始分段")
    if not all(isinstance(item, dict) and isinstance(item.get("text"), str) for item in segments):
        raise ValueError("ASR evidence 的原始分段格式无效")
    return segments, model, runtime


def _quality_assessment(evidence: dict[str, Any]) -> str:
    payload = evidence
    if isinstance(evidence.get("workflow"), dict):
        nested = evidence.get("asr_attempt")
        if isinstance(nested, dict):
            payload = nested
    quality = payload.get("quality")
    assessment = quality.get("assessment") if isinstance(quality, dict) else None
    if assessment in {"text_available", "uncertain", "no_speech_detected", "model_or_decode_failure"}:
        return assessment
    return "not_reported"


def _safe_timing(evidence: dict[str, Any]) -> dict[str, float]:
    """Expose only known finite timing metrics, never arbitrary evidence values."""
    timing_source = evidence
    workflow = evidence.get("workflow")
    if isinstance(workflow, dict) and workflow.get("final_transcription_source") == "local_asr":
        nested = evidence.get("asr_attempt")
        if isinstance(nested, dict):
            timing_source = nested
    raw = timing_source.get("timing", {})
    if not isinstance(raw, dict):
        return {}
    sanitized: dict[str, float] = {}
    for key in _PUBLIC_TIMING_FIELDS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            normalized = float(value)
            if math.isfinite(normalized) and normalized >= 0:
                sanitized[key] = normalized
    return sanitized


def compare_texts(
    reference: str,
    hypothesis: str,
    *,
    critical_terms: tuple[str, ...] = (),
) -> dict[str, Any]:
    normalized_reference = normalize_for_cer(reference)
    normalized_hypothesis = normalize_for_cer(hypothesis)
    if not normalized_reference:
        raise ValueError("人工参考稿归一化后为空")
    distance = edit_distance(normalized_reference, normalized_hypothesis)
    term_results: list[dict[str, Any]] = []
    for index, term in enumerate(critical_terms, start=1):
        normalized_term = normalize_for_cer(term)
        if not normalized_term:
            raise ValueError("关键术语归一化后为空")
        reference_count = normalized_reference.count(normalized_term)
        hypothesis_count = normalized_hypothesis.count(normalized_term)
        if reference_count == 0:
            raise ValueError("关键术语不在人工参考稿中")
        term_results.append({
            "term_index": index,
            "reference_count": reference_count,
            "hypothesis_count": hypothesis_count,
            "matched": hypothesis_count > 0,
        })
    return {
        "reference_characters": len(normalized_reference),
        "hypothesis_characters": len(normalized_hypothesis),
        "edit_distance": distance,
        "cer": distance / len(normalized_reference),
        "cer_percent": round(100 * distance / len(normalized_reference), 4),
        "critical_terms": term_results,
    }


def evaluate_files(reference_path: Path, evidence_path: Path, critical_terms: tuple[str, ...]) -> dict[str, Any]:
    if reference_path.stat().st_size > MAX_REFERENCE_FILE_BYTES:
        raise ValueError("人工参考稿文件过大；请使用不超过 1 MiB 的短片段参考稿")
    if evidence_path.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
        raise ValueError("evidence 文件过大；请按短片段重新生成结果")
    reference = reference_path.read_text(encoding="utf-8-sig")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evidence.json 顶层必须是对象")
    segments, model, runtime = _asr_payload(payload)
    quality_assessment = _quality_assessment(payload)
    result = compare_texts(
        reference,
        "".join(item["text"] for item in segments),
        critical_terms=critical_terms,
    )
    requested_model = model.get("requested", "unknown")
    if model.get("kind") == "local_directory" or (
        isinstance(requested_model, str) and ("/" in requested_model or "\\" in requested_model)
    ):
        requested_model = model.get("kind", "local_model_directory")
    return {
        "status": "evaluated",
        "method": "character_error_rate_after_nfkc_casefold_and_removing_whitespace_punctuation_and_controls",
        "quality_assessment": quality_assessment,
        "model": requested_model,
        "device": runtime.get("device", "unknown"),
        "compute_type": runtime.get("compute_type", "unknown"),
        "timing": _safe_timing(payload),
        **result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将已完成 ASR 的 evidence.json 与人工校订参考稿比较；只输出指标，不输出文本。"
    )
    parser.add_argument("--reference", required=True, type=Path, help="人工听写并校订的 UTF-8 参考稿")
    parser.add_argument("--evidence", required=True, type=Path, help="本地运行生成的 evidence.json")
    parser.add_argument(
        "--critical-term",
        action="append",
        default=[],
        help="关键术语、数字或否定词；可重复指定，结果只显示匹配情况",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_files(args.reference, args.evidence, tuple(args.critical_term))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
