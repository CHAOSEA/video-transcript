"""Bounded media downloads through yt-dlp's native Python downloaders."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import math
import shutil
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .online import (
    _base_options,
    _allowed_hosts,
    _check_network_url,
    _error_category,
    _finite_positive_duration,
    _safe_ytdlp_factory,
    normalize_video_url,
    validate_proxy_url,
    OnlineInputError,
    UnsafeNetworkTarget,
)


DEFAULT_MAX_DURATION_SECONDS = 30 * 60
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_ALLOWED_DURATION_SECONDS = 4 * 60 * 60
MAX_ALLOWED_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_XHS_PAGE_BYTES = 8 * 1024 * 1024
ALLOWED_DOWNLOAD_PROTOCOLS = ("https", "http_dash_segments", "m3u8_native")
ALLOWED_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".ogg", ".opus", ".webm", ".wav"}
ALLOWED_MUXED_VIDEO_EXTENSIONS = {".mp4"}


class DownloadError(RuntimeError):
    """A bounded online audio download failed without retaining its URL."""

    def __init__(self, message: str, *, category: str = "download_failed") -> None:
        super().__init__(message)
        self.category = category


class DownloadLimitError(DownloadError):
    """The media exceeded the configured duration or byte limit."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="download_limit_exceeded")


class UnsupportedDownloadedMedia(DownloadError):
    """The downloaded audio format cannot be inspected or decoded locally."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="unsupported_media")


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    extension: str
    size_bytes: int
    sha256: str
    duration_seconds: float
    container_format: str | None
    audio_codec: str | None
    extractor: str | None
    yt_dlp_version: str
    has_video_stream: bool = False

    def evidence(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("path")
        result["media_kind"] = "video_with_audio" if self.has_video_stream else "audio_only"
        return result


def validate_download_limits(max_duration_seconds: int, max_download_bytes: int) -> None:
    if (
        isinstance(max_duration_seconds, bool)
        or not isinstance(max_duration_seconds, int)
    ):
        raise ValueError("max_duration_seconds must be an integer")
    if (
        isinstance(max_download_bytes, bool)
        or not isinstance(max_download_bytes, int)
    ):
        raise ValueError("max_download_bytes must be an integer")
    if not 1 <= max_duration_seconds <= MAX_ALLOWED_DURATION_SECONDS:
        raise ValueError(
            f"max_duration_seconds must be between 1 and {MAX_ALLOWED_DURATION_SECONDS}"
        )
    if not 1 <= max_download_bytes <= MAX_ALLOWED_DOWNLOAD_BYTES:
        raise ValueError(
            f"max_download_bytes must be between 1 and {MAX_ALLOWED_DOWNLOAD_BYTES}"
        )


def _distribution_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def javascript_runtime_options() -> dict[str, Any]:
    """Explicitly enable Node when present; never fetch remote EJS components."""
    node = shutil.which("node")
    return {"js_runtimes": {"node": {"path": node}}} if node else {}


def runtime_evidence() -> dict[str, str | None]:
    node = shutil.which("node")
    return {
        "yt_dlp": _distribution_version("yt-dlp"),
        "yt_dlp_ejs": _distribution_version("yt-dlp-ejs"),
        "javascript_runtime": "node" if node else None,
    }


class _XhsVideoMetaParser(HTMLParser):
    """Collect only candidate video URLs from Open Graph media tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {key.casefold(): value for key, value in attrs if key}
        name = (values.get("property") or values.get("name") or "").casefold()
        content = values.get("content")
        if name in {"og:video", "og:video:url", "og:video:secure_url"} and content:
            self.urls.append(content.strip())


def _xhs_https_og_video_url(
    client: Any,
    page_url: str,
    *,
    expected_video_id: str,
) -> str | None:
    """Read a bounded XHS page and return its validated HTTPS MP4 og:video URL.

    The URL is returned only in memory for yt-dlp's existing bounded downloader.
    The page body and signed URL are never written to logs or evidence.
    """
    response = client.urlopen(page_url)
    try:
        final_url = response.geturl() if hasattr(response, "geturl") else page_url
        page = response.read(MAX_XHS_PAGE_BYTES + 1)
    finally:
        response.close()

    if len(page) > MAX_XHS_PAGE_BYTES:
        return None
    try:
        final_reference = normalize_video_url(final_url)
    except OnlineInputError:
        return None
    if (
        final_reference.platform != "xiaohongshu"
        or final_reference.video_id != expected_video_id
    ):
        return None

    parser = _XhsVideoMetaParser()
    parser.feed(page.decode("utf-8", "replace"))
    page = b""
    allowed_hosts = _allowed_hosts("xiaohongshu")
    for candidate in parser.urls:
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme.casefold() != "https"
                or not parsed.path.casefold().endswith(".mp4")
            ):
                continue
            safe_candidate = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
            )
            _check_network_url(safe_candidate, allowed_hosts)
        except (ValueError, UnsafeNetworkTarget):
            continue
        return safe_candidate
    return None


def _has_safe_https_media_format(info: dict[str, Any], platform: str) -> bool:
    """Return whether a safe HTTPS format can pass the download selector."""
    allowed_hosts = _allowed_hosts(platform)
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        if str(item.get("protocol") or "").casefold() not in ALLOWED_DOWNLOAD_PROTOCOLS:
            continue
        audio_codec = str(item.get("acodec") or "").casefold()
        if not audio_codec or audio_codec == "none":
            continue
        extension = str(item.get("ext") or "").casefold()
        is_supported_audio = extension in ALLOWED_AUDIO_EXTENSIONS
        if platform == "xiaohongshu":
            video_codec = str(item.get("vcodec") or "").casefold()
            is_supported_muxed_mp4 = extension == "mp4" and video_codec not in {"", "none"}
            if not (is_supported_audio or is_supported_muxed_mp4):
                continue
        elif not is_supported_audio:
            continue
        try:
            parsed = urlsplit(item["url"])
            if parsed.scheme.casefold() != "https":
                continue
            _check_network_url(item["url"], allowed_hosts)
        except (ValueError, UnsafeNetworkTarget):
            continue
        return True
    return False


def _xhs_duration_from_info(info: dict[str, Any]) -> float | None:
    """XHS exposes duration on stream formats, not on its raw video result."""
    values = [_finite_positive_duration(info.get("duration"))]
    values.extend(
        _finite_positive_duration(item.get("duration"))
        for item in info.get("formats") or []
        if isinstance(item, dict)
    )
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_download(path: Path) -> tuple[float, str | None, str | None, bool]:
    try:
        import av

        with av.open(str(path), mode="r", metadata_errors="ignore") as container:
            audio_streams = list(container.streams.audio)
            if not audio_streams:
                raise UnsupportedDownloadedMedia("下载结果中没有音轨")
            stream = audio_streams[0]
            duration = None
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            if not duration and container.duration is not None:
                duration = float(container.duration / av.time_base)
            if duration is None or not math.isfinite(duration) or duration <= 0:
                raise UnsupportedDownloadedMedia("无法从下载媒体读取有效音轨时长")
            codec = stream.codec_context.codec
            codec_name = codec.name if codec else None
            container_format = container.format.name if container.format else None
            has_video_stream = bool(container.streams.video)
            return duration, container_format, codec_name, has_video_stream
    except UnsupportedDownloadedMedia:
        raise
    except Exception as exc:
        raise UnsupportedDownloadedMedia(
            f"下载媒体无法由本机 PyAV 解码：{type(exc).__name__}"
        ) from exc


def _safe_file_in_directory(
    path: Path,
    directory: Path,
    *,
    allow_muxed_mp4: bool = False,
) -> Path:
    resolved = path.resolve(strict=True)
    root = directory.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DownloadError("下载器返回了临时目录之外的路径") from exc
    if path.is_symlink() or not resolved.is_file():
        raise DownloadError("下载结果不是普通文件")
    extension = resolved.suffix.casefold()
    if extension not in ALLOWED_AUDIO_EXTENSIONS and not (
        allow_muxed_mp4 and extension in ALLOWED_MUXED_VIDEO_EXTENSIONS
    ):
        raise UnsupportedDownloadedMedia(
            f"下载器返回了未支持的媒体扩展名 {resolved.suffix or '(无扩展名)'}"
        )
    return resolved


def _format_selector(platform: str) -> str:
    """Prefer standalone audio; only XHS may fall back to an audio-bearing MP4."""
    selectors = [
        f"bestaudio[protocol={protocol}]" for protocol in ALLOWED_DOWNLOAD_PROTOCOLS
    ]
    if platform == "xiaohongshu":
        selectors.extend(
            "best[acodec!=none][vcodec!=none][ext=mp4]"
            f"[protocol={protocol}]"
            for protocol in ALLOWED_DOWNLOAD_PROTOCOLS
        )
    return "/".join(selectors)


def download_audio_to_directory(
    url: str,
    *,
    directory: str | Path,
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    client_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    proxy: str | None = None,
) -> DownloadedMedia:
    """Download one audio-only stream to a caller-owned private temp directory.

    The caller owns and must remove ``directory``. Output names never contain
    extractor-provided titles. Network requests use the same per-hop host guard as
    subtitle extraction, while yt-dlp's native downloaders avoid external tools.
    """
    validate_download_limits(max_duration_seconds, max_download_bytes)
    proxy = validate_proxy_url(proxy)
    reference = normalize_video_url(url)
    if reference.video_id is None or reference.extractor_key is None:
        raise DownloadError(
            "分享链接尚未解析为规范视频地址；停止下载。",
            category="unsupported_url",
        )
    target = Path(directory).expanduser().resolve()
    if not target.is_dir():
        raise DownloadError("下载目标必须是已存在的专用临时目录")
    if any(target.iterdir()):
        raise DownloadError("下载临时目录必须为空")

    options = _base_options(proxy)
    options.update(javascript_runtime_options())
    allow_muxed_mp4 = reference.platform == "xiaohongshu"
    options.update({
        "format": _format_selector(reference.platform),
        "outtmpl": str(target / "media.%(ext)s"),
        "paths": {"home": str(target), "temp": str(target)},
        "noplaylist": True,
        "ignoreerrors": False,
        "skip_download": False,
        "max_filesize": max_download_bytes,
        "external_downloader": {
            "default": "native",
            "http": "native",
            "dash": "native",
            "m3u8": "native",
        },
        "hls_prefer_native": True,
        "concurrent_fragment_downloads": 1,
        "nopart": True,
        "overwrites": False,
        "geo_bypass": False,
        "geo_bypass_country": None,
        "geo_bypass_ip_block": None,
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "socket_timeout": 20,
    })

    limit_state: dict[str, bool] = {"duration": False, "bytes": False}

    def duration_filter(info: Any, _incomplete: bool = False) -> str | None:
        if isinstance(info, dict) and (
            info.get("is_live") is True
            or info.get("live_status") in {"is_live", "is_upcoming", "was_live"}
        ):
            limit_state["duration"] = True
            return "live or scheduled media is not supported"
        duration = _finite_positive_duration(info.get("duration")) if isinstance(info, dict) else None
        if duration is None or duration > max_duration_seconds:
            limit_state["duration"] = True
            return "media duration is missing or exceeds the configured limit"
        return None

    def progress_hook(data: dict[str, Any]) -> None:
        downloaded = int(data.get("downloaded_bytes") or 0)
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        if max(downloaded, total) > max_download_bytes:
            limit_state["bytes"] = True
            raise DownloadLimitError("下载媒体超过配置的字节上限")

    options["match_filter"] = duration_filter
    options["progress_hooks"] = [progress_hook]
    factory = client_factory or _safe_ytdlp_factory
    try:
        with factory(reference.platform, options) as client:
            request_url = reference.request_url or reference.canonical_url
            if reference.platform == "xiaohongshu":
                info = client.extract_info(
                    request_url,
                    download=False,
                    process=False,
                    ie_key=reference.extractor_key,
                )
                if (
                    isinstance(info, dict)
                    and str(info.get("id") or "").casefold() == reference.video_id
                    and str(info.get("extractor_key") or info.get("extractor") or "").casefold()
                    == "xiaohongshu"
                    and not _has_safe_https_media_format(info, reference.platform)
                ):
                    if not isinstance(info.get("formats"), list):
                        info["formats"] = []
                    source_duration = _xhs_duration_from_info(info)
                    if source_duration is None or source_duration > max_duration_seconds:
                        limit_state["duration"] = True
                        raise DownloadLimitError("视频时长未知或超过配置的时长上限")
                    og_video_url = _xhs_https_og_video_url(
                        client,
                        request_url,
                        expected_video_id=reference.video_id,
                    )
                    if og_video_url:
                        info["formats"].append({
                            "format_id": "xhs-og-video-https",
                            "url": og_video_url,
                            "ext": "mp4",
                            "protocol": "https",
                            # The MP4 is validated for real audio and video after
                            # download; the page tag itself does not declare codecs.
                            "acodec": "unknown",
                            "vcodec": "unknown",
                            # XHS exposes this on the source formats, not the raw
                            # result; yt-dlp applies match_filter to the selection.
                            "duration": source_duration,
                        })
                process_result = getattr(client, "process_ie_result", None)
                if callable(process_result):
                    info = process_result(info, download=True)
                else:
                    # Small injected test clients may implement only extract_info.
                    info = client.extract_info(
                        request_url,
                        download=True,
                        ie_key=reference.extractor_key,
                    )
            else:
                info = client.extract_info(
                    request_url,
                    download=True,
                    ie_key=reference.extractor_key,
                )
    except Exception as exc:
        if limit_state["bytes"]:
            raise DownloadLimitError("下载媒体超过配置的字节上限") from exc
        if limit_state["duration"]:
            raise DownloadLimitError("视频时长未知或超过配置的时长上限") from exc
        if isinstance(exc, DownloadError):
            raise
        category, safe_message = _error_category(exc)
        if category not in {
            "proxy_unavailable", "network_error", "access_or_auth_restricted",
            "unsafe_redirect_or_resource", "extractor_error",
        }:
            category = "download_failed"
            safe_message = f"yt-dlp 下载失败：{type(exc).__name__}；原始异常已省略。"
        raise DownloadError(safe_message, category=category) from exc

    if limit_state["duration"]:
        raise DownloadLimitError("视频时长未知或超过配置的时长上限")
    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise DownloadError("提取器没有返回单视频下载结果")
    final_page = info.get("webpage_url") or info.get("original_url")
    if isinstance(final_page, str):
        try:
            final_reference = normalize_video_url(final_page)
        except OnlineInputError as exc:
            raise UnsafeNetworkTarget("unexpected final webpage URL") from exc
        if (final_reference.platform, final_reference.video_id) != (reference.platform, reference.video_id):
            raise UnsafeNetworkTarget("final webpage URL identifies another video")
    if _finite_positive_duration(info.get("duration")) is None:
        raise DownloadLimitError("平台未提供有效视频时长")
    if _finite_positive_duration(info.get("duration")) > max_duration_seconds:
        raise DownloadLimitError("视频时长超过配置的上限")

    files = [path for path in target.iterdir() if path.is_file() and not path.name.endswith(".part")]
    if len(files) != 1:
        raise DownloadError("下载器未生成唯一的音频媒体文件")
    media_path = _safe_file_in_directory(
        files[0], target, allow_muxed_mp4=allow_muxed_mp4
    )
    size = media_path.stat().st_size
    if size <= 0:
        raise DownloadError("下载结果为空")
    if size > max_download_bytes:
        raise DownloadLimitError("下载媒体超过配置的字节上限")
    duration, container_format, codec_name, has_video_stream = _inspect_download(media_path)
    if media_path.suffix.casefold() in ALLOWED_MUXED_VIDEO_EXTENSIONS and not has_video_stream:
        raise UnsupportedDownloadedMedia("小红书 MP4 回退结果不含视频流")
    if duration > max_duration_seconds:
        raise DownloadLimitError("实际音轨时长超过配置的上限")

    return DownloadedMedia(
        path=media_path,
        extension=media_path.suffix.casefold(),
        size_bytes=size,
        sha256=_sha256(media_path),
        duration_seconds=duration,
        container_format=container_format,
        audio_codec=codec_name,
        extractor=info.get("extractor_key") or info.get("extractor"),
        yt_dlp_version=_distribution_version("yt-dlp"),
        has_video_stream=has_video_stream,
    )
