"""Subtitle retrieval for explicitly supported public video platforms."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from math import isfinite
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from . import __version__
from .artifacts import (
    _write_new_text_file,
    escape_markdown_text as _escape_caption_for_markdown,
    format_timestamp,
)
from .models import Segment


MAX_SUBTITLE_BYTES = 10 * 1024 * 1024
MAX_SUBTITLE_FORMAT_ATTEMPTS = 4
SUPPORTED_LANGUAGES = {"zh", "en", "ja"}
SUPPORTED_SUBTITLE_FORMATS = {"json3", "json", "vtt", "srt", "srv3", "ttml", "xml"}
SUBTITLE_FORMAT_PREFERENCE = ("json3", "json", "vtt", "srt", "srv3", "ttml", "xml")


class OnlineInputError(ValueError):
    """The supplied URL is not an explicitly supported public video URL."""


class UnsafeNetworkTarget(RuntimeError):
    """An extractor request or its final redirect target is not allowed."""


class SubtitleParseError(ValueError):
    """A selected subtitle could not be safely parsed into timeline segments."""

    def __init__(
        self,
        message: str,
        *,
        timestamp_error: bool = False,
        reason: str = "malformed_payload",
    ):
        super().__init__(message)
        self.timestamp_error = timestamp_error
        self.reason = reason


@dataclass(frozen=True, slots=True)
class VideoReference:
    platform: str
    video_id: str | None
    canonical_url: str
    extractor_key: str | None
    # The network request may need an explicit public share token. Keep it out of
    # repr/evidence and only carry it in memory for the extractor request.
    request_url: str | None = field(default=None, repr=False, compare=False)


def _xhs_request_url(hostname: str, path: str, query: str) -> str | None:
    """Retain only XHS's explicit share token for this request, never evidence."""
    values = parse_qs(query, keep_blank_values=True).get("xsec_token", [])
    if not values:
        return None
    if len(values) != 1 or not values[0] or len(values[0]) > 1024:
        raise OnlineInputError("小红书分享参数无效")
    return f"https://{hostname}{path}?{urlencode({'xsec_token': values[0]})}"


def _carry_xhs_request_token(request_url: str | None, canonical_url: str) -> str | None:
    if not request_url:
        return None
    try:
        parsed = urlsplit(request_url)
    except ValueError:
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("xsec_token", [])
    if len(values) != 1 or not values[0] or len(values[0]) > 1024:
        return None
    return f"{canonical_url}?{urlencode({'xsec_token': values[0]})}"


def normalize_video_url(value: str) -> VideoReference:
    """Validate a supported video URL and discard queries and fragments.

    Share links are retained only as a sanitized, platform-scoped URL until yt-dlp
    resolves them to a canonical video page. Only the explicit Xiaohongshu
    ``xsec_token`` may be sent in memory; query parameters are never written to evidence.
    """
    candidate = value.strip()
    if not candidate or len(candidate) > 4096:
        raise OnlineInputError("URL 为空或超过 4096 个字符")
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise OnlineInputError("URL 格式无效") from exc

    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        raise OnlineInputError("只接受不含凭据的 HTTPS 视频链接")
    if port not in (None, 443):
        raise OnlineInputError("视频链接不能指定非标准端口")
    if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/watch":
            values = query.get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
            video_id = match.group(1) if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise OnlineInputError("不支持的 YouTube URL；请提供单个视频链接")
        return VideoReference(
            "youtube",
            video_id,
            f"https://www.youtube.com/watch?v={video_id}",
            "Youtube",
        )

    if hostname == "youtu.be":
        match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
        if not match:
            raise OnlineInputError("不支持的 YouTube 短链接路径")
        video_id = match.group(1)
        return VideoReference(
            "youtube",
            video_id,
            f"https://www.youtube.com/watch?v={video_id}",
            "Youtube",
        )

    if hostname in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed.path)
        if not match:
            raise OnlineInputError("不支持的 Bilibili URL；请提供单个视频链接")
        video_id = match.group(1)
        return VideoReference(
            "bilibili",
            video_id,
            f"https://www.bilibili.com/video/{video_id}",
            "BiliBili",
        )

    if hostname in {"douyin.com", "www.douyin.com", "m.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}:
        match = re.fullmatch(r"/(?:video|share/video)/(?P<id>[0-9]{8,30})/?", parsed.path)
        if match:
            video_id = match.group("id")
            return VideoReference(
                "douyin",
                video_id,
                f"https://www.douyin.com/video/{video_id}",
                "Douyin",
            )
        raise OnlineInputError("不支持的抖音视频或分享链接")

    if hostname == "v.douyin.com":
        if re.fullmatch(r"/[A-Za-z0-9_-]{3,80}/?", parsed.path):
            path = parsed.path.rstrip("/")
            return VideoReference("douyin", None, f"https://v.douyin.com{path}", None)
        raise OnlineInputError("不支持的抖音分享链接")

    if hostname in {"xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com"}:
        match = re.fullmatch(r"/(?:explore|discovery/item)/(?P<id>[0-9a-fA-F]{16,64})/?", parsed.path)
        if not match:
            raise OnlineInputError("小红书只支持视频笔记链接，不支持纯图文笔记")
        video_id = match.group("id").casefold()
        request_url = _xhs_request_url(hostname, parsed.path, parsed.query)
        return VideoReference(
            "xiaohongshu",
            video_id,
            f"https://www.xiaohongshu.com/explore/{video_id}",
            "XiaoHongShu",
            request_url,
        )

    if hostname in {"xhslink.com", "www.xhslink.com"}:
        if re.fullmatch(r"/(?:[A-Za-z0-9_-]{1,32}/){0,2}[A-Za-z0-9_-]{4,100}/?", parsed.path):
            path = parsed.path.rstrip("/")
            request_url = _xhs_request_url(hostname, parsed.path, parsed.query)
            return VideoReference("xiaohongshu", None, f"https://{hostname}{path}", None, request_url)
        raise OnlineInputError("不支持的小红书分享链接")

    raise OnlineInputError(
        "仅支持 YouTube、Bilibili、抖音和小红书的 HTTPS 单视频链接或列明的分享链接"
    )


def _reference_from_info(reference: VideoReference, info: dict[str, Any]) -> VideoReference:
    """Resolve share links to a same-platform video identity before any download."""
    final_page = info.get("webpage_url") or info.get("original_url")
    final_reference: VideoReference | None = None
    if isinstance(final_page, str):
        try:
            final_reference = normalize_video_url(final_page)
        except OnlineInputError as exc:
            raise UnsafeNetworkTarget("unexpected final webpage URL") from exc
        if final_reference.platform != reference.platform:
            raise UnsafeNetworkTarget("final webpage changed platform")
        if reference.video_id and final_reference.video_id and reference.video_id != final_reference.video_id:
            raise UnsafeNetworkTarget("final webpage identifies another video")
        if final_reference.video_id is not None:
            request_url = _carry_xhs_request_token(reference.request_url, final_reference.canonical_url)
            return VideoReference(
                final_reference.platform,
                final_reference.video_id,
                final_reference.canonical_url,
                final_reference.extractor_key,
                request_url,
            )

    extractor_platform = {
        "douyin": "douyin",
        "xiaohongshu": "xiaohongshu",
    }
    extractor_key = str(info.get("extractor_key") or info.get("extractor") or "")
    normalized_key = extractor_key.casefold()
    if normalized_key not in extractor_platform or extractor_platform[normalized_key] != reference.platform:
        raise UnsafeNetworkTarget("share link did not resolve through the expected platform extractor")
    video_id = str(info.get("id") or "")
    patterns = {
        "douyin": r"[0-9]{8,30}",
        "xiaohongshu": r"[0-9a-fA-F]{16,64}",
    }
    if not re.fullmatch(patterns[reference.platform], video_id):
        raise UnsafeNetworkTarget("platform extractor returned an invalid video ID")
    if reference.video_id and reference.video_id != video_id:
        raise UnsafeNetworkTarget("platform extractor returned another video ID")
    if reference.platform == "douyin":
        return VideoReference("douyin", video_id, f"https://www.douyin.com/video/{video_id}", "Douyin")
    video_id = video_id.casefold()
    canonical_url = f"https://www.xiaohongshu.com/explore/{video_id}"
    return VideoReference(
        "xiaohongshu", video_id, canonical_url, "XiaoHongShu",
        _carry_xhs_request_token(reference.request_url, canonical_url),
    )


def _allowed_hosts(platform: str) -> tuple[str, ...]:
    hosts = {
        "youtube": (
            "youtube.com", "youtube-nocookie.com", "googlevideo.com",
            "googleapis.com", "ytimg.com", "ggpht.com",
        ),
        "bilibili": ("bilibili.com", "hdslb.com", "bilivideo.com", "biliapi.net", "bilibili.tv"),
        "douyin": (
            "douyin.com", "iesdouyin.com", "douyinvod.com", "douyinpic.com", "douyinstatic.com",
        ),
        "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhscdn.com"),
    }
    try:
        return hosts[platform]
    except KeyError as exc:
        raise UnsafeNetworkTarget("unknown platform has no network allowlist") from exc


def _check_network_url(value: str, allowed_hosts: tuple[str, ...]) -> None:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise UnsafeNetworkTarget("invalid network target") from exc
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password or port not in (None, 443):
        raise UnsafeNetworkTarget("network target must use standard HTTPS without credentials")
    if not hostname:
        raise UnsafeNetworkTarget("network target has no host")
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        pass
    else:
        raise UnsafeNetworkTarget("IP literal network targets are not allowed")
    if not any(hostname == suffix or hostname.endswith("." + suffix) for suffix in allowed_hosts):
        raise UnsafeNetworkTarget("network target host is outside the platform allowlist")


def _safe_ytdlp_factory(platform: str, options: dict[str, Any]):
    from yt_dlp import YoutubeDL

    allowed_hosts = _allowed_hosts(platform)

    class SafeYoutubeDL(YoutubeDL):
        def __init__(self, params):
            super().__init__(params)
            _harden_ytdlp_request_handlers(self, allowed_hosts)

        def urlopen(self, req):
            request_url = req.url if hasattr(req, "url") else str(req)
            _check_network_url(request_url, allowed_hosts)
            response = super().urlopen(req)
            final_url = response.geturl() if hasattr(response, "geturl") else getattr(response, "url", request_url)
            _check_network_url(final_url, allowed_hosts)
            return response

    return SafeYoutubeDL(options)


def _harden_ytdlp_request_handlers(client: Any, allowed_hosts: tuple[str, ...]) -> None:
    """Install redirect-aware HTTPS host checks in yt-dlp's pinned transports.

    yt-dlp 2026.8.19 exposes extraction and subtitle APIs publicly, but its transport
    director does not expose a redirect-policy hook. The project pins this dependency
    and replaces its request handlers so each HTTP redirect is checked before the next
    request is sent. Unknown optional handlers fail closed.
    """
    import urllib.request

    from yt_dlp.networking import _urllib

    class SafeRedirectHandler(_urllib.RedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _check_network_url(newurl, allowed_hosts)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class SafeUrllibRH(_urllib.UrllibRH):
        RH_KEY = "Urllib"

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            opener = urllib.request.OpenerDirector()
            handlers = [
                _urllib.ProxyHandler(proxies),
                _urllib.HTTPHandler(
                    debuglevel=int(bool(self.verbose)),
                    context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support),
                    source_address=self.source_address,
                ),
                _urllib.HTTPCookieProcessor(cookiejar),
                _urllib.DataHandler(),
                _urllib.UnknownHandler(),
                _urllib.HTTPDefaultErrorHandler(),
                _urllib.FTPHandler(),
                _urllib.HTTPErrorProcessor(),
                SafeRedirectHandler(),
            ]
            for handler in handlers:
                opener.add_handler(handler)
            opener.addheaders = []
            return opener

    director = client._request_director
    # urllib exposes a redirect hook that checks each Location before following it.
    # Other transports are disabled because checking their final response URL occurs
    # after a redirect target may already have received a request.
    handler = director.handlers.get("Urllib")
    if handler is None:
        raise RuntimeError("yt-dlp urllib request handler is required for safe online access")
    director.add_handler(SafeUrllibRH(
        logger=handler._logger,
        headers=handler.headers,
        cookiejar=handler.cookiejar,
        timeout=handler.timeout,
        proxies=handler.proxies,
        source_address=handler.source_address,
        verbose=handler.verbose,
        prefer_system_certs=handler.prefer_system_certs,
        client_cert=handler._client_cert,
        verify=handler.verify,
        legacy_ssl_support=handler.legacy_ssl_support,
    ))
    for key in list(director.handlers):
        if key != "Urllib":
            director.handlers.pop(key, None)
    if set(director.handlers) != {"Urllib"}:
        raise RuntimeError("yt-dlp request handlers could not be restricted to safe urllib")


def _base_options() -> dict[str, Any]:
    # Ignore user yt-dlp configuration and every credential source. No media format
    # is selected and extract_info is always called with download=False.
    options = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "cachedir": False,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "remote_components": [],
        "geo_bypass": False,
        "geo_bypass_country": None,
        "geo_bypass_ip_block": None,
        "socket_timeout": 20,
        "retries": 1,
        "extractor_retries": 1,
        "logger": _QuietLogger(),
    }
    node = shutil.which("node")
    if node:
        options["js_runtimes"] = {"node": {"path": node}}
    return options


class _QuietLogger:
    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def _language_matches(tag: str, requested: str) -> bool:
    normalized = tag.casefold().replace("_", "-")
    if requested == "zh":
        return normalized == "zh" or normalized.startswith("zh-") or normalized in {"chi", "zho"}
    if requested == "en":
        return normalized == "en" or normalized.startswith("en-") or normalized == "eng"
    return normalized == "ja" or normalized.startswith("ja-") or normalized == "jpn"


def _candidate_tracks(info: dict[str, Any], language: str) -> list[tuple[str, str, dict[str, Any]]]:
    sources = (
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for source_name, source_tracks in sources:
        matching_languages = [
            tag for tag in source_tracks if isinstance(tag, str) and _language_matches(tag, language)
        ]
        matching_languages.sort(key=lambda tag: (tag.casefold() != language, tag.casefold()))
        for tag in matching_languages:
            formats = source_tracks.get(tag)
            if not isinstance(formats, list):
                continue
            supported = [
                item for item in formats
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and str(item.get("ext", "")).casefold() in SUPPORTED_SUBTITLE_FORMATS
            ]
            supported.sort(key=lambda item: (
                SUBTITLE_FORMAT_PREFERENCE.index(str(item.get("ext", "")).casefold()),
                str(item.get("name", "")),
            ))
            candidates.extend((source_name, tag, item) for item in supported)
    return candidates


def _select_track(info: dict[str, Any], language: str) -> tuple[str, str, dict[str, Any]] | None:
    candidates = _candidate_tracks(info, language)
    return candidates[0] if candidates else None


def _parse_failure_reason(exc: Exception) -> str:
    cause = exc.__cause__ if isinstance(exc, SubtitleParseError) and exc.__cause__ else exc
    message = str(cause).casefold()
    if "no duration" in message or "missing duration" in message:
        return "missing_event_duration"
    if any(marker in message for marker in ("timestamp", "out of order", "exceeds video duration", "invalid timeline")):
        return "invalid_timeline"
    if "10 mib" in message or "exceeds the" in message:
        return "subtitle_size_limit"
    return "malformed_payload"


_TIME = r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{1,3})"
_TIMING_LINE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})(?:\s+.*)?\s*$")


def _seconds(value: str) -> float:
    match = re.fullmatch(_TIME, value.strip())
    if not match:
        raise ValueError("invalid subtitle timestamp")
    hours, minutes, seconds, millis = match.groups()
    return (int(hours or 0) * 3600) + (int(minutes) * 60) + int(seconds) + int(millis.ljust(3, "0")) / 1000


def _parse_text_subtitles(text: str) -> tuple[list[Segment], int]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n")
    segments: list[Segment] = []
    empty_count = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if "-->" not in line:
            index += 1
            continue
        match = _TIMING_LINE.fullmatch(line)
        if not match:
            raise ValueError("malformed subtitle timing line")
        # Capture the timestamp groups from the nested pattern as two tokens.
        left = match.group(1)
        right = match.group(6)
        start = _seconds(left)
        end = _seconds(right)
        if end <= start:
            raise ValueError("subtitle segment has non-positive duration")
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            if lines[index].strip().startswith(("NOTE ", "STYLE", "REGION")):
                break
            text_lines.append(lines[index])
            index += 1
        caption = "\n".join(text_lines)
        if caption.strip():
            segments.append(Segment(start, end, caption))
        else:
            empty_count += 1
    return segments, empty_count


def _parse_json_subtitles(payload: Any, ext: str) -> tuple[list[Segment], int]:
    segments: list[Segment] = []
    empty_count = 0
    if not isinstance(payload, dict):
        raise ValueError("subtitle JSON root must be an object")
    if isinstance(payload.get("events"), list):  # YouTube json3
        for event in payload["events"]:
            if not isinstance(event, dict) or "tStartMs" not in event:
                continue
            raw_text = "".join(
                str(part.get("utf8", ""))
                for part in event.get("segs", [])
                if isinstance(part, dict)
            )
            if not raw_text.strip():
                empty_count += 1
                continue
            if "dDurationMs" not in event:
                raise ValueError("subtitle event has no duration")
            start = float(event["tStartMs"]) / 1000
            end = start + float(event["dDurationMs"]) / 1000
            segments.append(Segment(start, end, raw_text))
        return segments, empty_count

    body = payload.get("body")  # Bilibili subtitle JSON
    if isinstance(body, list):
        for cue in body:
            if not isinstance(cue, dict):
                raise ValueError("subtitle cue must be an object")
            raw_text = cue.get("content", "")
            if not isinstance(raw_text, str):
                raise ValueError("subtitle cue text must be a string")
            if not raw_text.strip():
                empty_count += 1
                continue
            segments.append(Segment(float(cue["from"]), float(cue["to"]), raw_text))
        return segments, empty_count
    raise ValueError(f"unsupported {ext} subtitle JSON structure")


def _parse_xml_subtitles(text: str) -> tuple[list[Segment], int]:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, flags=re.IGNORECASE):
        raise ValueError("XML declarations with a doctype or entity are not accepted")
    root = ET.fromstring(text.lstrip("\ufeff"))
    segments: list[Segment] = []
    empty_count = 0
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].casefold()
        if tag not in {"text", "p"}:
            continue
        start_value = node.attrib.get("start") or node.attrib.get("begin")
        end_value = node.attrib.get("end")
        duration_value = node.attrib.get("dur")
        if start_value is None or (end_value is None and duration_value is None):
            continue
        start = _xml_seconds(start_value)
        end = _xml_seconds(end_value) if end_value is not None else start + _xml_seconds(duration_value)
        raw_text = "".join(node.itertext())
        caption = raw_text
        if not caption.strip():
            empty_count += 1
            continue
        segments.append(Segment(start, end, caption))
    return segments, empty_count


def _xml_seconds(value: str) -> float:
    value = value.strip()
    if re.fullmatch(_TIME, value):
        return _seconds(value)
    unit = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value, flags=re.IGNORECASE)
    if unit:
        amount = float(unit.group(1))
        scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit.group(2).casefold()]
        return amount * scale
    return float(value)


def parse_subtitle(data: bytes, ext: str) -> tuple[list[Segment], int]:
    if len(data) > MAX_SUBTITLE_BYTES:
        raise ValueError("subtitle exceeds the 10 MiB limit")
    normalized_ext = ext.casefold().lstrip(".")
    try:
        text = data.decode("utf-8-sig")
        if normalized_ext in {"vtt", "srt"}:
            return _parse_text_subtitles(text)
        if normalized_ext in {"json", "json3"}:
            return _parse_json_subtitles(json.loads(text), normalized_ext)
        if normalized_ext in {"srv3", "ttml", "xml"}:
            return _parse_xml_subtitles(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ET.ParseError, TypeError, KeyError, ValueError) as exc:
        detail = str(exc).casefold()
        timestamp_error = any(word in detail for word in ("timestamp", "out of order", "beyond"))
        reason = _parse_failure_reason(exc)
        raise SubtitleParseError(
            "subtitle content is malformed",
            timestamp_error=timestamp_error,
            reason=reason,
        ) from exc
    raise SubtitleParseError("unsupported subtitle format")


def _check_segments(segments: list[Segment], duration: float | None) -> dict[str, Any]:
    overlaps = 0
    previous_start = -1.0
    previous_end = -1.0
    for index, segment in enumerate(segments):
        if segment.start < previous_start:
            raise ValueError(f"subtitle segment {index} is out of order")
        if duration is not None and segment.end > duration + 0.5:
            raise ValueError(f"subtitle segment {index} exceeds video duration")
        if segment.start < previous_end:
            overlaps += 1
        previous_start = segment.start
        previous_end = max(previous_end, segment.end)
    last_end = max((segment.end for segment in segments), default=0.0)
    first_start = segments[0].start if segments else None
    coverage = min(last_end / duration, 1.0) if duration and duration > 0 else None
    leading_silence = first_start / duration if duration and duration > 0 and first_start is not None else None
    incomplete = bool(
        duration and duration > 0 and segments
        and (last_end / duration < 0.5 or first_start is None or first_start > max(30.0, duration * 0.3))
    )
    return {
        "overlapping_segments": overlaps,
        "last_segment_end_seconds": round(last_end, 3),
        "coverage_end_ratio": round(coverage, 4) if coverage is not None else None,
        "leading_silence_ratio": round(leading_silence, 4) if leading_silence is not None else None,
        "obviously_incomplete": incomplete,
    }


def _safe_title(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)
    return " ".join(cleaned.split())[:500]


def _finite_positive_duration(value: Any) -> float | None:
    if value is None:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if isfinite(duration) and duration > 0 else None


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def _error_category(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, SubtitleParseError):
        return "subtitle_parse_failed", "所选字幕格式损坏或当前版本无法解析。"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "subtitle_parse_failed", "字幕时间轴或结构无效，未生成逐字稿。"
    if isinstance(exc, UnsafeNetworkTarget):
        return "unsafe_redirect_or_resource", "提取器返回了不允许的网络目标，已停止请求。"
    message = str(exc).casefold()
    if "proxy" in message and any(marker in message for marker in ("connect", "refused", "unavailable", "failed")):
        return "proxy_unavailable", "本机网络代理连接失败；没有调整代理或读取登录凭据。"
    if any(marker in message for marker in (
        "connection refused", "connection reset", "timed out", "name resolution",
        "network is unreachable", "temporary failure in name resolution", "ssl error",
    )):
        return "network_error", "无法连接平台或字幕服务器；没有调整网络设置。"
    cookie_challenge_markers = (
        "fresh cookies", "cookies are needed", "cookie required",
    )
    if any(marker in message for marker in cookie_challenge_markers):
        return (
            "access_or_auth_restricted",
            "平台的访问检查未通过，具体原因尚未确认；“fresh cookies”提示本身不能证明必须登录。工具不会读取凭据或绕过验证。",
        )
    restricted_markers = (
        "login required", "sign in", "private video", "members-only", "age-restricted",
        "geo-restricted", "not available in your country", "authentication required",
        "http error 401", "status code 401",
    )
    if any(marker in message for marker in restricted_markers):
        return "access_or_auth_restricted", "平台要求登录、权限或地区访问；本工具不会读取或使用凭据。"
    if any(marker in message for marker in ("http error 403", "status code 403")):
        return (
            "access_or_auth_restricted",
            "平台拒绝了请求；仅凭 403 无法判断是登录、权限、地区策略还是反自动化检查。工具不会读取凭据或绕过验证。",
        )
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError)) or type(current).__name__ == "TransportError":
            return "network_error", "网络连接在收到平台响应前失败；没有调整网络设置。"
        reason = getattr(current, "reason", None)
        current = current.__cause__ or current.__context__ or (reason if isinstance(reason, BaseException) else None)
    if any(marker in message for marker in ("unable to download", "extractorerror", "no video formats")):
        return "extractor_error", "平台提取器未能读取公开视频信息；详情未记录，以免保存敏感数据。"
    return "network_or_extractor_error", "平台访问或字幕提取失败；详情未记录，以免保存签名 URL 或认证信息。"


def _render_online_markdown(
    *, title: str | None, platform: str, video_id: str, language: str,
    subtitle_source: str, segments: list[Segment],
) -> str:
    safe_title = (title or "（平台未提供标题）").replace("`", "ˋ")
    lines = [
        "# 视频字幕转录稿",
        "",
        f"- 标题：`{safe_title}`",
        f"- 平台：`{platform}`",
        f"- 视频标识：`{video_id}`",
        f"- 字幕语言：`{language}`",
        f"- 字幕来源：`{subtitle_source}`",
        "- 时间轴和原始分段以同目录 `evidence.json` 为准。",
        "",
        "---",
        "",
    ]
    for segment in segments:
        lines.extend([
            f"### {format_timestamp(segment.start)} – {format_timestamp(segment.end)}",
            "",
            _escape_caption_for_markdown(segment.text),
            "",
        ])
    return "\n".join(lines)


def _write_online_artifacts(
    output_dir: str | Path,
    evidence: dict[str, Any],
    markdown: str | None,
) -> tuple[Path, Path | None]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    evidence_path = destination / "evidence.json"
    transcript_path = destination / "transcript.md"
    if evidence_path.exists() or transcript_path.exists():
        raise FileExistsError(f"输出目录已包含结果，拒绝覆盖：{destination}。请指定新的空目录。")
    created: list[Path] = []
    try:
        if markdown is not None:
            _write_new_text_file(transcript_path, markdown)
            created.append(transcript_path)
        _write_new_text_file(
            evidence_path,
            json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        created.append(evidence_path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return evidence_path, transcript_path if markdown is not None else None


def retrieve_subtitle_to_artifacts(
    url: str,
    *,
    language: str,
    output_dir: str | Path,
    client_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Fetch one matching subtitle and save source evidence; never fetch media."""
    evidence = retrieve_subtitle_attempt(
        url,
        language=language,
        client_factory=client_factory,
        run_id=run_id,
    )
    segments = [Segment(**segment) for segment in evidence["segments"]]
    markdown = None
    if evidence["status"] == "subtitle_success":
        source = evidence["subtitle"]["source"]
        markdown = _render_online_markdown(
            title=evidence["source"]["title"],
            platform=evidence["source"]["platform"],
            video_id=evidence["source"]["video_id"],
            language=evidence["subtitle"]["language"],
            subtitle_source=source,
            segments=segments,
        )
    _write_online_artifacts(output_dir, evidence, markdown)
    return evidence


def retrieve_subtitle_attempt(
    url: str,
    *,
    language: str,
    client_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Fetch and classify a subtitle attempt without writing final artifacts."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError("language must be one of: zh, en, ja")
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("run_id must contain only letters, numbers, underscores, or hyphens")
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reference: VideoReference | None = None
    status = "failed"
    error: dict[str, str] | None = None
    metadata: dict[str, Any] = {"title": None, "duration_seconds": None}
    subtitle_info: dict[str, Any] = {
        "language": None,
        "source": None,
        "original_format": None,
        "extractor": reference.extractor_key if reference else None,
        "fetched_at_utc": None,
        "empty_segment_count": 0,
        "raw_segments": [],
        "attempts": [],
        "attempts_truncated": False,
        "anomalies": {
            "timestamp_errors": 0,
            "overlapping_segments": 0,
            "obviously_incomplete": False,
            "parse_error": False,
        },
    }
    segments: list[Segment] = []
    available_languages: list[str] = []
    markdown: str | None = None

    try:
        reference = normalize_video_url(url)
        subtitle_info["extractor"] = reference.extractor_key
    except OnlineInputError:
        status = "unsupported_url"
        error = {"category": status, "message": "URL 不受支持或不符合安全规则。"}

    if reference is not None:
        factory = client_factory or _safe_ytdlp_factory
        options = _base_options()
        try:
            with factory(reference.platform, options) as client:
                info = client.extract_info(
                    reference.request_url or reference.canonical_url,
                    download=False,
                    ie_key=reference.extractor_key,
                )
                if not isinstance(info, dict) or info.get("_type") == "playlist":
                    status = "unsupported_url"
                    error = {"category": status, "message": "只支持单个视频，不支持播放列表。"}
                else:
                    reference = _reference_from_info(reference, info)
                    metadata = {
                        "title": _safe_title(info.get("title")),
                        "duration_seconds": _finite_positive_duration(info.get("duration")),
                    }
                    for collection_name in ("subtitles", "automatic_captions"):
                        collection = info.get(collection_name) or {}
                        if isinstance(collection, dict):
                            available_languages.extend(
                                tag for tag in collection if isinstance(tag, str) and tag not in available_languages
                            )
                    candidates = _candidate_tracks(info, language)
                    selected = candidates[0] if candidates else None
                    matching_languages = [
                        tag for collection_name in ("subtitles", "automatic_captions")
                        for tag in (info.get(collection_name) or {})
                        if isinstance(tag, str) and _language_matches(tag, language)
                    ]
                    if not available_languages:
                        status = "no_available_subtitles"
                        error = {"category": status, "message": "该视频没有可用字幕轨道。"}
                    elif selected is None:
                        if matching_languages:
                            status = "unsupported_subtitle_format"
                            error = {"category": status, "message": "匹配语言的字幕格式当前无法解析。"}
                        else:
                            status = "no_matching_language"
                            error = {"category": status, "message": f"没有匹配 `{language}` 的字幕轨道。"}
                    else:
                        subtitle_info["extractor"] = info.get("extractor_key") or reference.extractor_key
                        manual_candidates = [item for item in candidates if item[0] == "manual"]
                        automatic_candidates = [item for item in candidates if item[0] == "automatic"]
                        if manual_candidates:
                            manual_limit = MAX_SUBTITLE_FORMAT_ATTEMPTS - 1 if automatic_candidates else MAX_SUBTITLE_FORMAT_ATTEMPTS
                            selected_candidates = manual_candidates[:manual_limit]
                            selected_candidates += automatic_candidates[
                                :MAX_SUBTITLE_FORMAT_ATTEMPTS - len(selected_candidates)
                            ]
                        else:
                            selected_candidates = automatic_candidates[:MAX_SUBTITLE_FORMAT_ATTEMPTS]
                        last_candidate_error: dict[str, str] | None = None
                        parse_error_count = 0
                        timestamp_error_count = 0
                        for subtitle_source, language_tag, track in selected_candidates:
                            subtitle_format = str(track.get("ext", "")).casefold()
                            attempt: dict[str, Any] = {
                                "source": subtitle_source,
                                "language": language_tag,
                                "format": subtitle_format,
                                "status": "failed",
                                "error": None,
                            }
                            try:
                                response = client.urlopen(track["url"])
                                try:
                                    raw = response.read(MAX_SUBTITLE_BYTES + 1)
                                finally:
                                    close = getattr(response, "close", None)
                                    if callable(close):
                                        close()
                            except Exception as exc:
                                category, safe_message = _error_category(exc)
                                attempt["status"] = category
                                attempt["error"] = {"category": category, "message": safe_message}
                                subtitle_info["attempts"].append(attempt)
                                last_candidate_error = attempt["error"]
                                status = category
                                if category in {
                                    "proxy_unavailable", "network_error", "access_or_auth_restricted",
                                    "unsafe_redirect_or_resource",
                                }:
                                    error = {"category": category, "message": safe_message}
                                    break
                                continue

                            subtitle_info["fetched_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                            try:
                                if len(raw) > MAX_SUBTITLE_BYTES:
                                    raise SubtitleParseError(
                                        "subtitle exceeds the 10 MiB limit",
                                        reason="subtitle_size_limit",
                                    )
                                candidate_segments, empty_count = parse_subtitle(raw, subtitle_format)
                                if not candidate_segments:
                                    attempt["status"] = "empty_subtitles"
                                    attempt["error"] = {
                                        "category": "empty_subtitles",
                                        "message": "字幕文件没有可用文本分段。",
                                    }
                                    last_candidate_error = attempt["error"]
                                    subtitle_info["attempts"].append(attempt)
                                    status = "empty_subtitles"
                                    continue
                                try:
                                    anomalies = _check_segments(candidate_segments, metadata["duration_seconds"])
                                except ValueError as exc:
                                    raise SubtitleParseError(
                                        str(exc),
                                        timestamp_error=True,
                                        reason="invalid_timeline",
                                    ) from exc
                                if anomalies["obviously_incomplete"]:
                                    attempt["status"] = "incomplete_subtitles"
                                    attempt["error"] = {
                                        "category": "incomplete_subtitles",
                                        "message": "字幕时间覆盖明显不足，未标记为成功。",
                                    }
                                    last_candidate_error = attempt["error"]
                                    subtitle_info["attempts"].append(attempt)
                                    status = "incomplete_subtitles"
                                    continue
                            except (SubtitleParseError, ValueError, TypeError, KeyError) as exc:
                                category, safe_message = _error_category(exc)
                                reason = _parse_failure_reason(exc)
                                attempt["status"] = category
                                attempt["error"] = {
                                    "category": category,
                                    "reason": reason,
                                    "message": safe_message,
                                }
                                subtitle_info["attempts"].append(attempt)
                                parse_error_count += 1
                                if isinstance(exc, SubtitleParseError) and exc.timestamp_error:
                                    timestamp_error_count += 1
                                last_candidate_error = attempt["error"]
                                status = category
                                continue

                            attempt["status"] = "subtitle_success"
                            subtitle_info["attempts"].append(attempt)
                            subtitle_info.update({
                                "language": language_tag,
                                "source": subtitle_source,
                                "original_format": subtitle_format,
                                "empty_segment_count": empty_count,
                                "raw_segments": [segment.to_dict() for segment in candidate_segments],
                                "anomalies": {
                                    **anomalies,
                                    "timestamp_errors": timestamp_error_count,
                                    "empty_segments": empty_count,
                                    "parse_error": parse_error_count > 0,
                                },
                            })
                            segments = candidate_segments
                            status = "subtitle_success"
                            error = None
                            markdown = _render_online_markdown(
                                title=metadata["title"],
                                platform=reference.platform,
                                video_id=reference.video_id,
                                language=language_tag,
                                subtitle_source=subtitle_source,
                                segments=segments,
                            )
                            break
                        else:
                            if last_candidate_error is not None:
                                error = last_candidate_error
                            subtitle_info["attempts_truncated"] = (
                                len(candidates) > len(subtitle_info["attempts"])
                                and len(subtitle_info["attempts"]) >= MAX_SUBTITLE_FORMAT_ATTEMPTS
                            )
                            subtitle_info["anomalies"]["parse_error"] = parse_error_count > 0
                            subtitle_info["anomalies"]["timestamp_errors"] = timestamp_error_count
        except Exception as exc:
            category, safe_message = _error_category(exc)
            status = category
            error = {"category": category, "message": safe_message}
            if category == "subtitle_parse_failed":
                subtitle_info["anomalies"]["parse_error"] = True
                if isinstance(exc, SubtitleParseError) and exc.timestamp_error:
                    subtitle_info["anomalies"]["timestamp_errors"] = 1

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence = {
        "schema_version": "2.1",
        "run_id": run_id,
        "status": status,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "source": {
            "platform": reference.platform if reference else None,
            "video_id": reference.video_id if reference else None,
            "canonical_url": reference.canonical_url if reference else None,
            "original_url_stored": False,
            "metadata_source": "yt-dlp" if reference else None,
            **metadata,
        },
        "subtitle": {
            **subtitle_info,
            "available_languages": available_languages,
            "preferred_language": language,
        },
        "extractor": {
            "name": subtitle_info.get("extractor"),
            "version": _version("yt-dlp"),
        },
        "software": {
            "application": "video-transcript-workbench",
            "application_version": __version__,
            "python": __import__("sys").version.split()[0],
            "yt_dlp": _version("yt-dlp"),
        },
        "segments": [segment.to_dict() for segment in segments],
        "error": error,
    }
    return evidence
