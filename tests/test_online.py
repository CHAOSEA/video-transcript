from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
import urllib.request
from pathlib import Path

from video_transcript_workbench.online import (
    OnlineInputError,
    SubtitleParseError,
    _check_network_url,
    _allowed_hosts,
    _error_category,
    _reference_from_info,
    _render_online_markdown,
    _select_track,
    normalize_video_url,
    parse_subtitle,
    retrieve_subtitle_to_artifacts,
    _base_options,
    _safe_ytdlp_factory,
)
from video_transcript_workbench.models import Segment
from video_transcript_workbench.online_cli import main as online_cli_main


class UrlValidationTests(unittest.TestCase):
    def test_youtube_query_is_stripped_from_canonical_source(self) -> None:
        result = normalize_video_url("https://youtube.com/watch?v=BaW_jenozKc&list=private&token=secret#t=3")
        self.assertEqual(result.platform, "youtube")
        self.assertEqual(result.video_id, "BaW_jenozKc")
        self.assertEqual(result.canonical_url, "https://www.youtube.com/watch?v=BaW_jenozKc")

    def test_supports_bilibili_video_ids(self) -> None:
        result = normalize_video_url("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
        self.assertEqual(result.platform, "bilibili")
        self.assertEqual(result.video_id, "BV1xx411c7mD")
        self.assertEqual(result.canonical_url, "https://www.bilibili.com/video/BV1xx411c7mD")

    def test_supports_douyin_and_xiaohongshu_video_urls(self) -> None:
        douyin = normalize_video_url("https://www.douyin.com/video/6961737553342991651?from=share")
        self.assertEqual((douyin.platform, douyin.video_id, douyin.extractor_key),
                         ("douyin", "6961737553342991651", "Douyin"))
        self.assertEqual(douyin.canonical_url, "https://www.douyin.com/video/6961737553342991651")

        xhs = normalize_video_url("https://www.xiaohongshu.com/discovery/item/6411CF99000000001300B6D9?xsec_token=secret")
        self.assertEqual((xhs.platform, xhs.video_id, xhs.extractor_key),
                         ("xiaohongshu", "6411cf99000000001300b6d9", "XiaoHongShu"))
        self.assertEqual(xhs.canonical_url, "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9")
        self.assertNotIn("secret", repr(xhs))

    def test_share_links_are_sanitized_until_resolved(self) -> None:
        douyin = normalize_video_url("https://v.douyin.com/AbC123/?share_token=secret#fragment")
        xhs = normalize_video_url("https://xhslink.com/AbC123?xsec_token=secret")
        self.assertEqual((douyin.platform, douyin.video_id, douyin.extractor_key), ("douyin", None, None))
        self.assertEqual(douyin.canonical_url, "https://v.douyin.com/AbC123")
        self.assertEqual((xhs.platform, xhs.video_id, xhs.extractor_key), ("xiaohongshu", None, None))
        self.assertEqual(xhs.canonical_url, "https://xhslink.com/AbC123")
        self.assertNotIn("secret", repr(douyin) + repr(xhs))

    def test_xiaohongshu_share_token_is_ephemeral_and_other_query_is_dropped(self) -> None:
        reference = normalize_video_url(
            "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"
            "?xsec_token=public-share-token&source=share"
        )
        self.assertEqual(
            reference.canonical_url,
            "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
        )
        self.assertEqual(
            reference.request_url,
            "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token=public-share-token",
        )
        self.assertNotIn("public-share-token", repr(reference))
        with self.assertRaises(OnlineInputError):
            normalize_video_url(
                "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9"
                "?xsec_token=first&xsec_token=second"
            )

    def test_share_resolution_requires_same_platform_and_valid_video_identity(self) -> None:
        pending = normalize_video_url("https://v.douyin.com/AbC123")
        resolved = _reference_from_info(pending, {
            "id": "6961737553342991651",
            "extractor_key": "Douyin",
        })
        self.assertEqual(resolved.canonical_url, "https://www.douyin.com/video/6961737553342991651")
        with self.assertRaises(Exception):
            _reference_from_info(pending, {"id": "6411cf99000000001300b6d9", "extractor_key": "XiaoHongShu"})
        with self.assertRaises(Exception):
            _reference_from_info(pending, {"id": "not-an-id", "extractor_key": "Douyin"})

    def test_platform_network_allowlists_are_scoped(self) -> None:
        _check_network_url("https://www.douyin.com/video/12345678", _allowed_hosts("douyin"))
        _check_network_url("https://cdn.douyinvod.com/video", _allowed_hosts("douyin"))
        _check_network_url("https://sns-video-bd.xhscdn.com/video", _allowed_hosts("xiaohongshu"))
        for platform, target in (
            ("douyin", "https://sns-video-bd.xhscdn.com/video"),
            ("xiaohongshu", "https://www.douyin.com/video/12345678"),
        ):
            with self.subTest(platform=platform), self.assertRaises(Exception):
                _check_network_url(target, _allowed_hosts(platform))
        with self.assertRaises(Exception):
            _allowed_hosts("unsupported")

    def test_rejects_non_https_credentials_ports_short_links_and_playlists(self) -> None:
        invalid_urls = (
            "http://youtube.com/watch?v=BaW_jenozKc",
            "https://name:pass@youtube.com/watch?v=BaW_jenozKc",
            "https://youtube.com:444/watch?v=BaW_jenozKc",
            "https://b23.tv/abc123",
            "https://youtube.com/playlist?list=PL123",
            "https://example.com/watch?v=BaW_jenozKc",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(OnlineInputError):
                normalize_video_url(url)
        with self.assertRaises(OnlineInputError):
            normalize_video_url("x" * 4097)

    def test_network_target_and_redirect_hosts_are_allowlisted(self) -> None:
        for url in (
            "http://www.youtube.com/watch?v=x",
            "https://127.0.0.1/secret",
            "https://example.com/subtitle.vtt",
            "https://user:pass@youtube.com/",
        ):
            with self.subTest(url=url), self.assertRaises(Exception):
                _check_network_url(url, ("youtube.com", "googlevideo.com"))
        _check_network_url("https://r2---sn.googlevideo.com/subtitle", ("youtube.com", "googlevideo.com"))

    def test_ytdlp_redirect_transports_are_hardened_before_next_request(self) -> None:
        cases = (
            ("youtube", "https://www.youtube.com/watch?v=BaW_jenozKc", "http://127.0.0.1/admin"),
            (
                "xiaohongshu",
                "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
                "http://sns-video-bd.xhscdn.com/video.mp4",
            ),
            (
                "xiaohongshu",
                "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
                "https://media.example.invalid/video.mp4",
            ),
        )
        for platform, source_url, target_url in cases:
            with self.subTest(platform=platform, target_scheme=target_url.split(":", 1)[0]):
                client = _safe_ytdlp_factory(platform, _base_options())
                try:
                    handlers = client._request_director.handlers
                    self.assertEqual(set(handlers), {"Urllib"})

                    # Redirects are checked before the next request is sent.
                    urllib_handler = handlers["Urllib"]
                    opener = urllib_handler._create_instance(
                        proxies=urllib_handler.proxies,
                        cookiejar=urllib_handler.cookiejar,
                    )
                    redirect_handler = next(
                        item for item in opener.handlers if type(item).__name__ == "SafeRedirectHandler"
                    )
                    request = urllib.request.Request(source_url)
                    with self.assertRaises(Exception):
                        redirect_handler.redirect_request(
                            request, None, 302, "Found", {}, target_url
                        )
                finally:
                    client.close()


class SubtitleParsingTests(unittest.TestCase):
    def test_parses_vtt_and_preserves_original_caption_text(self) -> None:
        raw = b"WEBVTT\n\n00:00:01.200 --> 00:00:02.500 align:start\n  Raw <i>caption</i>  \n"
        segments, empty = parse_subtitle(raw, "vtt")
        self.assertEqual(empty, 0)
        self.assertEqual(segments[0].start, 1.2)
        self.assertEqual(segments[0].end, 2.5)
        self.assertEqual(segments[0].text, "  Raw <i>caption</i>  ")

    def test_parses_srt_and_counts_empty_segments(self) -> None:
        raw = b"1\n00:00:00,000 --> 00:00:00,500\nHello\n\n2\n00:00:00,500 --> 00:00:01,000\n \n"
        segments, empty = parse_subtitle(raw, "srt")
        self.assertEqual([segment.text for segment in segments], ["Hello"])
        self.assertEqual(empty, 1)

    def test_parses_youtube_json3_and_bilibili_json(self) -> None:
        yt = json.dumps({"events": [{"tStartMs": 250, "dDurationMs": 500, "segs": [{"utf8": "原句"}]}]}).encode()
        bili = json.dumps({"body": [{"from": 0.25, "to": 0.75, "content": "原句"}]}).encode()
        for ext, raw in (("json3", yt), ("json", bili)):
            with self.subTest(ext=ext):
                segments, empty = parse_subtitle(raw, ext)
                self.assertEqual(segments[0].text, "原句")
                self.assertEqual(segments[0].start, 0.25)
                self.assertEqual(empty, 0)

    def test_parses_ttml_clock_and_offset_timestamps(self) -> None:
        raw = b'<tt><body><p begin="00:00:01.000" dur="1500ms">Caption</p></body></tt>'
        segments, empty = parse_subtitle(raw, "ttml")
        self.assertEqual((segments[0].start, segments[0].end), (1.0, 2.5))
        self.assertEqual(segments[0].text, "Caption")
        self.assertEqual(empty, 0)

    def test_rejects_damaged_subtitle(self) -> None:
        with self.assertRaises(SubtitleParseError):
            parse_subtitle(b"00:99:00,000 --> broken\ntext", "srt")
        with self.assertRaises(SubtitleParseError):
            parse_subtitle(b"{not-json", "json3")
        with self.assertRaises(SubtitleParseError):
            parse_subtitle(b"\xff\xfe", "vtt")

    def test_rejects_xml_entity_declarations(self) -> None:
        dangerous = b'<!DOCTYPE x [<!ENTITY payload "expanded">]><transcript><text start="0" dur="1">&payload;</text></transcript>'
        with self.assertRaises(SubtitleParseError):
            parse_subtitle(dangerous, "srv3")

    def test_markdown_escapes_untrusted_caption_but_raw_segment_remains_available(self) -> None:
        raw = "[[private-note]] <script>alert(1)</script> & literal"
        segment = Segment(0, 1, raw)
        markdown = _render_online_markdown(
            title="safe title",
            platform="youtube",
            video_id="BaW_jenozKc",
            language="en",
            subtitle_source="manual",
            segments=[segment],
        )
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn("\\[\\[private\\-note\\]\\]", markdown)
        self.assertEqual(segment.text, raw)

    def test_prefers_manual_track_to_automatic_track(self) -> None:
        info = {
            "subtitles": {"zh-Hans": [{"ext": "vtt", "url": "https://www.youtube.com/manual.vtt"}]},
            "automatic_captions": {"zh": [{"ext": "json3", "url": "https://www.youtube.com/auto.json3"}]},
        }
        selected = _select_track(info, "zh")
        self.assertIsNotNone(selected)
        self.assertEqual(selected[0], "manual")
        self.assertEqual(selected[1], "zh-Hans")


class FakeResponse:
    def __init__(self, data: bytes, url: str):
        self.data = data
        self.url = url

    def read(self, size: int = -1) -> bytes:
        return self.data if size < 0 else self.data[:size]

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        pass


class FakeYdl:
    def __init__(self, info: dict, subtitle: bytes):
        self.info = info
        self.subtitle = subtitle if isinstance(subtitle, bytes) else b""
        self.subtitles = subtitle if isinstance(subtitle, dict) else {}
        self.extract_call = None
        self.opened_url = None
        self.opened_urls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url: str, **kwargs):
        self.extract_call = (url, kwargs)
        return self.info

    def urlopen(self, url: str):
        self.opened_url = url
        self.opened_urls.append(url)
        return FakeResponse(self.subtitles.get(url, self.subtitle), url)


class OnlineRetrievalTests(unittest.TestCase):
    def test_xiaohongshu_request_token_is_not_written_to_evidence(self) -> None:
        token = "private-share-token"
        url = f"https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token={token}&source=share"
        info = {
            "id": "6411cf99000000001300b6d9",
            "title": "safe test title",
            "duration": 1.0,
            "extractor_key": "XiaoHongShu",
            "webpage_url": "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
            "subtitles": {},
            "automatic_captions": {},
        }
        client = FakeYdl(info, b"")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            evidence = retrieve_subtitle_to_artifacts(
                url,
                language="zh",
                output_dir=output,
                client_factory=lambda *_args: client,
                run_id="xhs-token-test",
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(
            client.extract_call[0],
            f"https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token={token}",
        )
        self.assertEqual(evidence["status"], "no_available_subtitles")
        self.assertEqual(evidence["source"]["canonical_url"], "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9")
        self.assertNotIn(token, evidence_text)

    def test_xiaohongshu_request_token_is_removed_from_extractor_errors(self) -> None:
        token = "private-share-token"
        url = f"https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token={token}"

        class FailingYdl(FakeYdl):
            def extract_info(self, _url: str, **_kwargs):
                raise RuntimeError(f"unable to download webpage: {url}")

        client = FailingYdl({}, b"")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            evidence = retrieve_subtitle_to_artifacts(
                url,
                language="zh",
                output_dir=output,
                client_factory=lambda *_args: client,
                run_id="xhs-token-error-test",
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(evidence["status"], "extractor_error")
        self.assertNotIn(token, evidence_text)
        self.assertNotIn("xsec_token", evidence_text)

    def test_online_cli_returns_failure_status_without_echoing_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                return_code = online_cli_main([
                    "https://example.com/video?token=secret",
                    "--language", "zh",
                    "--output-dir", str(output),
                ])
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(return_code, 2)
        self.assertEqual(evidence["status"], "unsupported_url")
        self.assertNotIn("token=secret", stdout.getvalue() + stderr.getvalue())

    def test_run_id_cannot_escape_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            with self.assertRaises(ValueError):
                retrieve_subtitle_to_artifacts(
                    "https://youtube.com/watch?v=BaW_jenozKc",
                    language="en",
                    output_dir=output,
                    run_id="../outside",
                )
            self.assertFalse(output.exists())

    def test_unsupported_url_writes_only_a_classified_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://example.com/private?token=secret",
                language="zh",
                output_dir=output,
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)
        self.assertEqual(result["status"], "unsupported_url")
        self.assertIsNone(evidence["source"]["canonical_url"])
        self.assertNotIn("token=secret", evidence_text)
        self.assertFalse((output / "transcript.md").exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            output.mkdir()
            evidence = output / "evidence.json"
            evidence.write_text("frozen", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                retrieve_subtitle_to_artifacts(
                    "https://example.com/unsupported",
                    language="zh",
                    output_dir=output,
                )
            self.assertEqual(evidence.read_text(encoding="utf-8"), "frozen")

    def test_manual_subtitle_writes_evidence_and_markdown_without_original_url(self) -> None:
        info = {
            "id": "BaW_jenozKc",
            "title": "A public test video",
            "duration": 2.0,
            "extractor_key": "Youtube",
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"zh-Hans": [{"ext": "vtt", "url": "https://www.youtube.com/sub.vtt?sig=secret"}]},
            "automatic_captions": {"zh": [{"ext": "json3", "url": "https://www.youtube.com/auto.json3"}]},
        }
        client = FakeYdl(info, "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n原始字幕\n".encode())
        factory_options: dict[str, object] = {}

        def factory(_platform: str, options: dict):
            factory_options.update(options)
            return client

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://youtube.com/watch?v=BaW_jenozKc&auth=secret",
                language="zh",
                output_dir=output,
                client_factory=factory,
                run_id="test-run",
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)
            markdown = (output / "transcript.md").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "subtitle_success")
        self.assertEqual(evidence["schema_version"], "2.1")
        self.assertEqual(evidence["source"]["canonical_url"], "https://www.youtube.com/watch?v=BaW_jenozKc")
        self.assertIs(evidence["source"]["original_url_stored"], False)
        self.assertNotIn("auth=secret", evidence_text)
        self.assertNotIn("sig=secret", evidence_text)
        self.assertEqual(evidence["subtitle"]["source"], "manual")
        self.assertEqual(evidence["segments"][0]["text"], "原始字幕")
        self.assertIn("### 00:00:00.000 – 00:00:01.500", markdown)
        self.assertIn("原始字幕", markdown)
        self.assertEqual(client.extract_call[0], "https://www.youtube.com/watch?v=BaW_jenozKc")
        self.assertEqual(client.extract_call[1], {"download": False, "ie_key": "Youtube"})
        self.assertTrue(factory_options["ignoreconfig"])
        self.assertIsNone(factory_options["cookiefile"])
        self.assertIsNone(factory_options["cookiesfrombrowser"])
        self.assertFalse((output / "video.mp4").exists())

    def test_manual_format_falls_back_and_records_each_safe_attempt(self) -> None:
        json3_url = "https://www.youtube.com/manual.json3?sig=private-one"
        vtt_url = "https://www.youtube.com/manual.vtt?sig=private-two"
        auto_url = "https://www.youtube.com/automatic.vtt?sig=private-three"
        info = {
            "title": "format fallback test",
            "duration": 2.0,
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"en": [
                {"ext": "json3", "url": json3_url},
                {"ext": "vtt", "url": vtt_url},
            ]},
            "automatic_captions": {"en": [{"ext": "vtt", "url": auto_url}]},
        }
        malformed_json3 = json.dumps({"events": [{
            "tStartMs": 0,
            "segs": [{"utf8": "this event has no duration"}],
        }]}).encode()
        valid_vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nManual fallback caption\n"
        client = FakeYdl(info, {
            json3_url: malformed_json3,
            vtt_url: valid_vtt,
            auto_url: b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAutomatic caption\n",
        })
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://www.youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: client,
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)

        attempts = evidence["subtitle"]["attempts"]
        self.assertEqual(result["status"], "subtitle_success")
        self.assertEqual(client.opened_urls, [json3_url, vtt_url])
        self.assertEqual(evidence["subtitle"]["source"], "manual")
        self.assertEqual(evidence["subtitle"]["original_format"], "vtt")
        self.assertEqual([item["format"] for item in attempts], ["json3", "vtt"])
        self.assertEqual(attempts[0]["status"], "subtitle_parse_failed")
        self.assertEqual(attempts[0]["error"]["reason"], "missing_event_duration")
        self.assertEqual(attempts[1]["status"], "subtitle_success")
        self.assertTrue(evidence["subtitle"]["anomalies"]["parse_error"])
        self.assertEqual(evidence["subtitle"]["anomalies"]["timestamp_errors"], 0)
        self.assertNotIn("sig=", evidence_text)
        self.assertNotIn("Automatic caption", evidence_text)
        self.assertEqual(evidence["segments"][0]["text"], "Manual fallback caption")

    def test_subtitle_format_attempts_are_bounded(self) -> None:
        formats = ("json3", "json", "vtt", "srt", "srv3")
        tracks = [
            {"ext": ext, "url": f"https://www.youtube.com/candidate-{index}.{ext}"}
            for index, ext in enumerate(formats)
        ]
        info = {
            "title": "bounded attempts",
            "duration": 20.0,
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"en": tracks},
            "automatic_captions": {},
        }
        client = FakeYdl(info, {track["url"]: b"\xff" for track in tracks})
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://www.youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: client,
            )
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "subtitle_parse_failed")
        self.assertEqual(len(client.opened_urls), 4)
        self.assertEqual(len(evidence["subtitle"]["attempts"]), 4)
        self.assertTrue(evidence["subtitle"]["attempts_truncated"])
        self.assertFalse((output / "transcript.md").exists())

    def test_automatic_caption_is_tried_after_manual_formats_fail(self) -> None:
        manual_url = "https://www.youtube.com/manual.json3"
        automatic_url = "https://www.youtube.com/automatic.vtt"
        info = {
            "title": "manual then automatic",
            "duration": 2.0,
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"en": [{"ext": "json3", "url": manual_url}]},
            "automatic_captions": {"en": [{"ext": "vtt", "url": automatic_url}]},
        }
        malformed_manual = json.dumps({"events": [{
            "tStartMs": 0,
            "segs": [{"utf8": "invalid manual event"}],
        }]}).encode()
        automatic_vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nAutomatic fallback\n"
        client = FakeYdl(info, {manual_url: malformed_manual, automatic_url: automatic_vtt})
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://www.youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: client,
            )
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))

        attempts = evidence["subtitle"]["attempts"]
        self.assertEqual(result["status"], "subtitle_success")
        self.assertEqual(client.opened_urls, [manual_url, automatic_url])
        self.assertEqual([item["source"] for item in attempts], ["manual", "automatic"])
        self.assertEqual(attempts[0]["status"], "subtitle_parse_failed")
        self.assertEqual(attempts[1]["status"], "subtitle_success")
        self.assertEqual(evidence["subtitle"]["source"], "automatic")

    def test_automatic_caption_is_explicitly_marked_and_never_confused_with_manual(self) -> None:
        info = {
            "title": "auto",
            "duration": 2.0,
            "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "subtitles": {},
            "automatic_captions": {"zh-CN": [{"ext": "json", "url": "https://api.bilibili.com/sub.json"}]},
        }
        client = FakeYdl(info, '{"body":[{"from":0,"to":1.5,"content":"自动生成"}]}'.encode())
        with tempfile.TemporaryDirectory() as temp:
            result = retrieve_subtitle_to_artifacts(
                "https://bilibili.com/video/BV1xx411c7mD",
                language="zh",
                output_dir=Path(temp) / "run",
                client_factory=lambda *_args: client,
            )
            evidence = json.loads((Path(temp) / "run" / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "subtitle_success")
        self.assertEqual(evidence["subtitle"]["source"], "automatic")

    def test_no_subtitle_and_no_language_match_are_explicit_failures(self) -> None:
        cases = (
            ({"subtitles": {}, "automatic_captions": {}}, "no_available_subtitles"),
            ({"subtitles": {"fr": [{"ext": "vtt", "url": "https://www.youtube.com/fr.vtt"}]}, "automatic_captions": {}}, "no_matching_language"),
        )
        for index, (tracks, expected) in enumerate(cases):
            info = {
                "title": "test",
                "duration": 10.0,
                "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
                **tracks,
            }
            client = FakeYdl(info, b"")
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / str(index)
                result = retrieve_subtitle_to_artifacts(
                    "https://youtu.be/BaW_jenozKc",
                    language="zh",
                    output_dir=output,
                    client_factory=lambda *_args, c=client: c,
                )
                evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
                self.assertEqual(result["status"], expected)
                self.assertEqual(evidence["status"], expected)
                self.assertFalse((output / "transcript.md").exists())

    def test_non_finite_metadata_duration_serializes_as_unknown(self) -> None:
        info = {
            "title": "metadata test",
            "duration": float("nan"),
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: FakeYdl(info, b""),
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)
        self.assertEqual(result["status"], "no_available_subtitles")
        self.assertIsNone(evidence["source"]["duration_seconds"])
        self.assertNotIn("NaN", evidence_text)

    def test_incomplete_subtitles_are_not_reported_as_success(self) -> None:
        info = {
            "title": "long video",
            "duration": 600.0,
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"en": [{"ext": "srt", "url": "https://www.youtube.com/sub.srt"}]},
            "automatic_captions": {},
        }
        client = FakeYdl(info, b"1\n00:00:01,000 --> 00:00:05,000\nOnly a small opening\n")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: client,
            )
            self.assertEqual(result["status"], "incomplete_subtitles")
            self.assertFalse((output / "transcript.md").exists())

    def test_network_errors_do_not_persist_provider_exception_details(self) -> None:
        info = {
            "title": "test",
            "duration": 10.0,
            "webpage_url": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "subtitles": {"en": [{"ext": "vtt", "url": "https://www.youtube.com/sub.vtt"}]},
            "automatic_captions": {},
        }

        class FailingYdl(FakeYdl):
            def urlopen(self, _url: str):
                raise RuntimeError("failed https://youtube.com/sub.vtt?sig=secret")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            result = retrieve_subtitle_to_artifacts(
                "https://youtube.com/watch?v=BaW_jenozKc",
                language="en",
                output_dir=output,
                client_factory=lambda *_args: FailingYdl(info, b""),
            )
            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
        self.assertEqual(result["status"], "network_or_extractor_error")
        self.assertNotIn("sig=secret", evidence_text)

    def test_error_classification_reports_proxy_without_exposing_exception_details(self) -> None:
        category, message = _error_category(RuntimeError("ProxyError: unable to connect to proxy at secret.invalid"))
        self.assertEqual(category, "proxy_unavailable")
        self.assertNotIn("secret.invalid", message)

    def test_wrapped_transport_reset_is_classified_as_network_error(self) -> None:
        transport = RuntimeError("TransportError")
        transport.__cause__ = ConnectionResetError(10054, "connection reset")
        download = RuntimeError("Unable to download webpage: private-url")
        download.__cause__ = transport

        category, safe_message = _error_category(download)

        self.assertEqual(category, "network_error")
        self.assertNotIn("private-url", safe_message)

    def test_cookie_challenge_is_classified_as_access_restriction(self) -> None:
        category, safe_message = _error_category(RuntimeError(
            "Fresh cookies (not necessarily logged in) are needed; token=private"
        ))
        self.assertEqual(category, "access_or_auth_restricted")
        self.assertNotIn("cookie", safe_message.casefold())
        self.assertNotIn("private", safe_message)


if __name__ == "__main__":
    unittest.main()
