from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
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
            self.assertEqual(received["audio_samples"].shape, (16_000,))

            written = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "success")
            self.assertEqual(written["schema_version"], "1.1")
            self.assertNotIn("source", written)
            self.assertEqual(written["audio"]["sha256"], before_hash)
            self.assertEqual(written["audio"]["media_type"], "audio")
            self.assertEqual(written["audio"]["audio_stream_index"], 0)
            self.assertEqual(written["model"]["requested"], "small")
            self.assertIn(written["model"]["revision"]["status"], {"resolved", "unknown"})
            self.assertEqual(written["runtime"]["compute_type"], "int8")
            self.assertTrue(written["runtime"]["cpu_model"])
            self.assertIn("media_decode_seconds", written["timing"])
            self.assertEqual(written["segments"][0]["text"], " 原句一 ")
            self.assertEqual(written["segments"][1]["text"], "<script>alert(1)</script>")
            self.assertEqual(
                written["parameters"]["input_max_duration_seconds"],
                DEFAULT_MAX_AUDIO_DURATION_SECONDS,
            )
            markdown = transcript.read_text(encoding="utf-8")
            self.assertIn("00:00:00.000 – 00:00:00.400", markdown)
            self.assertIn(" 原句一 ", markdown)
            self.assertIn("&lt;script&gt;alert\\(1\\)&lt;/script&gt;", markdown)
            self.assertNotIn("<script>", markdown)

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
        self.assertEqual(maximum.max_duration_seconds, MAX_AUDIO_DURATION_SECONDS)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([
                    "sample.wav", "--model", "small", "--output-dir", "out",
                    "--max-duration-seconds", str(MAX_AUDIO_DURATION_SECONDS + 1),
                ])


if __name__ == "__main__":
    unittest.main()
