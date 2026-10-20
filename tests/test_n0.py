from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_transcript_workbench.artifacts import (
    AudioInputError,
    DEFAULT_MAX_AUDIO_DURATION_SECONDS,
    MAX_AUDIO_DURATION_SECONDS,
    inspect_audio,
    render_markdown,
    write_success_artifacts,
)
from video_transcript_workbench.engine import (
    EngineExecutionError,
    ModelSelectionError,
    OutputConflictError,
    _model_factory,
    _model_revision,
    resolve_model_reference,
    transcribe_to_artifacts,
)
from video_transcript_workbench.models import (
    AudioMetadata,
    RunConfig,
    RunEvidence,
    Segment,
    SegmentValidationError,
    validate_segment_sequence,
)


def write_test_wav(path: Path, *, frames: int = 16_000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * frames)


class InputValidationTests(unittest.TestCase):
    def test_rejects_invalid_duration_limits(self) -> None:
        invalid_values = (
            0,
            -1,
            None,
            True,
            float("nan"),
            float("inf"),
            MAX_AUDIO_DURATION_SECONDS + 1,
            10**100,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    inspect_audio(Path("unused.wav"), max_duration_seconds=value)

    def test_rejects_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.wav"
            with self.assertRaises(AudioInputError):
                inspect_audio(missing)

    def test_rejects_unsupported_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "audio.flac"
            source.write_bytes(b"not audio")
            with self.assertRaisesRegex(AudioInputError, "不支持的媒体类型"):
                inspect_audio(source)

    def test_rejects_corrupt_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bad.wav"
            source.write_bytes(b"not a wave file")
            with self.assertRaises(AudioInputError):
                inspect_audio(source)

    def test_run_config_rejects_non_integral_or_non_finite_controls(self) -> None:
        invalid_options = (
            {"cpu_threads": True},
            {"cpu_threads": 1.5},
            {"cpu_threads": 0},
            {"beam_size": False},
            {"beam_size": 2.5},
            {"best_of": 0},
            {"temperature": True},
            {"temperature": float("nan")},
            {"temperature": float("inf")},
            {"temperature": 10**1000},
            {"device": "metal"},
            {"vad_filter": 1},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    RunConfig(model="small", **options)

    def test_run_config_rejects_non_string_model_and_language(self) -> None:
        with self.assertRaises(ValueError):
            RunConfig(model=None)
        with self.assertRaises(ValueError):
            RunConfig(model="small", language=None)


class ModelSelectionTests(unittest.TestCase):
    def test_accepts_explicit_cached_alias(self) -> None:
        self.assertEqual(
            resolve_model_reference("small"),
            ("small", "local_hub_cache_alias"),
        )

    def test_accepts_existing_local_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "my-model"
            path.mkdir()
            resolved, kind = resolve_model_reference(str(path))
            self.assertEqual(Path(resolved), path.resolve())
            self.assertEqual(kind, "local_directory")
            self.assertEqual(_model_revision(resolved, kind)["status"], "unknown")

    def test_resolves_revision_from_local_snapshot_path(self) -> None:
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshots" / revision
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").write_bytes(b"local snapshot marker")
            info = _model_revision(str(snapshot), "local_directory")
            self.assertEqual(info["status"], "resolved")
            self.assertEqual(info["value"], revision)

    def test_rejects_missing_model_directory_and_remote_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "models" / "small"
            with self.assertRaises(ModelSelectionError):
                resolve_model_reference(str(missing))
        with self.assertRaises(ModelSelectionError):
            resolve_model_reference("some-user/remote-model")


class DeviceSelectionTests(unittest.TestCase):
    def test_model_factory_passes_selected_device_and_precision(self) -> None:
        with patch("video_transcript_workbench.engine.WhisperModel") as whisper:
            _model_factory(
                "small",
                device="cuda",
                compute_type="float16",
                cpu_threads=4,
                revision="a" * 40,
            )
        options = whisper.call_args.kwargs
        self.assertEqual(options["device"], "cuda")
        self.assertEqual(options["compute_type"], "float16")
        self.assertEqual(options["cpu_threads"], 4)
        self.assertTrue(options["local_files_only"])
        self.assertEqual(options["revision"], "a" * 40)

    def test_cpu_is_a_deterministic_int8_baseline(self) -> None:
        from video_transcript_workbench.device import resolve_device

        plan = resolve_device("cpu")
        self.assertEqual((plan.device, plan.compute_type), ("cpu", "int8"))
        self.assertEqual(plan.selection["reason"], "cpu_explicitly_requested")

    def test_auto_selects_cuda_compute_type_reported_by_ctranslate2(self) -> None:
        from video_transcript_workbench import device

        with patch.object(device, "inspect_cuda", return_value={
            "available": True,
            "device_count": 1,
            "supported_compute_types": ["float16", "int8_float16"],
            "selected_compute_type": "float16",
            "reason": "test_probe",
        }):
            plan = device.resolve_device("auto")
        self.assertEqual((plan.device, plan.compute_type), ("cuda", "float16"))

    def test_auto_uses_cpu_when_ctranslate2_cannot_use_cuda(self) -> None:
        from video_transcript_workbench import device

        with patch.object(device, "inspect_cuda", return_value={
            "available": False,
            "device_count": 0,
            "supported_compute_types": [],
            "reason": "probe_failed",
        }):
            plan = device.resolve_device("auto")
        self.assertEqual((plan.device, plan.compute_type), ("cpu", "int8"))
        self.assertEqual(plan.selection["reason"], "cuda_unavailable_cpu_selected")

    def test_explicit_cuda_fails_instead_of_silently_falling_back(self) -> None:
        from video_transcript_workbench import device

        with patch.object(device, "inspect_cuda", return_value={
            "available": False,
            "device_count": 0,
            "supported_compute_types": [],
            "reason": "probe_failed",
        }):
            with self.assertRaises(device.DeviceSelectionError):
                device.resolve_device("cuda")


class ModelStoreTests(unittest.TestCase):
    def test_prepare_is_an_explicit_online_operation_and_verifies_files(self) -> None:
        from video_transcript_workbench.model_store import prepare_model

        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshots" / ("a" * 40)
            snapshot.mkdir(parents=True)
            for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (snapshot / name).write_bytes(b"present")
            with patch(
                "faster_whisper.utils.download_model",
                return_value=str(snapshot),
            ) as download:
                status = prepare_model("small")
        self.assertEqual(status.status, "ready")
        self.assertEqual(status.revision, "a" * 40)
        download.assert_called_once_with("small", local_files_only=False, use_auth_token=False)

    def test_verify_reports_incomplete_cache_without_downloading(self) -> None:
        from video_transcript_workbench.model_store import inspect_model

        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshot"
            snapshot.mkdir()
            (snapshot / "config.json").write_bytes(b"config")
            with patch(
                "faster_whisper.utils.download_model",
                return_value=str(snapshot),
            ) as download:
                status = inspect_model("medium")
        self.assertEqual(status.status, "incomplete")
        self.assertIn("model.bin", status.missing_files)
        download.assert_called_once_with("medium", local_files_only=True, use_auth_token=False)

    def test_inspect_model_rejects_untrusted_huggingface_cache_revision(self) -> None:
        from video_transcript_workbench.model_store import inspect_model

        with tempfile.TemporaryDirectory() as temp:
            cache_root = Path(temp)
            model_cache = cache_root / "models--Systran--faster-whisper-small"
            (model_cache / "refs").mkdir(parents=True)
            (model_cache / "snapshots").mkdir()
            (model_cache / "refs" / "main").write_text("../../outside", encoding="utf-8")
            with (
                patch("faster_whisper.utils.download_model", side_effect=RuntimeError("offline")),
                patch("huggingface_hub.constants.HF_HUB_CACHE", str(cache_root)),
            ):
                status = inspect_model("small")
        self.assertEqual(status.status, "incomplete")
        self.assertEqual(status.missing_files, ("refs/main",))

    def test_model_manager_rejects_arbitrary_hub_repositories(self) -> None:
        from video_transcript_workbench.model_store import prepare_model

        with self.assertRaises(ValueError):
            prepare_model("attacker/model")


class SegmentAndOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioMetadata(
            file_name="sample.wav",
            sha256="a" * 64,
            duration_seconds=3.0,
            sample_rate_hz=16_000,
            channels=1,
            sample_width_bytes=2,
        )

    def test_accepts_monotonic_segment_order(self) -> None:
        validate_segment_sequence(
            [Segment(0.0, 0.8, "first"), Segment(0.8, 1.7, "second")],
            duration_seconds=3.0,
        )

    def test_rejects_out_of_order_segments_without_sorting(self) -> None:
        segments = [Segment(1.0, 1.7, "later"), Segment(0.0, 0.8, "earlier")]
        with self.assertRaises(SegmentValidationError):
            validate_segment_sequence(segments, duration_seconds=3.0)
        self.assertEqual(segments[0].text, "later")

    def test_rejects_segment_past_audio_end(self) -> None:
        with self.assertRaisesRegex(SegmentValidationError, "beyond"):
            validate_segment_sequence(
                [Segment(2.5, 3.2, "overrun")],
                duration_seconds=3.0,
            )

    def test_segment_and_timeline_reject_non_numeric_or_non_finite_values(self) -> None:
        for start, end in ((True, 1.0), (0.0, "1"), (10**1000, 10**1000 + 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(SegmentValidationError):
                    Segment(start, end, "invalid time")
        for duration in (0, -1, True, float("nan"), float("inf"), "3"):
            with self.subTest(duration=duration):
                with self.assertRaises(SegmentValidationError):
                    validate_segment_sequence([Segment(0.0, 0.5, "x")], duration)

    def test_markdown_contains_original_text_and_time_axis(self) -> None:
        raw_text = " 原始文本 punctuation， preserved "
        markdown = render_markdown(
            self.audio,
            "small",
            "zh",
            [Segment(0.0, 1.234, raw_text)],
        )
        self.assertIn("### 00:00:00.000 – 00:00:01.234", markdown)
        self.assertIn(raw_text, markdown)
        self.assertIn("a" * 64, markdown)

    def test_markdown_escapes_untrusted_local_text(self) -> None:
        markdown = render_markdown(
            self.audio,
            "small",
            "zh",
            [Segment(0.0, 1.0, "<script>alert(1)</script> [link](javascript:bad())")],
        )
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertNotIn("[link](javascript:bad())", markdown)

    def test_parallel_writers_never_replace_each_others_artifacts(self) -> None:
        import video_transcript_workbench.artifacts as artifacts

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            barrier = threading.Barrier(2)
            actual_write = artifacts._write_new_text_file

            def synchronized_write(path: Path, text: str) -> None:
                if path.name == "transcript.md":
                    barrier.wait(timeout=5)
                actual_write(path, text)

            def build_evidence(run_id: str) -> RunEvidence:
                return RunEvidence(
                    schema_version="1.0",
                    run_id=run_id,
                    status="success",
                    started_at_utc="2026-01-01T00:00:00+00:00",
                    finished_at_utc="2026-01-01T00:00:01+00:00",
                    audio=self.audio,
                    model_requested="small",
                    model_kind="local_hub_cache_alias",
                    device="cpu",
                    compute_type="int8",
                    cpu_model="test-cpu",
                    cpu_threads=1,
                    num_workers=1,
                    language_requested="zh",
                    language_detected="zh",
                    language_probability=1.0,
                    parameters={},
                    software={},
                )

            def write_one(run_id: str) -> tuple[str, str]:
                try:
                    write_success_artifacts(
                        output,
                        f"text from {run_id}",
                        build_evidence(run_id),
                    )
                    return "written", run_id
                except FileExistsError:
                    return "conflict", run_id

            with patch.object(artifacts, "_write_new_text_file", synchronized_write):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(write_one, ("run-a", "run-b")))

            self.assertEqual(sum(state == "written" for state, _ in results), 1)
            self.assertEqual(sum(state == "conflict" for state, _ in results), 1)
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual((output / "transcript.md").read_text(encoding="utf-8"), f"text from {evidence['run_id']}")

    def test_evidence_contains_runtime_parameters_and_segments(self) -> None:
        evidence = RunEvidence(
            schema_version="1.0",
            run_id="test-run",
            status="success",
            started_at_utc="2026-01-01T00:00:00+00:00",
            finished_at_utc="2026-01-01T00:00:01+00:00",
            audio=self.audio,
            model_requested="small",
            model_kind="local_hub_cache_alias",
            device="cpu",
            compute_type="int8",
            cpu_model="test-cpu",
            cpu_threads=4,
            num_workers=1,
            language_requested="zh",
            language_detected="zh",
            language_probability=0.99,
            parameters={"beam_size": 5, "vad_filter": False},
            software={"faster_whisper": "1.2.1"},
            segments=[Segment(0.0, 0.5, "raw")],
        )
        serialized = evidence.to_dict()
        self.assertEqual(serialized["status"], "success")
        self.assertEqual(serialized["runtime"]["device"], "cpu")
        self.assertEqual(serialized["runtime"]["compute_type"], "int8")
        self.assertEqual(serialized["parameters"]["beam_size"], 5)
        self.assertEqual(serialized["segments"][0]["text"], "raw")
        self.assertEqual(serialized["quality"], {})


class EngineBehaviorTests(unittest.TestCase):
    def test_missing_local_model_writes_failure_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            output = root / "output"
            write_test_wav(audio)

            def missing_model(*_args, **_kwargs):
                raise FileNotFoundError("model cache is incomplete")

            with self.assertRaisesRegex(EngineExecutionError, "转录失败"):
                transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2),
                    output_dir=output,
                    model_factory=missing_model,
                    run_id="failure-case",
                )

            failure = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["error"]["type"], "FileNotFoundError")
            self.assertFalse((output / "transcript.md").exists())

    def test_success_preserves_source_and_raw_segment_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            before = audio.read_bytes()
            before_hash = hashlib.sha256(before).hexdigest()
            received: dict[str, object] = {}

            class FakeModel:
                def transcribe(self, path: str, **kwargs):
                    received["audio_samples"] = path
                    received["transcribe_options"] = kwargs
                    return iter([
                        SimpleNamespace(start=0.0, end=0.4, text=" 原句一 "),
                        SimpleNamespace(start=0.4, end=0.9, text="<script>alert(1)</script>"),
                    ]), SimpleNamespace(language="zh", language_probability=0.99)

            def fake_factory(model: str, **kwargs):
                received["model"] = model
                received["factory_options"] = kwargs
                return FakeModel()

            transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2),
                output_dir=root,
                model_factory=fake_factory,
                run_id="success-case",
            )

            self.assertEqual(audio.read_bytes(), before)
            self.assertEqual(received["model"], "small")
            self.assertEqual(received["factory_options"]["device"], "cpu")
            self.assertEqual(received["factory_options"]["compute_type"], "int8")
            self.assertIs(received["factory_options"]["local_files_only"], True)
            self.assertEqual(received["transcribe_options"]["language"], "zh")
            self.assertIs(received["transcribe_options"]["vad_filter"], False)
            self.assertEqual(received["transcribe_options"]["no_speech_threshold"], 0.6)
            self.assertEqual(received["audio_samples"].shape, (16_000,))

            written = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "success")
            self.assertEqual(written["schema_version"], "1.3")
            self.assertNotIn("source", written)
            self.assertEqual(written["audio"]["sha256"], before_hash)
            self.assertEqual(written["audio"]["media_type"], "audio")
            self.assertEqual(written["audio"]["audio_stream_index"], 0)
            self.assertEqual(written["model"]["requested"], "small")
            self.assertIn(written["model"]["revision"]["status"], {"resolved", "unknown"})
            self.assertEqual(written["runtime"]["compute_type"], "int8")
            self.assertEqual(written["runtime"]["device_requested"], "cpu")
            self.assertTrue(written["runtime"]["cpu_model"])
            self.assertIn("media_decode_seconds", written["timing"])
            self.assertEqual(written["segments"][0]["text"], " 原句一 ")
            self.assertEqual(written["segments"][1]["text"], "<script>alert(1)</script>")
            self.assertEqual(written["quality"]["assessment"], "text_available")
            self.assertEqual(
                written["parameters"]["input_max_duration_seconds"],
                DEFAULT_MAX_AUDIO_DURATION_SECONDS,
            )
            markdown = transcript.read_text(encoding="utf-8")
            self.assertIn("00:00:00.000 – 00:00:00.400", markdown)
            self.assertIn(" 原句一 ", markdown)
            self.assertIn("&lt;script&gt;alert\\(1\\)&lt;/script&gt;", markdown)
            self.assertNotIn("<script>", markdown)

    def test_vad_empty_result_is_no_speech_state_not_model_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            output = root / "output"
            write_test_wav(audio)

            class FakeModel:
                def transcribe(self, _samples, **options):
                    self.vad_filter = options["vad_filter"]
                    return iter(()), SimpleNamespace(language="zh", language_probability=0.9)

            fake = FakeModel()
            transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2, vad_filter=True),
                output_dir=output,
                model_factory=lambda *_args, **_kwargs: fake,
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            markdown = transcript.read_text(encoding="utf-8")

        self.assertTrue(fake.vad_filter)
        self.assertEqual(result.status, "no_speech_detected")
        self.assertEqual(evidence["status"], "no_speech_detected")
        self.assertEqual(evidence["quality"]["assessment"], "no_speech_detected")
        self.assertEqual(evidence["segments"], [])
        self.assertIn("不证明音频中绝对没有语音", markdown)
        self.assertNotIn("### 00:00", markdown)

    def test_repeated_and_high_no_speech_signals_mark_uncertain_without_deleting_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            output = root / "output"
            write_test_wav(audio)

            class FakeModel:
                def transcribe(self, _samples, **_options):
                    return iter([
                        SimpleNamespace(start=0.0, end=0.4, text="重复原句", no_speech_prob=0.9),
                        SimpleNamespace(start=0.4, end=0.8, text="重复原句", no_speech_prob=0.9),
                    SimpleNamespace(start=0.8, end=1.0, text="正常分段保留", no_speech_prob=0.1),
                    ]), SimpleNamespace(language="zh", language_probability=0.9)

            transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2),
                output_dir=output,
                model_factory=lambda *_args, **_kwargs: FakeModel(),
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            markdown = transcript.read_text(encoding="utf-8")

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(evidence["status"], "uncertain")
        self.assertEqual(
            [item["text"] for item in evidence["segments"]],
            ["重复原句", "重复原句", "正常分段保留"],
        )
        self.assertEqual(evidence["quality"]["review_scope"], "segments")
        self.assertEqual(evidence["quality"]["affected_segment_indices"], [0, 1])
        self.assertEqual(evidence["quality"]["affected_segment_count"], 2)
        self.assertIn("其他分段不会因此自动判错", evidence["quality"]["note"])
        warning_codes = {item["code"] for item in evidence["quality"]["warnings"]}
        self.assertEqual(
            warning_codes,
            {"model_no_speech_probability_at_threshold", "identical_segment_text_repeated"},
        )
        self.assertEqual(markdown.count("重复原句"), 2)
        self.assertIn("质量提示", markdown)

    def test_empty_result_without_vad_is_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)

            class FakeModel:
                def transcribe(self, _samples, **_options):
                    return iter(()), SimpleNamespace(language="zh", language_probability=0.9)

            _transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2),
                output_dir=root / "output",
                model_factory=lambda *_args, **_kwargs: FakeModel(),
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(evidence["quality"]["assessment"], "uncertain")
        self.assertEqual(
            evidence["quality"]["warnings"][0]["code"],
            "model_returned_no_usable_text_without_vad",
        )

    def test_model_timestamp_overrun_is_uncertain_and_raw_segment_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)

            class FakeModel:
                def transcribe(self, _samples, **_options):
                    return iter([
                        SimpleNamespace(start=0.5, end=1.2, text="保留时间戳原始文本", no_speech_prob=0.1),
                    ]), SimpleNamespace(language="zh", language_probability=0.9)

            transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2),
                output_dir=root / "output",
                model_factory=lambda *_args, **_kwargs: FakeModel(),
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            markdown = transcript.read_text(encoding="utf-8")

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(evidence["segments"][0]["end"], 1.2)
        self.assertIn("segment_timestamp_outside_audio", {
            warning["code"] for warning in evidence["quality"]["warnings"]
        })
        self.assertIn("保留时间戳原始文本", markdown)
        self.assertIn("质量提示", markdown)

    def test_invalid_model_language_probability_is_written_as_null_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            output = root / "output"

            class FakeModel:
                def transcribe(self, _samples, **_kwargs):
                    return iter([SimpleNamespace(start=0.0, end=0.5, text="ok")]), SimpleNamespace(
                        language="zh", language_probability=float("nan")
                    )

            _transcript, evidence_path, result = transcribe_to_artifacts(
                audio,
                config=RunConfig(model="small", cpu_threads=2),
                output_dir=output,
                model_factory=lambda *_args, **_kwargs: FakeModel(),
                run_id="nan-probability",
            )
            raw_json = evidence_path.read_text(encoding="utf-8")
            evidence = json.loads(raw_json)

        self.assertEqual(result.status, "success")
        self.assertIsNone(evidence["language"]["probability"])
        self.assertNotIn("NaN", raw_json)

    def test_existing_output_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            existing = root / "transcript.md"
            existing.write_text("keep this", encoding="utf-8")

            def should_not_run(*_args, **_kwargs):
                self.fail("model must not run when output already exists")

            with self.assertRaises(OutputConflictError):
                transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2),
                    output_dir=root,
                    model_factory=should_not_run,
                )
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep this")

    def test_invalid_run_id_is_rejected_before_decoding_or_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            output = root / "output"
            for invalid_run_id in ("../outside", "", 0):
                with self.subTest(run_id=invalid_run_id):
                    with patch("video_transcript_workbench.engine.inspect_audio") as inspect:
                        with self.assertRaisesRegex(ValueError, "run_id"):
                            transcribe_to_artifacts(
                                audio,
                                config=RunConfig(model="small", cpu_threads=2),
                                output_dir=output,
                                run_id=invalid_run_id,
                            )
                    inspect.assert_not_called()
            self.assertFalse(output.exists())

    def test_concurrent_same_output_reports_conflict_without_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            output = root / "output"
            barrier = threading.Barrier(2)

            class FakeModel:
                def transcribe(self, _audio, **_kwargs):
                    barrier.wait(timeout=5)
                    return iter([SimpleNamespace(start=0.0, end=0.5, text="same input")]), SimpleNamespace(
                        language="zh", language_probability=1.0
                    )

            def run_one(run_id: str) -> str:
                try:
                    transcribe_to_artifacts(
                        audio,
                        config=RunConfig(model="small", cpu_threads=2),
                        output_dir=output,
                        model_factory=lambda *_args, **_kwargs: FakeModel(),
                        run_id=run_id,
                    )
                    return "success"
                except (OutputConflictError, FileExistsError):
                    return "conflict"
                except Exception as exc:
                    return f"unexpected:{type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run_one, ("run-a", "run-b")))

            self.assertCountEqual(results, ["success", "conflict"])
            self.assertTrue((output / "transcript.md").is_file())
            self.assertTrue((output / "evidence.json").is_file())
            self.assertEqual(len(list(output.iterdir())), 2)

    def test_configured_duration_limit_stops_before_model_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            factory = unittest.mock.Mock()
            with self.assertRaisesRegex(AudioInputError, "超过限制"):
                transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2),
                    output_dir=root / "output",
                    max_duration_seconds=0.5,
                    model_factory=factory,
                )
            factory.assert_not_called()

    def test_model_file_exists_error_is_recorded_as_model_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            output = root / "output"

            def fail_during_model_initialization(*_args, **_kwargs):
                raise FileExistsError("model initialization conflict")

            with self.assertRaisesRegex(EngineExecutionError, "model initialization conflict"):
                transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2),
                    output_dir=output,
                    model_factory=fail_during_model_initialization,
                )
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "failed")
            self.assertEqual(evidence["error"]["type"], "FileExistsError")
            self.assertFalse((output / "transcript.md").exists())

    def test_auto_cuda_failure_retries_same_model_on_cpu_and_records_fallback(self) -> None:
        from video_transcript_workbench.device import DevicePlan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            calls: list[tuple[str, str, str]] = []

            class FakeModel:
                def transcribe(self, _samples, **_kwargs):
                    return iter([SimpleNamespace(start=0.0, end=0.5, text="fallback raw")]), SimpleNamespace(
                        language="zh", language_probability=1.0
                    )

            def factory(model: str, **kwargs):
                calls.append((model, kwargs["device"], kwargs["compute_type"]))
                if kwargs["device"] == "cuda":
                    raise RuntimeError("cuDNN initialization failed")
                return FakeModel()

            with patch(
                "video_transcript_workbench.engine.resolve_device",
                return_value=DevicePlan(
                    requested="auto",
                    device="cuda",
                    compute_type="float16",
                    selection={"reason": "cuda_available_to_ctranslate2"},
                ),
            ):
                _transcript, evidence_path, _result = transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2, device="auto"),
                    output_dir=root / "output",
                    model_factory=factory,
                )

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(calls, [("small", "cuda", "float16"), ("small", "cpu", "int8")])
        self.assertEqual(evidence["model"]["requested"], "small")
        self.assertEqual(evidence["runtime"]["device_requested"], "auto")
        self.assertEqual(evidence["runtime"]["device"], "cpu")
        self.assertEqual(evidence["runtime"]["compute_type"], "int8")
        self.assertEqual(
            evidence["runtime"]["device_selection"]["fallback"]["reason"],
            "cuda_runtime_or_kernel_failure",
        )

    def test_auto_cuda_inference_oom_retries_same_model_on_cpu(self) -> None:
        from video_transcript_workbench.device import DevicePlan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            calls: list[tuple[str, str]] = []

            class GpuModel:
                def transcribe(self, _samples, **_kwargs):
                    raise RuntimeError("CUDA out of memory PRIVATE_PATH_SENTINEL")

            class CpuModel:
                def transcribe(self, _samples, **_kwargs):
                    return iter([SimpleNamespace(start=0.0, end=0.5, text="cpu retry")]), SimpleNamespace(
                        language="zh", language_probability=1.0
                    )

            def factory(model: str, **kwargs):
                calls.append((model, kwargs["device"]))
                return GpuModel() if kwargs["device"] == "cuda" else CpuModel()

            with patch(
                "video_transcript_workbench.engine.resolve_device",
                return_value=DevicePlan("auto", "cuda", "float16", {"reason": "cuda_available"}),
            ):
                _transcript, evidence_path, _result = transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="small", cpu_threads=2, device="auto"),
                    output_dir=root / "output",
                    model_factory=factory,
                )

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(calls, [("small", "cuda"), ("small", "cpu")])
        self.assertEqual(evidence["runtime"]["device"], "cpu")
        self.assertEqual(
            evidence["runtime"]["device_selection"]["fallback"]["phase"],
            "inference",
        )
        self.assertNotIn("PRIVATE_PATH_SENTINEL", json.dumps(evidence))

    def test_auto_large_model_cuda_fallback_warns_before_cpu_retry(self) -> None:
        from video_transcript_workbench.device import DevicePlan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            calls: list[tuple[str, str]] = []

            class CpuModel:
                def transcribe(self, _samples, **_kwargs):
                    return iter([SimpleNamespace(start=0.0, end=0.5, text="保留的原始文本")]), SimpleNamespace(
                        language="zh", language_probability=0.9
                    )

            def factory(model: str, **kwargs):
                calls.append((model, kwargs["device"]))
                if kwargs["device"] == "cuda":
                    raise RuntimeError("CUDA out of memory")
                return CpuModel()

            with patch(
                "video_transcript_workbench.engine.resolve_device",
                return_value=DevicePlan("auto", "cuda", "float16", {"reason": "cuda_available"}),
            ), redirect_stderr(io.StringIO()) as stderr:
                _transcript, evidence_path, result = transcribe_to_artifacts(
                    audio,
                    config=RunConfig(model="large-v3", cpu_threads=2, device="auto"),
                    output_dir=root / "output",
                    model_factory=factory,
                )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(calls, [("large-v3", "cuda"), ("large-v3", "cpu")])
        self.assertIn("CPU/int8", stderr.getvalue())
        self.assertIn("不会自动换成较小模型", stderr.getvalue())
        self.assertEqual(result.status, "success")
        self.assertEqual(evidence["model"]["requested"], "large-v3")

    def test_explicit_cuda_runtime_error_is_sanitized(self) -> None:
        from video_transcript_workbench.device import DevicePlan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)

            def failing_factory(*_args, **_kwargs):
                raise RuntimeError("CUDA initialization failed PRIVATE_PATH_SENTINEL")

            with patch(
                "video_transcript_workbench.engine.resolve_device",
                return_value=DevicePlan("cuda", "cuda", "float16", {"reason": "cuda_available"}),
            ):
                with self.assertRaisesRegex(EngineExecutionError, "详细运行环境信息已省略") as caught:
                    transcribe_to_artifacts(
                        audio,
                        config=RunConfig(model="small", cpu_threads=2, device="cuda"),
                        output_dir=root / "output",
                        model_factory=failing_factory,
                    )

            evidence_text = (root / "output" / "evidence.json").read_text(encoding="utf-8")

        self.assertNotIn("PRIVATE_PATH_SENTINEL", str(caught.exception))
        self.assertNotIn("PRIVATE_PATH_SENTINEL", evidence_text)

    def test_auto_does_not_retry_cpu_for_a_missing_model(self) -> None:
        from video_transcript_workbench.device import DevicePlan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "sample.wav"
            write_test_wav(audio)
            devices: list[str] = []

            def factory(_model: str, **kwargs):
                devices.append(kwargs["device"])
                raise FileNotFoundError("model.bin is missing")

            with patch(
                "video_transcript_workbench.engine.resolve_device",
                return_value=DevicePlan("auto", "cuda", "float16", {"reason": "cuda_available"}),
            ):
                with self.assertRaises(EngineExecutionError):
                    transcribe_to_artifacts(
                        audio,
                        config=RunConfig(model="large-v3", cpu_threads=2, device="auto"),
                        output_dir=root / "output",
                        model_factory=factory,
                    )
            evidence = json.loads((root / "output" / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(devices, ["cuda"])
        self.assertNotIn("fallback", evidence["runtime"]["device_selection"])
        self.assertEqual(evidence["model"]["requested"], "large-v3")


class CliDurationLimitTests(unittest.TestCase):
    def test_local_cli_has_safe_default_and_bounded_override(self) -> None:
        from video_transcript_workbench.cli import build_parser

        parser = build_parser()
        defaults = parser.parse_args([
            "sample.wav", "--model", "small", "--output-dir", "out",
        ])
        maximum = parser.parse_args([
            "sample.wav", "--model", "small", "--output-dir", "out",
            "--max-duration-seconds", str(MAX_AUDIO_DURATION_SECONDS),
        ])
        self.assertEqual(defaults.max_duration_seconds, DEFAULT_MAX_AUDIO_DURATION_SECONDS)
        self.assertEqual(defaults.device, "auto")
        self.assertEqual(maximum.max_duration_seconds, MAX_AUDIO_DURATION_SECONDS)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([
                    "sample.wav", "--model", "small", "--output-dir", "out",
                    "--max-duration-seconds", str(MAX_AUDIO_DURATION_SECONDS + 1),
                ])


class CliQualityMessageTests(unittest.TestCase):
    def test_uncertain_cli_message_reports_local_review_scope(self) -> None:
        from video_transcript_workbench.cli import main as local_cli_main

        evidence = SimpleNamespace(
            status="uncertain",
            quality={
                "review_scope": "segments",
                "affected_segment_count": 2,
            },
            segments=[SimpleNamespace(text="first"), SimpleNamespace(text="second")],
            to_dict=lambda: {
                "runtime": {"device": "cuda", "compute_type": "float16", "device_selection": {}}
            },
        )
        output = io.StringIO()
        with patch(
            "video_transcript_workbench.engine.transcribe_to_artifacts",
            return_value=(Path("transcript.md"), Path("evidence.json"), evidence),
        ), redirect_stdout(output):
            result = local_cli_main([
                "sample.wav", "--model", "small", "--output-dir", "out",
            ])

        self.assertEqual(result, 0)
        self.assertIn("质量提示涉及 2 个分段", output.getvalue())
        self.assertIn("优先复核 Evidence 指出的片段", output.getvalue())
        self.assertIn("实际设备：cuda / float16", output.getvalue())


if __name__ == "__main__":
    unittest.main()
