from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from video_transcript_workbench.doctor import collect_doctor_report, doctor_json
from video_transcript_workbench.doctor import _node_probe
from video_transcript_workbench.engine import ModelSelectionError, validate_managed_model_is_prepared
from video_transcript_workbench.model_cli import build_recommendation, main as models_main


def _dependencies(*, online: bool = True) -> dict[str, dict[str, object]]:
    result = {
        name: {"status": "available", "version": "1.0", "required_for_local_asr": required}
        for name, required in (
            ("faster_whisper", True),
            ("av", True),
            ("ctranslate2", True),
            ("huggingface_hub", True),
            ("yt_dlp", False),
            ("yt_dlp_ejs", False),
        )
    }
    if not online:
        result["yt_dlp_ejs"] = {"status": "unavailable", "version": None, "required_for_local_asr": False}
    return result


def _models(*cached: str) -> dict[str, dict[str, object]]:
    names = ("tiny", "base", "small", "medium", "large-v3")
    return {name: {"status": "ready" if name in cached else "missing", "cached": name in cached} for name in names}


class DoctorTests(unittest.TestCase):
    def _probe_node_version(self, node_version: str) -> dict[str, object]:
        completed = SimpleNamespace(returncode=0, stdout=node_version, stderr="")
        with patch("video_transcript_workbench.doctor.shutil.which", return_value="node.exe"), patch(
            "video_transcript_workbench.doctor.subprocess.run", return_value=completed
        ):
            return _node_probe()

    def test_node_25_is_reported_eol_and_keeps_local_asr_independent(self) -> None:
        node = self._probe_node_version("v25.9.0")
        self.assertEqual(node["status"], "eol")
        self.assertEqual(node["lifecycle"], "eol")
        report = collect_doctor_report(
            dependency_probe=_dependencies,
            model_probe=lambda: _models("small"),
            cpu_probe=lambda: {"status": "available", "supported_compute_types": ["int8"], "inference_verified": False},
            cuda_probe=lambda: {"status": "unavailable", "device_count": 0, "supported_compute_types": [], "reason": "no_device", "model_load_verified": False, "inference_verified": False},
            node_probe=lambda: node,
            cli_probe=lambda: {"video-transcript": {"available": True}, "video-transcript-link": {"available": True}, "video-transcript-online": {"available": True}},
        )
        self.assertTrue(report["capabilities"]["local_asr"]["ready"])
        self.assertEqual(report["online_javascript"]["status"], "limited")
        self.assertIn("node_eol", {item["code"] for item in report["recommendations"]})

    def test_node_24_lts_is_available_for_optional_online_javascript(self) -> None:
        node = self._probe_node_version("v24.10.0")
        self.assertEqual(node["status"], "available")
        self.assertEqual(node["lifecycle"], "lts")

    def test_cpu_only_is_ready_without_cuda_or_node(self) -> None:
        report = collect_doctor_report(
            dependency_probe=_dependencies,
            model_probe=lambda: _models("small"),
            cpu_probe=lambda: {"status": "available", "supported_compute_types": ["int8"], "inference_verified": False},
            cuda_probe=lambda: {"status": "unavailable", "device_count": 0, "supported_compute_types": [], "reason": "no_device", "model_load_verified": False, "inference_verified": False},
            node_probe=lambda: {"status": "not_installed", "version": None},
            cli_probe=lambda: {"video-transcript": {"available": True}, "video-transcript-link": {"available": True}, "video-transcript-online": {"available": True}},
        )
        self.assertEqual(report["capabilities"]["local_asr"]["status"], "ready")
        self.assertTrue(report["capabilities"]["local_asr"]["ready"])
        self.assertEqual(report["online_javascript"]["status"], "limited")
        self.assertEqual(report["capabilities"]["cuda"]["status"], "unavailable")
        self.assertFalse(report["capabilities"]["cuda"]["inference_verified"])
        self.assertIn("node_optional", {item["code"] for item in report["recommendations"]})

    def test_missing_model_reports_actionable_setup_without_failing_optional_features(self) -> None:
        report = collect_doctor_report(
            dependency_probe=_dependencies,
            model_probe=lambda: _models(),
            cpu_probe=lambda: {"status": "available", "supported_compute_types": ["int8"], "inference_verified": False},
            cuda_probe=lambda: {"status": "unavailable", "device_count": 0, "supported_compute_types": [], "reason": "no_device", "model_load_verified": False, "inference_verified": False},
            node_probe=lambda: {"status": "available", "version": "v24.1.0"},
            cli_probe=lambda: {
                "video-transcript": {"available": True},
                "video-transcript-link": {"available": True},
                "video-transcript-online": {"available": True},
            },
        )
        self.assertEqual(report["capabilities"]["local_asr"]["status"], "model_not_prepared")
        self.assertFalse(report["capabilities"]["local_asr"]["ready"])
        self.assertEqual(report["capabilities"]["online_subtitles"]["status"], "available")
        advice = " ".join(item["message"] for item in report["recommendations"])
        self.assertIn("models prepare", advice)

    def test_cuda_probe_does_not_claim_model_load_or_inference(self) -> None:
        report = collect_doctor_report(
            dependency_probe=_dependencies,
            model_probe=lambda: _models("small"),
            cpu_probe=lambda: {"status": "available", "supported_compute_types": ["int8"], "inference_verified": False},
            cuda_probe=lambda: {"status": "probe_available", "device_count": 1, "supported_compute_types": ["float16"], "reason": "ok", "model_load_verified": False, "inference_verified": False},
            node_probe=lambda: {"status": "available", "version": "v24.1.0"},
            cli_probe=lambda: {},
        )
        self.assertEqual(report["capabilities"]["cuda"]["status"], "probe_available")
        self.assertFalse(report["capabilities"]["cuda"]["model_load_verified"])
        self.assertFalse(report["capabilities"]["cuda"]["inference_verified"])

    def test_json_schema_is_stable_and_excludes_paths_secrets_and_exception_text(self) -> None:
        report = collect_doctor_report(
            dependency_probe=lambda: {
                **_dependencies(),
                "faster_whisper": {"status": "unavailable", "reason": "import_failed", "version": None, "required_for_local_asr": True},
            },
            model_probe=lambda: {"small": {"status": "check_unavailable", "cached": False, "reason": "cache_check_failed"}},
            cpu_probe=lambda: {"status": "unavailable", "supported_compute_types": [], "inference_verified": False},
            cuda_probe=lambda: {"status": "probe_failed", "device_count": 0, "supported_compute_types": [], "reason": "cuda_probe_failed", "model_load_verified": False, "inference_verified": False},
            node_probe=lambda: {"status": "not_installed", "version": None},
            cli_probe=lambda: {},
        )
        serialized = doctor_json(report)
        restored = json.loads(serialized)
        self.assertEqual(restored["schema_version"], "1.0")
        self.assertEqual(restored["capabilities"]["local_asr"]["status"], "dependencies_missing")
        for forbidden in ("C:\\Users\\", "PRIVATE_TOKEN", "PRIVATE_PATH_SENTINEL", "access-token"):
            self.assertNotIn(forbidden, serialized)


class ModelRecommendationTests(unittest.TestCase):
    def _cache_probe(self, cached: tuple[str, ...]):
        return lambda name: SimpleNamespace(status="ready" if name in cached else "missing")

    def test_cpu_speed_candidate_is_explicit_and_does_not_download(self) -> None:
        report = build_recommendation(
            "speed",
            "auto",
            cache_probe=self._cache_probe(()),
            cuda_probe=lambda: {"cuda_probe": {"available": False}},
        )
        self.assertEqual(report["candidate_model"], "base")
        self.assertTrue(report["requires_explicit_prepare"])
        self.assertFalse(report["automatic_download"])
        self.assertFalse(report["automatic_model_change"])
        self.assertIn("--model base --device auto", report["commands"]["local_transcription"])
        self.assertTrue(any("models prepare base" in note for note in report["notes"]))

    def test_cuda_speed_candidate_keeps_requested_device_and_discloses_probe_limit(self) -> None:
        report = build_recommendation(
            "speed",
            "auto",
            cache_probe=self._cache_probe(("small",)),
            cuda_probe=lambda: {"cuda_probe": {"available": True}},
        )
        self.assertEqual(report["candidate_model"], "small")
        self.assertTrue(report["candidate_is_cached"])
        self.assertFalse(report["device_probe"]["inference_verified"])
        self.assertIn("--device auto", report["commands"]["online_transcription"])
        self.assertTrue(any("未验证" in note for note in report["notes"]))

    def test_quality_uses_scoped_fleurs_evidence_and_does_not_set_global_default(self) -> None:
        report = build_recommendation(
            "quality",
            "cpu",
            cache_probe=self._cache_probe(()),
            cuda_probe=lambda: {"cuda_probe": {"available": False}},
        )
        self.assertEqual(report["candidate_model"], "large-v3")
        self.assertEqual(report["evidence"]["utterances"], 30)
        self.assertEqual(report["evidence"]["large_v3_weighted_cer_percent"], 7.89)
        self.assertIn("不能推断所有视频", report["rationale"])
        self.assertIn("--device cpu", report["commands"]["local_transcription"])

    def test_cached_intent_only_recommends_an_existing_model(self) -> None:
        report = build_recommendation(
            "cached",
            "cpu",
            cache_probe=self._cache_probe(("small", "large-v3")),
            cuda_probe=lambda: {"cuda_probe": {"available": False}},
        )
        self.assertEqual(report["candidate_model"], "small")
        self.assertEqual(report["cached_models"], ["small", "large-v3"])
        self.assertFalse(report["requires_explicit_prepare"])
        self.assertNotIn("prepare", report["commands"])
        self.assertNotIn("prepare", report["notes"])

    def test_json_cli_is_machine_readable_and_uses_only_read_probes(self) -> None:
        out, err = StringIO(), StringIO()
        with patch("video_transcript_workbench.model_cli.inspect_model", side_effect=self._cache_probe(("small",))), patch(
            "video_transcript_workbench.model_cli.describe_cuda", return_value={"cuda_probe": {"available": False}}
        ), patch("video_transcript_workbench.model_cli.prepare_model") as prepare, redirect_stdout(out), redirect_stderr(err):
            code = models_main(["recommend", "--intent", "cached", "--device", "cpu", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(json.loads(out.getvalue())["candidate_model"], "small")
        prepare.assert_not_called()


class MissingModelGuidanceTests(unittest.TestCase):
    def test_missing_managed_model_gives_prepare_and_verify_commands(self) -> None:
        with patch("video_transcript_workbench.model_store.inspect_model", return_value=SimpleNamespace(status="missing")):
            with self.assertRaisesRegex(ModelSelectionError, "models prepare small.*models verify small"):
                validate_managed_model_is_prepared("small")

    def test_incomplete_model_points_to_verify_then_explicit_prepare(self) -> None:
        with patch("video_transcript_workbench.model_store.inspect_model", return_value=SimpleNamespace(status="incomplete")):
            with self.assertRaisesRegex(ModelSelectionError, "models verify base.*models prepare base"):
                validate_managed_model_is_prepared("base")


if __name__ == "__main__":
    unittest.main()
