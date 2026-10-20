from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import av
import numpy as np
from yt_dlp import YoutubeDL

from video_transcript_workbench.download import (
    ALLOWED_DOWNLOAD_PROTOCOLS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_DURATION_SECONDS,
    MAX_ALLOWED_DURATION_SECONDS,
    DownloadError,
    DownloadLimitError,
    DownloadedMedia,
    UnsupportedDownloadedMedia,
    _inspect_download,
    _format_selector,
    _xhs_duration_from_info,
    _xhs_https_og_video_url,
    download_audio_to_directory,
    javascript_runtime_options,
)
from video_transcript_workbench.models import RunConfig
from video_transcript_workbench.online import UnsafeNetworkTarget
from video_transcript_workbench.workflow import decide_asr_fallback, run_link_transcription


VIDEO_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


def write_test_wav(path: Path, frames: int = 16_000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * frames)


def write_test_webm(path: Path) -> None:
    sample_rate = 48_000
    samples = (np.sin(np.arange(sample_rate, dtype=np.float32) * (2 * np.pi * 440 / sample_rate)) * 0.1)
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = sample_rate
    with av.open(str(path), mode="w", format="webm") as container:
        stream = container.add_stream("libopus", rate=sample_rate)
        stream.layout = "mono"
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def write_test_muxed_mp4(path: Path) -> None:
    with av.open(str(path), mode="w", format="mp4") as container:
        audio = container.add_stream("aac", rate=16_000)
        audio.layout = "mono"
        video = container.add_stream("mpeg4", rate=2)
        video.width = 64
        video.height = 64
        video.pix_fmt = "yuv420p"

        samples = (np.sin(np.arange(16_000, dtype=np.float32) * (2 * np.pi * 440 / 16_000)) * 0.1)
        audio_frame = av.AudioFrame.from_ndarray(
            samples.reshape(1, -1), format="fltp", layout="mono"
        )
        audio_frame.sample_rate = 16_000
        for packet in audio.encode(audio_frame):
            container.mux(packet)
        for packet in audio.encode(None):
            container.mux(packet)

        black = np.zeros((64, 64, 3), dtype=np.uint8)
        for index in range(2):
            frame = av.VideoFrame.from_ndarray(black, format="rgb24")
            frame.pts = index
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode(None):
            container.mux(packet)


class FakeSubtitleClient:
    def __init__(self, info: dict, subtitle: bytes = b""):
        self.info = info
        self.subtitle = subtitle
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.info.get("raise"):
            raise self.info["raise"]
        return self.info

    def urlopen(self, _url: str):
        return FakeResponse(self.subtitle)


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, size: int = -1):
        return self.data if size < 0 else self.data[:size]

    def geturl(self):
        return "https://www.youtube.com/subtitle.vtt"

    def close(self):
        pass


def video_info(*, subtitles: dict | None = None, duration: float = 1.0) -> dict:
    return {
        "id": "BaW_jenozKc",
        "title": "test title",
        "duration": duration,
        "extractor_key": "Youtube",
        "webpage_url": VIDEO_URL,
        "subtitles": subtitles or {},
        "automatic_captions": {},
    }


def fake_download_factory(source: Path | None = None, capture: dict | None = None):
    def adapter(_url: str, *, directory: Path, max_duration_seconds: int, max_download_bytes: int):
        if capture is not None:
            capture["directory"] = directory
            capture["url"] = _url
            capture["limits"] = (max_duration_seconds, max_download_bytes)
        path = Path(directory) / ("media" + (source.suffix if source else ".wav"))
        if source:
            shutil.copyfile(source, path)
        else:
            write_test_wav(path)
        duration, container_format, codec_name, has_video_stream = _inspect_download(path)
        return DownloadedMedia(
            path=path,
            extension=path.suffix,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            duration_seconds=duration,
            container_format=container_format,
            audio_codec=codec_name,
            extractor="Youtube",
            yt_dlp_version="test",
            has_video_stream=has_video_stream,
        )
    return adapter


def fake_asr_success(*_args, **kwargs):
    path = Path(_args[0])
    from video_transcript_workbench.artifacts import inspect_audio

    audio = inspect_audio(path).metadata
    return {
        "status": "success",
        "audio": {
            "file_name": audio.file_name,
            "sha256": audio.sha256,
            "duration_seconds": audio.duration_seconds,
        },
        "model": {"requested": kwargs["config"].model},
        "runtime": {"device": "cpu", "compute_type": "int8"},
        "language": {"detected": "zh"},
        "segments": [{"start": 0.0, "end": 0.5, "text": "原始 ASR 文本"}],
        "software": {"faster_whisper": "test"},
        "parameters": {"beam_size": kwargs["config"].beam_size},
        "timing": {"inference_seconds": 0.1},
    }


def subtitle_factory(info: dict, payload: bytes = b""):
    client = FakeSubtitleClient(info, payload)
    return lambda *_args: client, client


class FallbackPolicyTests(unittest.TestCase):
    def test_safe_missing_states_allow_asr_and_network_or_access_states_stop(self) -> None:
        for status in (
            "no_available_subtitles", "no_matching_language", "empty_subtitles",
            "unsupported_subtitle_format",
        ):
            self.assertTrue(decide_asr_fallback(status).allowed)
        for status in (
            "proxy_unavailable", "network_error", "access_or_auth_restricted",
            "unsafe_redirect_or_resource", "unsupported_url", "extractor_error",
        ):
            self.assertFalse(decide_asr_fallback(status).allowed)

    def test_suspect_subtitles_only_fall_back_when_explicitly_enabled(self) -> None:
        for status in ("incomplete_subtitles", "subtitle_parse_failed"):
            self.assertFalse(decide_asr_fallback(status).allowed)
            decision = decide_asr_fallback(status, fallback_on_suspect=True)
            self.assertTrue(decision.allowed)
            self.assertEqual(decision.policy, "explicit_suspect_fallback")


class DownloadAdapterTests(unittest.TestCase):
    def test_download_refuses_unresolved_share_links_before_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "download"
            target.mkdir()
            with self.assertRaises(DownloadError) as caught:
                download_audio_to_directory(
                    "https://v.douyin.com/AbC123?share_token=secret",
                    directory=target,
                    client_factory=lambda *_args: self.fail("unresolved share links must not access network"),
                )
        self.assertEqual(caught.exception.category, "unsupported_url")

    def test_download_options_pin_native_audio_only_transport_and_disable_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            options_seen: dict = {}
            source = root / "source.wav"
            write_test_wav(source)
            (root / "download").mkdir()

            class FakeDownloadClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, **kwargs):
                    self.kwargs = kwargs
                    shutil.copyfile(source, root / "download" / "media.wav")
                    return {"duration": 1.0, "extractor_key": "Youtube"}

            def factory(_platform, options):
                options_seen.update(options)
                return FakeDownloadClient()

            media = download_audio_to_directory(
                VIDEO_URL,
                directory=root / "download",
                client_factory=factory,
            )
            self.assertEqual(media.extension, ".wav")
            self.assertEqual(media.duration_seconds, 1.0)
            self.assertTrue(options_seen["ignoreconfig"])
            self.assertIsNone(options_seen["cookiefile"])
            self.assertIsNone(options_seen["cookiesfrombrowser"])
            self.assertFalse(options_seen["usenetrc"])
            self.assertFalse(options_seen["geo_bypass"])
            self.assertEqual(options_seen["external_downloader"]["default"], "native")
            self.assertEqual(options_seen["concurrent_fragment_downloads"], 1)
            self.assertEqual(options_seen["max_filesize"], DEFAULT_MAX_DOWNLOAD_BYTES)
            self.assertEqual(options_seen["format"], "bestaudio[protocol=https]/bestaudio[protocol=http_dash_segments]/bestaudio[protocol=m3u8_native]")
            self.assertEqual(options_seen["remote_components"], [])
            with YoutubeDL(options_seen) as ydl:
                selector = ydl.build_format_selector(options_seen["format"])
                for protocol in ALLOWED_DOWNLOAD_PROTOCOLS:
                    synthetic_format = {
                        "format_id": protocol,
                        "ext": "m4a",
                        "acodec": "aac",
                        "vcodec": "none",
                        "protocol": protocol,
                        "url": "https://r1.googlevideo.com/audio",
                    }
                    selected = list(selector({
                        "formats": [synthetic_format],
                        "extractor": "Youtube",
                        "extractor_key": "Youtube",
                        "id": "BaW_jenozKc",
                        "title": "safe",
                    }))
                    self.assertEqual([item["protocol"] for item in selected], [protocol])

    def test_xhs_selector_uses_muxed_mp4_only_after_audio_only_formats(self) -> None:
        xhs_selector = _format_selector("xiaohongshu")
        other_selector = _format_selector("youtube")
        muxed = {
            "format_id": "muxed-mp4",
            "ext": "mp4",
            "acodec": "aac",
            "vcodec": "avc1",
            "protocol": "https",
            "url": "https://sns-video-bd.xhscdn.com/video",
        }
        audio_only = {
            "format_id": "audio-m4a",
            "ext": "m4a",
            "acodec": "aac",
            "vcodec": "none",
            "protocol": "https",
            "url": "https://sns-video-bd.xhscdn.com/audio",
        }
        with YoutubeDL({"quiet": True}) as ydl:
            xhs = ydl.build_format_selector(xhs_selector)
            other = ydl.build_format_selector(other_selector)
            xhs_muxed = list(xhs({"formats": [muxed], "extractor": "XiaoHongShu"}))
            other_muxed = list(other({"formats": [muxed], "extractor": "Youtube"}))
            xhs_audio = list(xhs({"formats": [audio_only, muxed], "extractor": "XiaoHongShu"}))
        self.assertEqual([item["format_id"] for item in xhs_muxed], ["muxed-mp4"])
        self.assertEqual(other_muxed, [])
        self.assertEqual([item["format_id"] for item in xhs_audio], ["audio-m4a"])

    def test_xhs_downloader_accepts_audio_bearing_mp4_and_records_media_kind(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        url = f"https://www.xiaohongshu.com/explore/{video_id}"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            write_test_muxed_mp4(source)
            target = root / "download"
            target.mkdir()
            options_seen: dict = {}

            class FakeClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, **_kwargs):
                    shutil.copyfile(source, target / "media.mp4")
                    return {
                        "duration": 1.0,
                        "extractor_key": "XiaoHongShu",
                        "webpage_url": url,
                    }

            def factory(_platform, options):
                options_seen.update(options)
                return FakeClient()

            media = download_audio_to_directory(url, directory=target, client_factory=factory)
            duration, container_format, codec, has_video = _inspect_download(media.path)

        self.assertEqual(media.extension, ".mp4")
        self.assertGreater(duration, 0.9)
        self.assertEqual(codec, "aac")
        self.assertTrue(has_video)
        self.assertIn("video_with_audio", media.evidence()["media_kind"])
        self.assertIn("best[acodec!=none][vcodec!=none][ext=mp4]", options_seen["format"])
        self.assertIn("mp4", container_format or "")

    def test_xhs_https_og_video_format_is_selected_without_persisting_its_url(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"
        candidate = "https://sns-video-bd.xhscdn.com/private.mp4?sign=synthetic-signature"
        page_html = (
            '<meta property="og:video" content="'
            + candidate
            + '">'
        ).encode()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            write_test_muxed_mp4(source)
            target = root / "download"
            target.mkdir()
            options_seen: dict = {}
            captured: dict = {}

            class Response:
                def read(self, size: int = -1):
                    return page_html if size < 0 else page_html[:size]

                def geturl(self):
                    return page_url

                def close(self):
                    pass

            class FakeClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, **kwargs):
                    self.assert_download_false = kwargs["download"] is False
                    self.assert_process_false = kwargs["process"] is False
                    return {
                        "id": video_id,
                        "title": "test title",
                        "duration": None,
                        "extractor_key": "XiaoHongShu",
                        "webpage_url": page_url,
                        "formats": [{
                            "format_id": "http-only",
                            "url": "http://sns-video-bd.xhscdn.com/private.mp4",
                            "ext": "mp4",
                            "protocol": "http",
                            "acodec": "aac",
                            "vcodec": "h264",
                            "duration": 1.0,
                        }, {
                            "format_id": "https-video-only",
                            "url": "https://sns-video-bd.xhscdn.com/video-only.mp4",
                            "ext": "mp4",
                            "protocol": "https",
                            "acodec": "none",
                            "vcodec": "h264",
                            "duration": 1.0,
                        }],
                    }

                def urlopen(self, _url):
                    return Response()

                def process_ie_result(self, info, *, download):
                    self.assert_download_true = download is True
                    with YoutubeDL({"quiet": True}) as ydl:
                        selector = ydl.build_format_selector(options_seen["format"])
                        selected = list(selector(info))
                    captured["format_id"] = selected[0]["format_id"]
                    captured["url"] = selected[0]["url"]
                    captured["duration"] = selected[0]["duration"]
                    shutil.copyfile(source, target / "media.mp4")
                    info["duration"] = selected[0]["duration"]
                    return info

            client = FakeClient()
            media = download_audio_to_directory(
                page_url,
                directory=target,
                client_factory=lambda _platform, options: (options_seen.update(options) or client),
            )
            serialized_evidence = json.dumps(media.evidence())

        self.assertTrue(client.assert_download_false)
        self.assertTrue(client.assert_process_false)
        self.assertTrue(client.assert_download_true)
        self.assertEqual(captured["format_id"], "xhs-og-video-https")
        self.assertEqual(captured["url"], candidate)
        self.assertEqual(captured["duration"], 1.0)
        self.assertNotIn("synthetic-signature", serialized_evidence)
        self.assertEqual(media.extension, ".mp4")
        self.assertTrue(media.has_video_stream)

    def test_xhs_og_video_candidate_must_be_https_and_on_allowlisted_host(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"

        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload

            def read(self, size: int = -1):
                return self.payload if size < 0 else self.payload[:size]

            def geturl(self):
                return page_url

            def close(self):
                pass

        class FakeClient:
            def __init__(self, payload: bytes):
                self.payload = payload

            def urlopen(self, _url):
                return Response(self.payload)

        for candidate in (
            "http://sns-video-bd.xhscdn.com/video.mp4",
            "https://media.example.invalid/video.mp4",
        ):
            payload = f'<meta property="og:video" content="{candidate}">'.encode()
            self.assertIsNone(_xhs_https_og_video_url(
                FakeClient(payload), page_url, expected_video_id=video_id
            ))

    def test_xhs_empty_or_missing_formats_still_tries_page_https_media(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"
        candidate = "https://sns-video-bd.xhscdn.com/page-video.mp4?sign=private"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            write_test_muxed_mp4(source)

            for formats_state in ("empty", "missing"):
                with self.subTest(formats_state=formats_state):
                    target = root / f"download-{formats_state}"
                    target.mkdir()
                    options_seen: dict = {}

                    class FakeClient:
                        def __enter__(self):
                            return self

                        def __exit__(self, *_args):
                            return False

                        def extract_info(self, _url, **kwargs):
                            assert kwargs["download"] is False
                            info = {
                                "id": video_id,
                                "duration": 1.0,
                                "extractor_key": "XiaoHongShu",
                                "webpage_url": page_url,
                            }
                            if formats_state == "empty":
                                info["formats"] = []
                            return info

                        def process_ie_result(self, info, *, download):
                            assert download is True
                            assert info["formats"][0]["format_id"] == "xhs-og-video-https"
                            with YoutubeDL({"quiet": True}) as ydl:
                                selector = ydl.build_format_selector(options_seen["format"])
                                selected = list(selector(info))
                            assert selected[0]["url"] == candidate
                            shutil.copyfile(source, target / "media.mp4")
                            info["duration"] = selected[0]["duration"]
                            return info

                    with patch(
                        "video_transcript_workbench.download._xhs_https_og_video_url",
                        return_value=candidate,
                    ):
                        media = download_audio_to_directory(
                            page_url,
                            directory=target,
                            client_factory=lambda _platform, options: (
                                options_seen.update(options) or FakeClient()
                            ),
                        )
                    self.assertEqual(media.extension, ".mp4")
                    self.assertTrue(media.has_video_stream)
                    self.assertNotIn("private", json.dumps(media.evidence()))

    def test_xhs_og_video_candidate_is_bounded_by_actual_download_size(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, **_kwargs):
                return {
                    "id": video_id,
                    "duration": 1.0,
                    "extractor_key": "XiaoHongShu",
                    "webpage_url": page_url,
                    "formats": [{
                        "format_id": "https-mp4",
                        "url": "https://sns-video-bd.xhscdn.com/video.mp4?sign=private",
                        "ext": "mp4",
                        "protocol": "https",
                        "acodec": "aac",
                        "vcodec": "h264",
                        "duration": 1.0,
                    }],
                }

            def process_ie_result(self, info, *, download):
                assert download is True
                target.joinpath("media.mp4").write_bytes(b"0123456789abcdef-more")
                return info

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "download"
            target.mkdir()
            with self.assertRaises(DownloadLimitError) as caught:
                download_audio_to_directory(
                    page_url,
                    directory=target,
                    max_download_bytes=16,
                    client_factory=lambda *_args: FakeClient(),
                )
        self.assertEqual(caught.exception.category, "download_limit_exceeded")

    def test_xhs_og_video_rejects_corrupt_mp4_after_download(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, **_kwargs):
                return {
                    "id": video_id,
                    "duration": 1.0,
                    "extractor_key": "XiaoHongShu",
                    "webpage_url": page_url,
                    "formats": [{
                        "format_id": "https-mp4",
                        "url": "https://sns-video-bd.xhscdn.com/video.mp4?sign=private",
                        "ext": "mp4",
                        "protocol": "https",
                        "acodec": "aac",
                        "vcodec": "h264",
                        "duration": 1.0,
                    }],
                }

            def process_ie_result(self, info, *, download):
                assert download is True
                target.joinpath("media.mp4").write_bytes(b"not an mp4")
                return info

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "download"
            target.mkdir()
            with self.assertRaises(UnsupportedDownloadedMedia) as caught:
                download_audio_to_directory(
                    page_url,
                    directory=target,
                    client_factory=lambda *_args: FakeClient(),
                )
        self.assertEqual(caught.exception.category, "unsupported_media")

    def test_xhs_og_video_rejects_overlong_source_before_page_fallback(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        page_url = f"https://www.xiaohongshu.com/explore/{video_id}"

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, **_kwargs):
                return {
                    "id": video_id,
                    "duration": None,
                    "extractor_key": "XiaoHongShu",
                    "webpage_url": page_url,
                    "formats": [{
                        "url": "http://sns-video-bd.xhscdn.com/video.mp4",
                        "ext": "mp4",
                        "protocol": "http",
                        "duration": 61.0,
                    }],
                }

            def urlopen(self, _url):
                raise AssertionError("overlong videos must be rejected before page fallback")

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "download"
            target.mkdir()
            with self.assertRaises(DownloadLimitError) as caught:
                download_audio_to_directory(
                    page_url,
                    directory=target,
                    max_duration_seconds=60,
                    client_factory=lambda *_args: FakeClient(),
                )
            self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(caught.exception.category, "download_limit_exceeded")

    def test_xhs_duration_is_recovered_from_extracted_stream_formats(self) -> None:
        self.assertEqual(
            _xhs_duration_from_info({"formats": [{"duration": 12.5}, {"duration": 12.4}]}),
            12.5,
        )
        self.assertIsNone(_xhs_duration_from_info({"formats": [{"duration": None}]}))

    def test_other_platforms_reject_muxed_mp4_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            write_test_muxed_mp4(source)
            target = root / "download"
            target.mkdir()

            class FakeClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, **_kwargs):
                    shutil.copyfile(source, target / "media.mp4")
                    return {"duration": 1.0, "extractor_key": "Youtube"}

            with self.assertRaises(DownloadError) as caught:
                download_audio_to_directory(
                    VIDEO_URL,
                    directory=target,
                    client_factory=lambda *_args: FakeClient(),
                )
        self.assertEqual(caught.exception.category, "unsupported_media")

    def test_duration_filter_rejects_unknown_or_overlong_media_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "private"
            target.mkdir()
            captured: dict = {}

            class FakeClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, **kwargs):
                    return {"duration": 3_601, "extractor_key": "Youtube"}

                def check_filter(self, info):
                    return captured["match_filter"](info)

            def factory(_platform, options):
                captured["match_filter"] = options["match_filter"]
                client = FakeClient()
                original_extract_info = client.extract_info

                def extract_info(_url, **kwargs):
                    captured["reason"] = client.check_filter({"duration": 3_601})
                    return original_extract_info(_url, **kwargs)

                client.extract_info = extract_info
                return client

            with self.assertRaises(DownloadError) as caught:
                download_audio_to_directory(
                    VIDEO_URL,
                    directory=target,
                    max_duration_seconds=3_600,
                    client_factory=factory,
                )
            self.assertEqual(caught.exception.category, "download_limit_exceeded")
            self.assertTrue(captured["reason"])
            self.assertEqual(list(target.iterdir()), [])

    def test_limit_configuration_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            download_audio_to_directory(
                VIDEO_URL,
                directory=Path("."),
                max_duration_seconds=MAX_ALLOWED_DURATION_SECONDS + 1,
            )


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RunConfig(model="small", language="zh", cpu_threads=2)

    def run_workflow(self, root: Path, info: dict, *, payload: bytes = b"", **kwargs):
        factory, client = subtitle_factory(info, payload)
        result = run_link_transcription(
            VIDEO_URL,
            language="zh",
            config=self.config,
            output_dir=root / "output",
            subtitle_client_factory=factory,
            **kwargs,
        )
        return result, client

    def test_subtitle_success_writes_once_and_never_downloads_or_runs_asr(self) -> None:
        info = video_info(subtitles={"zh": [{"ext": "vtt", "url": "https://www.youtube.com/sub.vtt"}]})
        factory, _client = subtitle_factory(info, "WEBVTT\n\n00:00:00.000 --> 00:00:00.800\n字幕原句\n".encode())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            result = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=output,
                subtitle_client_factory=factory,
                download_adapter=lambda *_args, **_kwargs: self.fail("subtitle success must not download"),
                asr_runner=lambda *_args, **_kwargs: self.fail("subtitle success must not run ASR"),
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            markdown = (output / "transcript.md").read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        self.assertEqual(result["status"], "success")
        self.assertEqual(evidence["workflow"]["final_transcription_source"], "manual_subtitles")
        self.assertFalse(evidence["workflow"]["asr_executed"])
        self.assertEqual(evidence["segments"][0]["text"], "字幕原句")
        self.assertIn("字幕原句", markdown)
        self.assertEqual(evidence["schema_version"], "3.0")
        self.assertEqual(evidence["subtitle_attempt"]["schema_version"], "2.1")
        self.assertEqual(
            evidence["subtitle_attempt"]["subtitle"]["attempts"][0]["status"],
            "subtitle_success",
        )

    def test_no_subtitles_falls_back_and_preserves_subtitle_failure(self) -> None:
        factory, client = subtitle_factory(video_info())
        captured: dict = {}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            result = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=output,
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(capture=captured),
                asr_runner=fake_asr_success,
            )
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            markdown = (output / "transcript.md").read_text(encoding="utf-8")
        self.assertEqual(result["status"], "success")
        self.assertEqual(evidence["workflow"]["subtitle_attempt_status"], "no_available_subtitles")
        self.assertEqual(evidence["subtitle_attempt"]["status"], "no_available_subtitles")
        self.assertTrue(evidence["workflow"]["asr_executed"])
        self.assertEqual(evidence["workflow"]["final_transcription_source"], "local_asr")
        self.assertEqual(evidence["model"], {"requested": "small", "device": "cpu", "compute_type": "int8"})
        self.assertEqual(evidence["download"]["extension"], ".wav")
        self.assertEqual(evidence["segments"][0]["text"], "原始 ASR 文本")
        self.assertIn("local_asr", markdown)
        self.assertEqual(client.calls[0][1], {"download": False, "ie_key": "Youtube"})
        self.assertEqual(captured["url"], VIDEO_URL)
        self.assertFalse(captured["directory"].exists(), "private download dir should be removed")

    def test_resolved_douyin_share_link_uses_canonical_video_for_asr_fallback(self) -> None:
        video_id = "6961737553342991651"
        info = {
            "id": video_id,
            "title": "Douyin public test",
            "duration": 1.0,
            "extractor_key": "Douyin",
            "webpage_url": f"https://www.douyin.com/video/{video_id}?share_token=secret",
            "subtitles": {},
            "automatic_captions": {},
        }
        factory, client = subtitle_factory(info)
        captured: dict = {}
        with tempfile.TemporaryDirectory() as temp:
            result = run_link_transcription(
                "https://v.douyin.com/AbC123/?share_token=secret",
                language="zh",
                config=self.config,
                output_dir=Path(temp) / "output",
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(capture=captured),
                asr_runner=fake_asr_success,
            )
            evidence_text = (Path(temp) / "output" / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(client.calls[0][1], {"download": False, "ie_key": None})
        self.assertEqual(captured["url"], f"https://www.douyin.com/video/{video_id}")
        self.assertEqual(result["source"]["video_id"], video_id)
        self.assertNotIn("share_token", evidence_text)
        self.assertEqual(result["status"], "success")

    def test_xiaohongshu_share_token_is_carried_to_asr_but_not_saved(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        token = "private-share-token"
        url = f"https://www.xiaohongshu.com/explore/{video_id}?xsec_token={token}&source=share"
        info = {
            "id": video_id,
            "title": "Xiaohongshu public test",
            "duration": 1.0,
            "extractor_key": "XiaoHongShu",
            "webpage_url": f"https://www.xiaohongshu.com/explore/{video_id}",
            "subtitles": {},
            "automatic_captions": {},
        }
        factory, _client = subtitle_factory(info)
        captured: dict = {}
        with tempfile.TemporaryDirectory() as temp:
            result = run_link_transcription(
                url,
                language="zh",
                config=self.config,
                output_dir=Path(temp) / "output",
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(capture=captured),
                asr_runner=fake_asr_success,
            )
            evidence_text = (Path(temp) / "output" / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(
            captured["url"],
            f"https://www.xiaohongshu.com/explore/{video_id}?xsec_token={token}",
        )
        self.assertNotIn(token, evidence_text)
        self.assertEqual(result["status"], "success")

    def test_xhs_muxed_mp4_reaches_local_asr_and_is_recorded_truthfully(self) -> None:
        video_id = "6411cf99000000001300b6d9"
        url = f"https://www.xiaohongshu.com/explore/{video_id}"
        info = {
            "id": video_id,
            "title": "synthetic XHS media test",
            "duration": 1.0,
            "extractor_key": "XiaoHongShu",
            "webpage_url": url,
            "subtitles": {},
            "automatic_captions": {},
        }
        factory, _client = subtitle_factory(info)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            write_test_muxed_mp4(source)
            result = run_link_transcription(
                url,
                language="zh",
                config=self.config,
                output_dir=root / "output",
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(source),
                asr_runner=fake_asr_success,
            )
            evidence = json.loads(
                (root / "output" / "evidence.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(evidence["download"]["extension"], ".mp4")
        self.assertEqual(evidence["download"]["media_kind"], "video_with_audio")
        self.assertTrue(evidence["download"]["has_video_stream"])
        self.assertTrue(evidence["workflow"]["asr_executed"])
        self.assertEqual(evidence["workflow"]["final_transcription_source"], "local_asr")

    def test_no_matching_language_is_preserved_on_asr_success(self) -> None:
        factory, _client = subtitle_factory(video_info(subtitles={"fr": [{"ext": "vtt", "url": "https://www.youtube.com/fr.vtt"}]}))
        with tempfile.TemporaryDirectory() as temp:
            result = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=Path(temp) / "output",
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(),
                asr_runner=fake_asr_success,
            )
        self.assertEqual(result["subtitle_attempt"]["status"], "no_matching_language")
        self.assertEqual(result["workflow"]["subtitle_attempt_status"], "no_matching_language")
        self.assertEqual(result["status"], "success")

    def test_incomplete_subtitles_stop_by_default_and_explicit_policy_can_fall_back(self) -> None:
        info = video_info(
            subtitles={"zh": [{"ext": "srt", "url": "https://www.youtube.com/sub.srt"}]},
            duration=600,
        )
        payload = b"1\n00:00:01,000 --> 00:00:05,000\nOnly a small opening\n"
        factory, _client = subtitle_factory(info, payload)
        with tempfile.TemporaryDirectory() as temp:
            stopped = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=Path(temp) / "stopped",
                subtitle_client_factory=factory,
                download_adapter=lambda *_args, **_kwargs: self.fail("default policy must stop"),
                asr_runner=lambda *_args, **_kwargs: self.fail("default policy must stop"),
            )
            fallback_factory, _ = subtitle_factory(info, payload)
            allowed = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=Path(temp) / "allowed",
                subtitle_client_factory=fallback_factory,
                download_adapter=fake_download_factory(),
                asr_runner=fake_asr_success,
                fallback_on_suspect=True,
            )
        self.assertEqual(stopped["status"], "failed")
        self.assertEqual(stopped["workflow"]["fallback"]["policy"], "stop_on_suspect_subtitles")
        self.assertEqual(allowed["subtitle_attempt"]["status"], "incomplete_subtitles")
        self.assertEqual(allowed["workflow"]["fallback"]["policy"], "explicit_suspect_fallback")
        self.assertEqual(allowed["status"], "success")

    def test_network_permission_and_unsafe_url_failures_never_start_download(self) -> None:
        cases = (
            ("https://example.com/watch?v=x", None, "unsupported_url"),
            (VIDEO_URL, RuntimeError("ProxyError: connection refused"), "proxy_unavailable"),
            (VIDEO_URL, RuntimeError("HTTP Error 403: private video"), "access_or_auth_restricted"),
            (VIDEO_URL, UnsafeNetworkTarget("https://127.0.0.1/private"), "unsafe_redirect_or_resource"),
        )
        for index, (url, exception, expected) in enumerate(cases):
            info = video_info()
            if exception is not None:
                info["raise"] = exception
            factory, _client = subtitle_factory(info)
            with tempfile.TemporaryDirectory() as temp:
                result = run_link_transcription(
                    url,
                    language="zh",
                    config=self.config,
                    output_dir=Path(temp) / str(index),
                    subtitle_client_factory=factory,
                    download_adapter=lambda *_args, **_kwargs: self.fail("platform failure must not trigger download"),
                    asr_runner=lambda *_args, **_kwargs: self.fail("platform failure must not run ASR"),
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["workflow"]["subtitle_attempt_status"], expected)
            self.assertFalse(result["workflow"]["asr_executed"])

    def test_download_or_asr_failure_never_leaves_success_transcript(self) -> None:
        factory, _client = subtitle_factory(video_info())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "download-fail"

            def failed_download(*_args, **_kwargs):
                raise DownloadError("network is unavailable", category="network_error")

            result = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=output,
                subtitle_client_factory=factory,
                download_adapter=failed_download,
                asr_runner=lambda *_args, **_kwargs: self.fail("download failure must not run ASR"),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["category"], "network_error")
            self.assertFalse((output / "transcript.md").exists())

            factory2, _client = subtitle_factory(video_info())
            output2 = Path(temp) / "asr-fail"
            failed_asr = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=output2,
                subtitle_client_factory=factory2,
                download_adapter=fake_download_factory(),
                asr_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret URL must not appear")),
            )
            evidence_text = (output2 / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(failed_asr["status"], "failed")
        self.assertFalse((output2 / "transcript.md").exists())
        self.assertNotIn("secret URL", evidence_text)

    def test_decoded_duration_limit_failure_is_not_reported_as_asr_success(self) -> None:
        from video_transcript_workbench.artifacts import AudioInputError

        factory, _client = subtitle_factory(video_info())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "duration-limit"
            result = run_link_transcription(
                VIDEO_URL,
                language="zh",
                config=self.config,
                output_dir=output,
                subtitle_client_factory=factory,
                download_adapter=fake_download_factory(),
                asr_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AudioInputError("超过限制")),
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["asr_attempt"]["error"]["category"], "duration_limit_exceeded")
        self.assertFalse((output / "transcript.md").exists())

    def test_output_conflict_and_repeat_never_overwrite_existing_files(self) -> None:
        factory, _client = subtitle_factory(video_info())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            output.mkdir()
            saved = output / "evidence.json"
            saved.write_text("frozen", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                run_link_transcription(
                    VIDEO_URL,
                    language="zh",
                    config=self.config,
                    output_dir=output,
                    subtitle_client_factory=lambda *_args: self.fail("must validate output before network"),
                )
            self.assertEqual(saved.read_text(encoding="utf-8"), "frozen")

    def test_interrupted_final_write_rolls_back_transcript(self) -> None:
        from video_transcript_workbench.online import _write_online_artifacts
        import video_transcript_workbench.online as online

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            original_write = online._write_new_text_file

            def fail_evidence_write(path, text):
                if Path(path).name == "evidence.json":
                    raise OSError("simulated disk failure")
                return original_write(path, text)

            with patch.object(online, "_write_new_text_file", fail_evidence_write):
                with self.assertRaises(OSError):
                    _write_online_artifacts(
                        output,
                        {"run_id": "test-run", "status": "success"},
                        "transcript body",
                    )
            self.assertFalse((output / "transcript.md").exists())
            self.assertFalse((output / "evidence.json").exists())
            self.assertEqual(list(output.iterdir()), [])

    def test_webm_opus_is_detected_and_decoded_from_its_actual_container(self) -> None:
        from video_transcript_workbench.artifacts import inspect_audio

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.webm"
            write_test_webm(path)
            inspected = inspect_audio(path)
        self.assertIn("webm", inspected.metadata.container_format or "")
        self.assertEqual(inspected.metadata.audio_codec, "opus")
        self.assertGreater(inspected.metadata.duration_seconds, 0.9)

    def test_asr_duration_guard_checks_container_before_full_decode(self) -> None:
        from unittest.mock import patch
        from video_transcript_workbench.artifacts import AudioInputError, inspect_audio

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            write_test_wav(path)
            with patch("faster_whisper.audio.decode_audio", side_effect=AssertionError("decode should not run")):
                with self.assertRaisesRegex(AudioInputError, "超过限制"):
                    inspect_audio(path, max_duration_seconds=0.5)

    def test_duration_guard_stops_on_audio_frames_if_container_underreports(self) -> None:
        from fractions import Fraction
        from unittest.mock import patch
        from video_transcript_workbench.artifacts import AudioInputError, inspect_audio

        class FakeContainer:
            def __init__(self):
                self.stream = SimpleNamespace(
                    index=0,
                    time_base=Fraction(1, 16_000),
                    duration=4_000,
                    rate=16_000,
                    codec_context=SimpleNamespace(
                        codec=SimpleNamespace(name="pcm_s16le"),
                        sample_rate=16_000,
                        layout=SimpleNamespace(nb_channels=1),
                        format=SimpleNamespace(name="s16", bytes=2),
                    ),
                )
                self.streams = SimpleNamespace(audio=[self.stream], video=[])
                self.duration = int(0.25 * av.time_base)
                self.format = SimpleNamespace(name="wav")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def decode(self, _stream):
                return iter([SimpleNamespace(sample_rate=16_000, samples=16_000)])

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "media.wav"
            path.write_bytes(b"fixture")
            with patch("av.open", return_value=FakeContainer()), patch(
                "faster_whisper.audio.decode_audio",
                side_effect=AssertionError("full decode should not run"),
            ):
                with self.assertRaisesRegex(AudioInputError, "完整解码前停止"):
                    inspect_audio(path, max_duration_seconds=0.5)

    def test_node_is_explicitly_enabled_if_available_but_no_remote_components_are_enabled(self) -> None:
        options = javascript_runtime_options()
        if options:
            self.assertIn("node", options["js_runtimes"])
            self.assertTrue(options["js_runtimes"]["node"]["path"])
        self.assertGreater(DEFAULT_MAX_DURATION_SECONDS, 0)
        self.assertGreater(DEFAULT_MAX_DOWNLOAD_BYTES, 0)


if __name__ == "__main__":
    unittest.main()
