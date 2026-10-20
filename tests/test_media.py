from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import av
import numpy as np

from video_transcript_workbench.artifacts import AudioInputError, inspect_audio


def _audio_frame(seconds: int = 1) -> av.AudioFrame:
    sample_rate = 16_000
    sample_count = seconds * sample_rate
    phase = np.arange(sample_count, dtype=np.float32)
    samples = (np.sin(phase * (2 * np.pi * 440 / sample_rate)) * 0.15).astype(np.float32)
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = sample_rate
    return frame


def _write_compressed_audio(path: Path, *, container_format: str, codec: str) -> None:
    with av.open(str(path), mode="w", format=container_format) as output:
        stream = output.add_stream(codec, rate=16_000)
        stream.layout = "mono"
        if codec == "aac":
            frame = _audio_frame()
        else:
            sample_rate = 16_000
            phase = np.arange(sample_rate, dtype=np.float32)
            samples = (np.sin(phase * (2 * np.pi * 440 / sample_rate)) * 4000)
            frame = av.AudioFrame.from_ndarray(
                samples.astype(np.int16).reshape(1, -1),
                format="s16p",
                layout="mono",
            )
            frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)


def _write_mp4(path: Path, *, include_audio: bool) -> None:
    with av.open(str(path), mode="w", format="mp4") as output:
        audio_stream = None
        if include_audio:
            audio_stream = output.add_stream("aac", rate=16_000)
            audio_stream.layout = "mono"
        video_stream = output.add_stream("mpeg4", rate=2)
        video_stream.width = 64
        video_stream.height = 64
        video_stream.pix_fmt = "yuv420p"

        if audio_stream is not None:
            for packet in audio_stream.encode(_audio_frame()):
                output.mux(packet)
            for packet in audio_stream.encode(None):
                output.mux(packet)

        black = np.zeros((64, 64, 3), dtype=np.uint8)
        for index in range(6):
            frame = av.VideoFrame.from_ndarray(black, format="rgb24")
            frame.pts = index
            for packet in video_stream.encode(frame):
                output.mux(packet)
        for packet in video_stream.encode(None):
            output.mux(packet)


class MediaDecodeTests(unittest.TestCase):
    def test_wav_is_decoded_and_metadata_uses_audio_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tone.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16_000)
                target.writeframes(b"\0\0" * 16_000)

            inspected = inspect_audio(path)
            self.assertEqual(inspected.metadata.duration_seconds, 1.0)
            self.assertEqual(inspected.metadata.sample_width_bytes, 2)
            self.assertEqual(inspected.metadata.media_type, "audio")

    def test_mp3_and_m4a_are_decoded_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures = [
                (root / "tone.mp3", "mp3", "libmp3lame"),
                (root / "tone.m4a", "ipod", "aac"),
            ]
            for path, container_format, codec in fixtures:
                with self.subTest(extension=path.suffix):
                    _write_compressed_audio(path, container_format=container_format, codec=codec)
                    inspected = inspect_audio(path)
                    self.assertGreater(inspected.metadata.duration_seconds, 0.9)
                    self.assertEqual(inspected.metadata.media_type, "audio")
                    self.assertEqual(inspected.metadata.channels, 1)

    def test_mp4_audio_duration_is_not_container_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tone.mp4"
            _write_mp4(path, include_audio=True)
            inspected = inspect_audio(path)
            self.assertEqual(inspected.metadata.media_type, "video")
            self.assertEqual(inspected.metadata.audio_stream_index, 0)
            self.assertGreater(inspected.metadata.duration_seconds, 0.9)
            self.assertLess(inspected.metadata.duration_seconds, 1.2)
            self.assertGreater(inspected.metadata.container_duration_seconds or 0, 2.5)
            self.assertIn("container_index=0", inspected.metadata.transcription_source)

    def test_video_without_audio_and_corrupt_media_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            no_audio = root / "no-audio.mp4"
            corrupt = root / "corrupt.mp4"
            _write_mp4(no_audio, include_audio=False)
            corrupt.write_bytes(b"not an mp4")
            with self.assertRaisesRegex(AudioInputError, "没有音轨"):
                inspect_audio(no_audio)
            with self.assertRaisesRegex(AudioInputError, "无法解码"):
                inspect_audio(corrupt)


if __name__ == "__main__":
    unittest.main()
