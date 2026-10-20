from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.cer_eval import (
    MAX_EDIT_CELLS,
    compare_texts,
    edit_distance,
    evaluate_files,
    normalize_for_cer,
)


class QualityEvaluationTests(unittest.TestCase):
    def test_normalization_is_stable_for_chinese_punctuation_and_fullwidth_text(self) -> None:
        self.assertEqual(normalize_for_cer("ＡＩ， 模型！\n"), "ai模型")

    def test_levenshtein_distance_and_cer(self) -> None:
        self.assertEqual(edit_distance("abc", "axc"), 1)
        result = compare_texts("人工参考稿。", "人工辨考稿！")
        self.assertEqual(result["reference_characters"], 5)
        self.assertEqual(result["edit_distance"], 1)
        self.assertEqual(result["cer"], 0.2)

    def test_levenshtein_rejects_pathological_cpu_work(self) -> None:
        side = int(MAX_EDIT_CELLS**0.5) + 1
        with self.assertRaisesRegex(ValueError, "过多计算"):
            edit_distance("a" * side, "b" * side)

    def test_critical_terms_are_reported_without_changing_cer(self) -> None:
        result = compare_texts(
            "模型没有下载。",
            "模型已经下载。",
            critical_terms=("没有", "下载"),
        )
        self.assertEqual(result["cer"], 2 / 6)
        self.assertFalse(result["critical_terms"][0]["matched"])
        self.assertTrue(result["critical_terms"][1]["matched"])

    def test_evaluate_files_reads_raw_segments_and_returns_no_transcript_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("这是人工校订文本。", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "success",
                "model": {"requested": "small"},
                "runtime": {"device": "cuda", "compute_type": "float16"},
                "timing": {"inference_seconds": 1.5, "private_note": "do-not-echo"},
                "segments": [{"text": "这是人工校订文本。"}],
            }), encoding="utf-8")
            result = evaluate_files(reference, evidence, ())
            serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["cer"], 0.0)
        self.assertEqual(result["model"], "small")
        self.assertEqual(result["device"], "cuda")
        self.assertNotIn("这是人工校订文本", serialized)
        self.assertNotIn("do-not-echo", serialized)
        self.assertEqual(result["timing"], {"inference_seconds": 1.5})

    def test_caption_only_evidence_is_not_misreported_as_asr_accuracy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("caption text", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "success",
                "workflow": {"final_transcription_source": "manual_subtitles"},
                "segments": [{"text": "caption text"}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不是本地 ASR"):
                evaluate_files(reference, evidence, ())

    def test_uncertain_asr_can_be_compared_with_human_reference_and_stays_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("这是一段人工校订参考稿。", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "uncertain",
                "quality": {
                    "assessment": "uncertain",
                    "warnings": [{"code": "identical_segment_text_repeated"}],
                },
                "model": {"requested": "small"},
                "runtime": {"device": "cpu", "compute_type": "int8"},
                "segments": [{"text": "这是一段人工校订参考稿。"}],
            }), encoding="utf-8")
            result = evaluate_files(reference, evidence, ())

        self.assertEqual(result["cer"], 0.0)
        self.assertEqual(result["quality_assessment"], "uncertain")

    def test_no_speech_state_is_not_scored_as_cer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("should not be compared", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "no_speech_detected",
                "segments": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "含文字分段"):
                evaluate_files(reference, evidence, ())

    def test_private_model_path_is_not_echoed_in_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("short reference", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "success",
                "model": {"requested": "D:\\Private\\models\\small", "kind": "local_directory"},
                "runtime": {"device": "cpu", "compute_type": "int8"},
                "segments": [{"text": "short reference"}],
            }), encoding="utf-8")
            result = evaluate_files(reference, evidence, ())
        self.assertEqual(result["model"], "local_directory")
        self.assertNotIn("Private", json.dumps(result))

    def test_malformed_runtime_field_is_rejected_without_traceback_shape_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_text("short", encoding="utf-8")
            evidence.write_text(json.dumps({
                "status": "success",
                "model": {},
                "runtime": [],
                "segments": [{"text": "short"}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model/runtime"):
                evaluate_files(reference, evidence, ())

    def test_oversized_reference_file_is_rejected_before_reading(self) -> None:
        from tools.cer_eval import MAX_REFERENCE_FILE_BYTES

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.txt"
            evidence = root / "evidence.json"
            reference.write_bytes(b"x" * (MAX_REFERENCE_FILE_BYTES + 1))
            evidence.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                evaluate_files(reference, evidence, ())


if __name__ == "__main__":
    unittest.main()
