"""Media downloader powered by yt-dlp (YouTube, VK, Iwara, PornHub, Rule34, …)."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
from http.cookiejar import Cookie
from pathlib import Path
from threading import Event
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import yt_dlp


ProgressCallback = Callable[[dict], None]
StatusCallback = Callable[[str], None]


class DownloadCancelled(Exception):
    """Raised when the user stops an in-progress download."""


def _is_cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _check_cancel(cancel_event: Event | None) -> None:
    if _is_cancelled(cancel_event):
        raise DownloadCancelled("Загрузка отменена")


def _interruptible_sleep(seconds: float, cancel_event: Event | None) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _check_cancel(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.15, remaining))


def _unlink_quiet(path: Path) -> None:
    for _ in range(6):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.12)


class DownloadCleanup:
    """Delete files created by the current download after the user cancels."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self._before = {path.resolve() for path in output_dir.iterdir() if path.is_file()}
        self._extra: set[Path] = set()

    def track(self, raw: object) -> None:
        if not raw:
            return
        path = Path(str(raw))
        self._extra.add(path)
        self._extra.add(Path(str(path) + ".part"))
        if path.suffix:
            self._extra.add(path.with_name(path.name + ".part"))

    def track_hook(self, data: dict) -> None:
        for key in ("filename", "tmpfilename", "filepath"):
            self.track(data.get(key))
        info = data.get("info_dict")
        if isinstance(info, dict):
            for key in ("filename", "filepath", "_filename"):
                self.track(info.get(key))

    def purge(self) -> None:
        victims: set[Path] = set(self._extra)
        if self.output_dir.exists():
            for path in self.output_dir.iterdir():
                if not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path
                if resolved not in self._before:
                    victims.add(path)
        for path in victims:
            _unlink_quiet(path)
            _unlink_quiet(Path(str(path) + ".part"))


def _delete_partial_files(data: dict) -> None:
    names: list[str] = []
    for key in ("tmpfilename", "filename", "filepath"):
        raw = data.get(key)
        if raw:
            names.append(str(raw))
    for name in names:
        _unlink_quiet(Path(name))
        _unlink_quiet(Path(str(name) + ".part"))


def _abort_if_cancelled(
    data: dict,
    cancel_event: Event | None,
    cleanup: DownloadCleanup | None = None,
) -> None:
    if cleanup is not None:
        cleanup.track_hook(data)
    if not _is_cancelled(cancel_event):
        return
    _delete_partial_files(data)
    raise DownloadCancelled("Загрузка отменена")


SITE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "youtube",
        re.compile(
            r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)",
            re.IGNORECASE,
        ),
    ),
    (
        "vk",
        re.compile(
            r"^https?://(?:(?:www|m)\.)?(?:vk\.com|vk\.ru|vkvideo\.ru)/",
            re.IGNORECASE,
        ),
    ),
    (
        "yandexmusic",
        re.compile(
            r"^https?://(?:(?:www|m)\.)?music\.yandex\.(?:ru|com|by|kz|ua)/",
            re.IGNORECASE,
        ),
    ),
    (
        "rule34",
        re.compile(
            r"^https?://(?:www\.)?(?:rule34\.xxx|rule34video\.com)/",
            re.IGNORECASE,
        ),
    ),
    (
        "iwara",
        re.compile(
            r"^https?://(?:www\.)?(?:iwara\.tv|ecchi\.iwara\.tv)/",
            re.IGNORECASE,
        ),
    ),
    (
        "pornhub",
        re.compile(
            r"^https?://(?:[\w-]+\.)?pornhub\.(?:com|org|net)/",
            re.IGNORECASE,
        ),
    ),
]

SITE_LABELS = {
    "youtube": "YouTube",
    "vk": "VK Video",
    "yandexmusic": "Яндекс.Музыка",
    "rule34": "Rule34",
    "iwara": "Iwara",
    "pornhub": "PornHub",
}

SUPPORTED_SITES_HINT = (
    "youtube.com / youtu.be / shorts\n"
    "vkvideo.ru / vk.com / vk.ru (видео и клипы)\n"
    "music.yandex.ru\n"
    "rule34.xxx / rule34video.com\n"
    "iwara.tv\n"
    "pornhub.com / pornhub.org"
)

YANDEX_TRACK_RE = re.compile(
    r"music\.yandex\.(?:ru|com|by|kz|ua)/(?:album/(?P<album>\d+)/)?track/(?P<track>\d+)",
    re.IGNORECASE,
)
YANDEX_API = "https://api.music.yandex.net"
YANDEX_SIGN_KEY = "p93jhgh689SBReK6ghtw62"
YANDEX_MD5_SALT = "XGRlBW9FXlekgbPrRHuSiA"
YANDEX_TOKEN_BY_SESSION_CLIENT_ID = "c0ebe342af7d48fbbbfcf2d2eedb8f9e"
YANDEX_TOKEN_BY_SESSION_CLIENT_SECRET = "ad0a908f0aa341a182a37ecd75bc319e"
YANDEX_MUSIC_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
YANDEX_MUSIC_CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"
_YANDEX_SESSION_NAMES = {"Session_id", "sessionid2"}
_YANDEX_OAUTH_COOKIE_NAMES = _YANDEX_SESSION_NAMES | {
    "sessar",
    "yandexuid",
    "yandex_login",
    "i",
    "yp",
    "ys",
    "L",
    "yashr",
    "lah",
    "mda",
    "my",
}
_YANDEX_OAUTH_COOKIE_ORDER = (
    "Session_id",
    "sessionid2",
    "sessar",
    "yandexuid",
    "yandex_login",
    "i",
    "yp",
    "ys",
    "L",
    "yashr",
    "lah",
    "mda",
    "my",
)
_YANDEX_MAX_COOKIE_HEADER = 4096
_YANDEX_BROWSER_COOKIES_TRIED = False

_IMPERSONATE_TARGET = None


def detect_site(url: str) -> str | None:
    text = url.strip()
    for name, pattern in SITE_PATTERNS:
        if pattern.match(text):
            return name
    # Fallback: host contains known brand (covers odd subdomains / query forms)
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:
        return None
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("vk.com") or host.endswith("vk.ru") or host.endswith("vkvideo.ru"):
        return "vk"
    if "music.yandex." in host:
        return "yandexmusic"
    if host.endswith("rule34.xxx") or host.endswith("rule34video.com"):
        return "rule34"
    if host.endswith("iwara.tv"):
        return "iwara"
    if host.endswith("pornhub.com") or host.endswith("pornhub.org") or host.endswith("pornhub.net"):
        return "pornhub"
    return None


def is_supported_url(url: str) -> bool:
    return detect_site(url) is not None


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_media_urls(text: str) -> list[str]:
    """Pull supported http(s) links from pasted text (spaces or newlines)."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _URL_IN_TEXT_RE.findall(text or ""):
        url = match.rstrip(").,];'\"")
        if not is_supported_url(url) or url in seen:
            continue
        seen.add(url)
        found.append(url)
    if found:
        return found
    stripped = (text or "").strip()
    if stripped and is_supported_url(stripped):
        return [stripped]
    return []


_HMS_TOKEN_RE = re.compile(
    r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?$",
    re.IGNORECASE,
)


def parse_timestamp(value: object | None) -> float | None:
    """Parse 90, 1:20, 1:20:05, 1h2m3s. Empty / 'конец' → None."""
    text = str(value or "").strip().replace(",", ".")
    if not text or text.lower() in {"inf", "end", "конец", "eof"}:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    compact = text.replace(" ", "")
    if any(ch in compact.lower() for ch in "hms"):
        match = _HMS_TOKEN_RE.fullmatch(compact)
        if match and compact:
            hours = int(match.group("h") or 0)
            minutes = int(match.group("m") or 0)
            seconds = float(match.group("s") or 0)
            if hours or minutes or match.group("s"):
                return hours * 3600 + minutes * 60 + seconds
    parts = text.split(":")
    if 2 <= len(parts) <= 3:
        try:
            nums = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(f"Неверное время: {text}") from exc
        if any(n < 0 for n in nums):
            raise ValueError(f"Неверное время: {text}")
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    raise ValueError(f"Неверное время: {text}")


def normalize_time_range(
    start: float | None,
    end: float | None,
) -> tuple[float | None, float | None]:
    if start is not None and start < 0:
        raise ValueError("Начало обрезки не может быть отрицательным.")
    if end is not None and end < 0:
        raise ValueError("Конец обрезки не может быть отрицательным.")
    if start is not None and end is not None and end <= start:
        raise ValueError("Конец обрезки должен быть позже начала.")
    return start, end


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(float(seconds))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def time_range_label(start: float | None, end: float | None) -> str:
    if start is None and end is None:
        return ""
    left = format_timestamp(start) if start is not None else "начало"
    right = format_timestamp(end) if end is not None else "конец"
    return f"{left}–{right}"


def time_range_filename_suffix(start: float | None, end: float | None) -> str:
    if start is None and end is None:
        return ""
    left = format_timestamp(start).replace(":", "-") if start is not None else "0-00"
    right = format_timestamp(end).replace(":", "-") if end is not None else "end"
    return f"{left}_{right}"


def timestamp_from_url(url: str) -> float | None:
    """Read YouTube-style t= / start= from the query or fragment."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    for blob in (parsed.query, parsed.fragment or ""):
        if not blob:
            continue
        query = blob if "=" in blob else f"t={blob}"
        try:
            qs = parse_qs(query)
        except Exception:
            continue
        for key in ("t", "start", "time_continue"):
            raw = (qs.get(key) or [""])[0]
            if not raw:
                continue
            try:
                return parse_timestamp(raw)
            except ValueError:
                continue
    return None


def apply_download_range(
    opts: dict,
    start: float | None,
    end: float | None,
) -> dict:
    """Ask yt-dlp to fetch only [start, end]; open bound uses 0 / inf."""
    if start is None and end is None:
        return opts
    from yt_dlp.utils import download_range_func

    start_s = 0.0 if start is None else float(start)
    end_s = float("inf") if end is None else float(end)
    opts["download_ranges"] = download_range_func(None, [(start_s, end_s)])
    opts["force_keyframes_at_cuts"] = True
    suffix = time_range_filename_suffix(start, end)
    if suffix:
        output_dir = Path(str(opts.get("outtmpl") or ".")).parent
        opts["outtmpl"] = str(output_dir / f"%(title)s [%(id)s] {suffix}.%(ext)s")
    return opts


def trim_existing_file(
    src: Path,
    start: float | None,
    end: float | None,
    cancel_event: Event | None = None,
) -> Path:
    """Cut an already downloaded file with ffmpeg (Yandex and similar)."""
    if start is None and end is None:
        return src
    suffix = time_range_filename_suffix(start, end)
    dest = src.with_name(f"{src.stem} {suffix}{src.suffix}") if suffix else src
    ffmpeg = get_ffmpeg_location()
    temp_out = dest.with_name(dest.stem + ".trim" + dest.suffix)
    _unlink_quiet(temp_out)

    def build_cmd(*, copy: bool) -> list[str]:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if start:
            cmd.extend(["-ss", f"{start:.3f}"])
        cmd.extend(["-i", str(src)])
        if end is not None:
            duration = max(0.05, float(end) - float(start or 0))
            cmd.extend(["-t", f"{duration:.3f}"])
        if copy:
            cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])
        elif src.suffix.lower() == ".mp3":
            cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
        elif src.suffix.lower() in {".m4a", ".aac"}:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"])
        cmd.append(str(temp_out))
        return cmd

    if not _run_ffmpeg_replace(build_cmd(copy=True), dest, temp_out, cancel_event):
        if not _run_ffmpeg_replace(build_cmd(copy=False), dest, temp_out, cancel_event):
            raise RuntimeError("Не удалось обрезать файл по времени.")
    if dest.resolve() != src.resolve():
        _unlink_quiet(src)
    return dest


def short_media_label(url: str) -> str:
    """Compact title for the download queue until the file name is known."""
    text = (url or "").strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return text[:72] or "Ссылка"
    path = (parsed.path or "").rstrip("/")
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query or "")
    video_id = (query.get("v") or [""])[0]
    if video_id:
        return video_id
    if "/shorts/" in path.lower() or host in {"youtu.be"}:
        return path.split("/")[-1] or text[:72]
    name = path.split("/")[-1] if path else (parsed.netloc or text)
    return (name or "Ссылка")[:72]


def is_youtube_url(url: str) -> bool:
    """Backward-compatible alias."""
    return detect_site(url) == "youtube"


def is_youtube_shorts(url: str | None) -> bool:
    """True for youtube.com/shorts/… (vertical original, not the 16:9 TV crop)."""
    if not url:
        return False
    try:
        path = (urlparse(url.strip()).path or "").lower()
    except Exception:
        return "/shorts/" in url.lower()
    return "/shorts/" in path


def site_label(url: str) -> str:
    site = detect_site(url)
    return SITE_LABELS.get(site or "", "Видео")


def get_ffmpeg_location() -> str:
    import imageio_ffmpeg
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

    # Full path to the binary (imageio names it ffmpeg-win-*.exe, not ffmpeg.exe)
    location = imageio_ffmpeg.get_ffmpeg_exe()
    # yt-dlp's FFmpegFD.available() builds FFmpegPostProcessor without YoutubeDL,
    # so it ignores ffmpeg_location in opts and only sees this ContextVar / PATH.
    FFmpegPostProcessor._ffmpeg_location.set(location)
    return location


_PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)


@contextlib.contextmanager
def _without_process_proxy_env() -> Iterator[None]:
    """
    Prevent curl_cffi from using a broken local/system proxy.

    On Windows, libcurl auto-detects the IE/WinHTTP proxy (often 127.0.0.1 from
    Clash/V2Ray). Clearing proxy env vars alone is not enough — NO_PROXY=* forces
    a direct connection for every host.
    """
    saved = {key: os.environ[key] for key in _PROXY_ENV_KEYS if key in os.environ}
    try:
        for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        yield
    finally:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(saved)


def _apply_direct_network(opts: dict) -> dict:
    """Force a direct connection so a broken local proxy (127.0.0.1) cannot break downloads."""
    # Empty string = no proxy in yt-dlp (None would fall back to env/system proxy).
    opts["proxy"] = ""
    return opts


def _force_ydl_direct_proxies(ydl: yt_dlp.YoutubeDL) -> None:
    """
    Make curl_cffi skip Windows system proxy auto-detect.

    yt-dlp's proxy="" becomes all=None and leaves CURLOPT_PROXY unset, so libcurl
    still picks IE/WinHTTP proxy (often 127.0.0.1). Setting no='*' maps to
    CURLOPT_NOPROXY=* and disables every proxy.
    """
    ydl.__dict__["proxies"] = {"all": None, "no": "*"}


@contextlib.contextmanager
def _youtube_dl(opts: dict) -> Iterator[yt_dlp.YoutubeDL]:
    _apply_direct_network(opts)
    with _without_process_proxy_env():
        with yt_dlp.YoutubeDL(opts) as ydl:
            _force_ydl_direct_proxies(ydl)
            yield ydl


def _is_proxy_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(
        needle in lower
        for needle in (
            "proxyerror",
            "could not resolve proxy",
            "failed to perform, curl: (97)",
            "failed to perform, curl: (7)",
            "curl: (97)",
            "could not resolve proxy: 127.0.0.1",
            "tunnel connection failed",
            "proxy connect aborted",
            "407 proxy",
            "resolve proxy",
        )
    )


def _proxy_error() -> RuntimeError:
    return RuntimeError(
        "Сетевая ошибка: TubeSave пытался ходить через локальный прокси "
        "(часто 127.0.0.1 — Clash / V2Ray / VPN), но прокси недоступен.\n\n"
        "Что сделать:\n"
        "1. Включите прокси/VPN, если он должен быть запущен, или\n"
        "2. Выключите системный прокси в Windows "
        "(Параметры → Сеть и Интернет → Прокси) и в клиенте Clash/V2Ray.\n"
        "3. Перезапустите TubeSave и скачайте снова."
    )


def get_impersonate_target():
    """Pick a curl_cffi browser profile that yt-dlp can actually use."""
    global _IMPERSONATE_TARGET
    if _IMPERSONATE_TARGET is not None:
        return _IMPERSONATE_TARGET

    from yt_dlp.networking.impersonate import ImpersonateTarget

    preferred = [
        ImpersonateTarget("chrome", "133", "macos", "15"),
        ImpersonateTarget("chrome", "136", "macos", "15"),
        ImpersonateTarget("chrome", "131", "android", "14"),
        ImpersonateTarget("edge", "101", "windows", "10"),
        ImpersonateTarget("chrome", "99", "windows", "10"),
    ]
    with _without_process_proxy_env():
        probe = yt_dlp.YoutubeDL(
            _apply_direct_network(
                {"quiet": True, "no_warnings": True, **_ydl_storage_opts()}
            )
        )
        _force_ydl_direct_proxies(probe)
        available = [target for target, _source in probe._get_available_impersonate_targets()]
    for target in preferred:
        if any(target in item or item in target for item in available):
            _IMPERSONATE_TARGET = target
            return target
    _IMPERSONATE_TARGET = available[0] if available else None
    return _IMPERSONATE_TARGET


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_AUDIO_SUFFIXES = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav", ".flac"}
_MAX_TAG_CHARS = 4000


def normalize_audio_format(value: object | None) -> str:
    text = str(value or "aac").strip().lower().lstrip(".")
    if text in {"mp3", "mpeg", "mpga"}:
        return "mp3"
    return "aac"


def _find_sidecar_thumbnail(video_path: Path) -> Path | None:
    stem = video_path.with_suffix("")
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = Path(str(stem) + ext)
        if candidate.exists():
            return candidate
    parent = video_path.parent
    matches = sorted(parent.glob("*.jpg")) + sorted(parent.glob("*.png"))
    prefix = video_path.name.split(" [")[0]
    for match in matches:
        if match.stem.startswith(prefix) or prefix.startswith(match.stem[:20]):
            return match
    return matches[0] if matches else None


def _info_text(info: dict, *keys: str) -> str:
    for key in keys:
        val = info.get(key)
        if isinstance(val, (list, tuple)):
            parts: list[str] = []
            for item in val:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("text") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    parts.append(name)
            if parts:
                return ", ".join(parts)
            continue
        text = str(val or "").strip()
        if text:
            return text
    return ""


def _truncate_tag(text: str, limit: int = _MAX_TAG_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _single_line(text: str) -> str:
    return re.sub(r"[\r\n]+", " / ", text).strip()


def _author_url_from_info(info: dict) -> str:
    url = _info_text(info, "channel_url", "uploader_url", "artist_url", "creator_url")
    if url:
        return url
    extractor = _info_text(info, "extractor_key", "extractor", "ie_key").lower()
    channel_id = _info_text(info, "channel_id")
    if channel_id.startswith("UC") and "youtube" in extractor:
        return f"https://www.youtube.com/channel/{channel_id}"
    uploader_id = _info_text(info, "uploader_id")
    if uploader_id.startswith("@") and "youtube" in extractor:
        return f"https://www.youtube.com/{uploader_id}"
    if uploader_id and "youtube" in extractor:
        return f"https://www.youtube.com/@{uploader_id}"
    return ""


def _format_tag_date(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) == 4 and text.isdigit():
        return text
    return text


def metadata_tags_from_info(info: dict | None) -> dict[str, str]:
    """Title, artist, description and author URL for ffmpeg/ffmetadata."""
    if not info:
        return {}
    title = _info_text(info, "track", "title")
    artist = _info_text(info, "artist", "artists", "creator", "channel", "uploader")
    album = _info_text(info, "album", "playlist_title")
    description = _truncate_tag(_single_line(_info_text(info, "description")))
    author_url = _author_url_from_info(info)
    webpage = _info_text(info, "webpage_url", "original_url")
    date = _format_tag_date(_info_text(info, "release_date", "upload_date"))
    genre = _info_text(info, "genre")

    comment_parts: list[str] = []
    if author_url:
        comment_parts.append(author_url)
    if description:
        comment_parts.append(description)

    tags: dict[str, str] = {}
    if title:
        tags["title"] = title
    if artist:
        tags["artist"] = artist
        tags["album_artist"] = artist
    if album:
        tags["album"] = album
    if date:
        tags["date"] = date
    if genre:
        tags["genre"] = genre
    if description:
        tags["description"] = description
        tags["synopsis"] = description
    if comment_parts:
        tags["comment"] = " — ".join(comment_parts)
    if author_url:
        tags["copyright"] = author_url
        tags["purl"] = author_url
    elif webpage:
        tags["purl"] = webpage
    return tags


def _escape_ffmetadata(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\\n")
    )


def _write_ffmetadata(path: Path, tags: dict[str, str]) -> None:
    lines = [";FFMETADATA1"]
    for key, raw in tags.items():
        value = _truncate_tag(str(raw or "").strip())
        if not value:
            continue
        lines.append(f"{key}={_escape_ffmetadata(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_ffmpeg_replace(
    cmd: list[str],
    dest: Path,
    temp_out: Path,
    cancel_event: Event | None,
) -> bool:
    _check_cancel(cancel_event)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if _is_cancelled(cancel_event):
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                temp_out.unlink(missing_ok=True)
                raise DownloadCancelled("Загрузка отменена") from None
    if proc.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 0:
        dest.unlink(missing_ok=True)
        temp_out.rename(dest)
        return True
    temp_out.unlink(missing_ok=True)
    return False


def _ffmpeg_embed_cmd(
    ffmpeg: str,
    src: Path,
    dest: Path,
    *,
    thumb: Path | None,
    meta_file: Path | None,
) -> list[str]:
    cmd = [ffmpeg, "-y", "-i", str(src)]
    next_idx = 1
    thumb_idx: int | None = None
    meta_idx: int | None = None
    if thumb is not None:
        cmd += ["-i", str(thumb)]
        thumb_idx = next_idx
        next_idx += 1
    if meta_file is not None:
        cmd += ["-f", "ffmetadata", "-i", str(meta_file)]
        meta_idx = next_idx

    is_audio = src.suffix.lower() in _AUDIO_SUFFIXES
    if thumb_idx is not None and is_audio:
        cmd += [
            "-map",
            "0:a",
            "-map",
            str(thumb_idx),
            "-c:a",
            "copy",
            "-c:v",
            "mjpeg",
            "-disposition:v",
            "attached_pic",
            "-metadata:s:v",
            "title=Album cover",
            "-metadata:s:v",
            "comment=Cover (front)",
        ]
    elif thumb_idx is not None:
        cmd += [
            "-map",
            "0",
            "-map",
            str(thumb_idx),
            "-c",
            "copy",
            "-c:v:1",
            "mjpeg",
            "-disposition:v:1",
            "attached_pic",
        ]
    else:
        cmd += ["-map", "0", "-c", "copy"]

    if meta_idx is not None:
        cmd += ["-map_metadata", str(meta_idx)]
    if dest.suffix.lower() == ".mp3":
        cmd += ["-id3v2_version", "3"]
    cmd.append(str(dest))
    return cmd


def transcode_to_mp3(
    src: Path,
    cancel_event: Event | None = None,
) -> Path:
    """Re-encode AAC/M4A (or any audio) to MP3. YouTube never serves MP3 natively."""
    if src.suffix.lower() == ".mp3":
        return src
    if not src.exists():
        raise RuntimeError("Нет аудиофайла для перекодирования в MP3.")

    dest = src.with_suffix(".mp3")
    temp_out = src.with_name(src.stem + ".transcode.tmp.mp3")
    ffmpeg = get_ffmpeg_location()
    for encoder in ("libmp3lame", "mp3"):
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            encoder,
            "-b:a",
            "320k",
            str(temp_out),
        ]
        if _run_ffmpeg_replace(cmd, dest, temp_out, cancel_event):
            if src.resolve() != dest.resolve():
                src.unlink(missing_ok=True)
            return dest
    raise RuntimeError(
        "Не удалось перекодировать в MP3.\n"
        "Встроенный ffmpeg не смог закодировать MPEG Layer III."
    )


def _cleanup_sidecar_images(video_path: Path, thumb: Path | None) -> None:
    for leftover in video_path.parent.glob(video_path.stem + ".*"):
        if leftover.suffix.lower() in _IMAGE_SUFFIXES:
            leftover.unlink(missing_ok=True)
    if thumb is not None and thumb.exists() and thumb.suffix.lower() in _IMAGE_SUFFIXES:
        if thumb.parent == video_path.parent:
            thumb.unlink(missing_ok=True)


def embed_thumbnail(
    video_path: Path,
    thumb_path: Path | None = None,
    cancel_event: Event | None = None,
    info: dict | None = None,
) -> Path:
    """Embed cover art and tags (description, author URL) for players/Explorer."""
    if not video_path.exists():
        return video_path

    thumb = thumb_path or _find_sidecar_thumbnail(video_path)
    tags = metadata_tags_from_info(info)
    if thumb is None and not tags:
        return video_path

    _check_cancel(cancel_event)
    ffmpeg = get_ffmpeg_location()
    temp_out = video_path.with_name(video_path.stem + ".thumb.tmp" + video_path.suffix)
    meta_file = video_path.with_name(video_path.stem + ".ffmetadata") if tags else None
    try:
        if meta_file is not None:
            _write_ffmetadata(meta_file, tags)

        combined_ok = False
        if thumb is not None and meta_file is not None:
            combined_ok = _run_ffmpeg_replace(
                _ffmpeg_embed_cmd(
                    ffmpeg, video_path, temp_out, thumb=thumb, meta_file=meta_file
                ),
                video_path,
                temp_out,
                cancel_event,
            )
        if not combined_ok:
            if thumb is not None:
                _run_ffmpeg_replace(
                    _ffmpeg_embed_cmd(
                        ffmpeg, video_path, temp_out, thumb=thumb, meta_file=None
                    ),
                    video_path,
                    temp_out,
                    cancel_event,
                )
            if meta_file is not None:
                _run_ffmpeg_replace(
                    _ffmpeg_embed_cmd(
                        ffmpeg, video_path, temp_out, thumb=None, meta_file=meta_file
                    ),
                    video_path,
                    temp_out,
                    cancel_event,
                )
    finally:
        if meta_file is not None:
            meta_file.unlink(missing_ok=True)

    _cleanup_sidecar_images(video_path, thumb)
    return video_path


def _quality_bound(quality: str | None) -> int | None:
    """Pixel cap for the shorter side, or None for unlimited."""
    text = (quality or "best").strip().lower()
    if text in {"best", "max", "highest"}:
        return None
    try:
        value = int(text.rstrip("p"))
    except ValueError:
        return None
    return value if value > 0 else None


def format_sort_keys(quality: str = "best", *, url: str | None = None) -> list[str]:
    """Sort by resolution first; cap '1080p' via res so vertical 1080x1920 still matches."""
    bound = _quality_bound(quality)
    res = f"res:{bound}" if bound else "res"
    keys = [res]
    if is_youtube_shorts(url):
        # Same short-side: prefer 9:16 over a 16:9 TV crop (1080x1920 beats 1920x1080).
        keys.append("+width")
    # Same short-side: prefer H.264 so Explorer can show a preview.
    keys.extend(["fps", "hdr:12", "vcodec:h264", "acodec:mp4a", "br"])
    return keys


def format_selector(
    *,
    audio_only: bool = False,
    quality: str = "best",
    site: str | None = None,
    url: str | None = None,
) -> str:
    """Build yt-dlp format string for video quality or audio-only.

    Quality caps live in format_sort (res:1080), not height<=1080: YouTube
    Shorts are 1080x1920, so a height filter would drop 1080p and pick 480p.
    """
    if audio_only:
        return "ba[ext=m4a]/ba[acodec^=mp4a]/ba/b"
    return "bv*+ba[ext=m4a]/bv*+ba/b"


def fallback_format_selector(
    *,
    audio_only: bool = False,
    quality: str = "best",
    url: str | None = None,
) -> str:
    """Looser selector that still merges best video+audio (not 360p muxed MP4)."""
    if audio_only:
        return "ba/b"
    return "bv*+ba/b"


def _ydl_storage_opts() -> dict:
    from bridge import cache_dir, temp_dir

    cache = cache_dir()
    tmp = temp_dir()
    cache.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        "cachedir": str(cache),
        "paths": {"temp": str(tmp)},
    }


def _ydl_js_runtimes() -> dict:
    """Enable a local JS runtime so YouTube web clients can solve challenges."""
    try:
        from yt_dlp.globals import supported_js_runtimes

        known = set(supported_js_runtimes.value or {})
    except Exception:
        return {}
    runtimes: dict = {}
    if "node" in known and shutil.which("node"):
        runtimes["node"] = {}
    if "deno" in known and shutil.which("deno"):
        runtimes["deno"] = {}
    return runtimes


class _QuietCookieLogger:
    def debug(self, *args, **kwargs) -> None:
        return

    def info(self, *args, **kwargs) -> None:
        return

    def warning(self, *args, **kwargs) -> None:
        return

    def error(self, *args, **kwargs) -> None:
        return


_YOUTUBE_AUTH_NAMES = {
    "LOGIN_INFO",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}
_YOUTUBE_SESSION_NAMES = _YOUTUBE_AUTH_NAMES | {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA",
    "__Secure-YEC",
    "PREF",
    "SOCS",
    "CONSENT",
}
_BROWSER_COOKIES_TRIED = False


def _youtube_cookie_file() -> Path:
    from bridge import user_data_dir

    folder = user_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "youtube-cookies.txt"


def _cookie_names_from_file(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.is_file():
        return names
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            names.add(parts[5])
    return names


def _cookie_file_has_youtube_session(path: Path) -> bool:
    names = _cookie_names_from_file(path)
    return bool(names & _YOUTUBE_SESSION_NAMES)


def _cookie_file_has_youtube_auth(path: Path) -> bool:
    names = _cookie_names_from_file(path)
    return "LOGIN_INFO" in names and bool(
        names & {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}
    )


def _looks_like_youtube_cookies(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if text.startswith("["):
        lower = text.lower()
        return "youtube" in lower or "login_info" in lower or "sapisid" in lower
    return any(name in text for name in _YOUTUBE_SESSION_NAMES) or "SID=" in text


def _add_cookie_to_jar(jar, *, name: str, value: str, domain: str, path: str, secure: bool, expires) -> None:
    host = (domain or ".youtube.com").strip() or ".youtube.com"
    if host.startswith("."):
        cookie_domain = host
        domain_initial_dot = True
    else:
        cookie_domain = host
        domain_initial_dot = False
    expiry = None
    if expires not in (None, "", 0, "0"):
        try:
            expiry = int(float(expires))
        except (TypeError, ValueError):
            expiry = None
    jar.set_cookie(
        Cookie(
            0,
            name,
            value,
            None,
            False,
            cookie_domain,
            True,
            domain_initial_dot,
            path or "/",
            True,
            bool(secure) or name.startswith("__Secure-") or name.startswith("__Host-"),
            expiry,
            expiry is None,
            None,
            None,
            {},
        )
    )


def _cookies_payload_to_jar(raw: str):
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar()
    text = (raw or "").strip()
    if not text:
        return jar
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            items = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "")
                if not name:
                    continue
                _add_cookie_to_jar(
                    jar,
                    name=name,
                    value=value,
                    domain=str(item.get("domain") or ".youtube.com"),
                    path=str(item.get("path") or "/"),
                    secure=bool(item.get("secure")),
                    expires=item.get("expirationDate") or item.get("expires"),
                )
            return jar
    if "\t" in text or text.startswith("# Netscape"):
        dest = _youtube_cookie_file()
        body = text if text.lstrip().startswith("#") else "# Netscape HTTP Cookie File\n" + text
        try:
            dest.write_text(body, encoding="utf-8")
            loaded = YoutubeDLCookieJar(str(dest))
            loaded.load(ignore_discard=True, ignore_expires=True)
            return loaded
        except Exception:
            pass
    for part in text.split(";"):
        piece = part.strip()
        if "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name = name.strip()
        if not name:
            continue
        _add_cookie_to_jar(
            jar,
            name=name,
            value=value.strip(),
            domain=".youtube.com",
            path="/",
            secure=name.startswith("__Secure-") or name.startswith("__Host-"),
            expires=None,
        )
    return jar


def _save_cookie_jar(jar, path: Path) -> bool:
    if jar is None or len(jar) == 0:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        jar.save(filename=str(path), ignore_discard=True, ignore_expires=True)
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _browser_cookie_specs() -> list[tuple[str, str | None, str]]:
    specs: list[tuple[str, str | None, str]] = [
        ("firefox", None, "Firefox"),
        ("edge", None, "Edge"),
        ("chrome", None, "Chrome"),
        ("opera", None, "Opera"),
        ("brave", None, "Brave"),
        ("vivaldi", None, "Vivaldi"),
    ]
    local = Path(os.environ.get("LOCALAPPDATA", "") or "")
    yandex = local / "Yandex" / "YandexBrowser" / "User Data"
    if yandex.is_dir():
        specs.insert(1, ("chrome", str(yandex), "Яндекс.Браузер"))
    return specs


def _browser_profile_exists(browser: str, profile: str | None) -> bool:
    if profile:
        return Path(profile).exists()
    local = Path(os.environ.get("LOCALAPPDATA", "") or "")
    roaming = Path(os.environ.get("APPDATA", "") or "")
    known = {
        "firefox": roaming / "Mozilla" / "Firefox" / "Profiles",
        "edge": local / "Microsoft" / "Edge" / "User Data",
        "chrome": local / "Google" / "Chrome" / "User Data",
        "opera": roaming / "Opera Software" / "Opera Stable",
        "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
        "vivaldi": local / "Vivaldi" / "User Data",
    }
    path = known.get(browser)
    return bool(path and path.exists())


def _extract_youtube_cookies_from_browsers(
    report: Callable[[str], None] | None = None,
    *,
    force: bool = False,
) -> bool:
    global _BROWSER_COOKIES_TRIED
    dest = _youtube_cookie_file()
    if _BROWSER_COOKIES_TRIED and not force:
        return _cookie_file_has_youtube_session(dest)
    _BROWSER_COOKIES_TRIED = True
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception:
        return False
    logger = _QuietCookieLogger()
    if report is not None:
        report("Чтение cookies браузера…")
    for browser, profile, _label in _browser_cookie_specs():
        if not _browser_profile_exists(browser, profile):
            continue
        try:
            jar = extract_cookies_from_browser(browser, profile, logger)
        except Exception:
            continue
        names = {cookie.name for cookie in jar if cookie.value}
        if not (names & _YOUTUBE_SESSION_NAMES):
            continue
        if _save_cookie_jar(jar, dest):
            return True
    return _cookie_file_has_youtube_session(dest)


def _import_youtube_cookie_payload(raw: str) -> bool:
    if not _looks_like_youtube_cookies(raw):
        return False
    jar = _cookies_payload_to_jar(raw)
    return _save_cookie_jar(jar, _youtube_cookie_file())


def _ensure_youtube_cookiefile(
    cookies: str = "",
    report: Callable[[str], None] | None = None,
    *,
    refresh_from_browser: bool = False,
) -> Path | None:
    dest = _youtube_cookie_file()
    imported = _import_youtube_cookie_payload(cookies)
    if imported and _cookie_file_has_youtube_session(dest):
        if report is not None:
            report("Сессия YouTube из браузера…")
        return dest
    if not refresh_from_browser and _cookie_file_has_youtube_session(dest):
        return dest
    if _extract_youtube_cookies_from_browsers(report, force=refresh_from_browser) and _cookie_file_has_youtube_session(dest):
        return dest
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    return None


def _apply_youtube_cookies(opts: dict, cookie_file: Path | None) -> dict:
    if cookie_file is None:
        return opts
    opts["cookiefile"] = str(cookie_file)
    return opts


def _youtube_bot_error() -> RuntimeError:
    return RuntimeError(
        "YouTube просит подтвердить, что вы не бот.\n\n"
        "1. Откройте это видео в браузере и войдите в аккаунт Google.\n"
        "2. Нажмите кнопку TubeSave на странице ролика — приложение возьмёт cookies сессии.\n"
        "3. Если кнопки нет: в TubeSave нажмите «Браузер» и установите расширение.\n\n"
        "Firefox обычно отдаёт cookies надёжнее Chrome. После одного скачивания через "
        "расширение следующие загрузки из приложения тоже могут использовать эту сессию."
    )


def _reraise_youtube_error(exc: Exception) -> None:
    message = str(exc)
    if _is_proxy_error(message):
        raise _proxy_error() from None
    if _is_youtube_bot_check(message):
        raise _youtube_bot_error() from None
    raise exc


def _reraise_download_error(exc: Exception) -> None:
    message = str(exc)
    if _is_proxy_error(message):
        raise _proxy_error() from None
    raise exc


def build_ydl_opts(
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
    audio_format: str = "aac",
    site: str | None = None,
    url: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    cancel_event: Event | None = None,
    cleanup: DownloadCleanup | None = None,
    impersonate: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    opts: dict = {
        **_ydl_storage_opts(),
        "outtmpl": str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        "ffmpeg_location": get_ffmpeg_location(),
        "writethumbnail": True,
        "writeinfojson": False,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": False,
        "format": format_selector(
            audio_only=audio_only, quality=quality, site=site, url=url
        ),
        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
        ],
    }

    want_mp3 = audio_only and normalize_audio_format(audio_format) == "mp3"
    if audio_only:
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3" if want_mp3 else "m4a",
                "preferredquality": "320" if want_mp3 else "0",
            }
        )
    else:
        opts["merge_output_format"] = "mp4"
        # Resolution first (res:1080 caps the short side, so 1080x1920 Shorts count as 1080p).
        opts["format_sort"] = format_sort_keys(quality, url=url)
        opts["format_sort_force"] = True

    apply_download_range(opts, start_time, end_time)

    js_runtimes = _ydl_js_runtimes()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes

    # Browser impersonation helps YouTube CDN; also fine for most other sites.
    if impersonate:
        target = get_impersonate_target()
        if target is not None:
            opts["impersonate"] = target

    progress_hooks: list[ProgressCallback] = []
    if cleanup is not None or cancel_event is not None:
        progress_hooks.append(
            lambda data: _abort_if_cancelled(data, cancel_event, cleanup)
        )
    if progress_hook is not None:
        progress_hooks.append(progress_hook)
    if progress_hooks:
        opts["progress_hooks"] = progress_hooks

    postprocessor_hooks: list[ProgressCallback] = []
    if cleanup is not None or cancel_event is not None:
        postprocessor_hooks.append(
            lambda data: _abort_if_cancelled(data, cancel_event, cleanup)
        )
    if status_callback is not None:

        def postprocessor_hook(data: dict) -> None:
            _abort_if_cancelled(data, cancel_event, cleanup)
            status = data.get("status")
            postprocessor = data.get("postprocessor") or ""
            if status == "started":
                if postprocessor == "Merger":
                    status_callback("Объединение видео и аудио…")
                elif postprocessor == "FFmpegExtractAudio":
                    status_callback(
                        "Перекодирование в MP3…" if want_mp3 else "Извлечение аудио…"
                    )
                elif postprocessor == "FFmpegThumbnailsConvertor":
                    status_callback("Подготовка превью…")
                elif postprocessor == "FFmpegVideoConvertor":
                    status_callback("Конвертация в MP4…")
                elif postprocessor == "MoveFiles":
                    status_callback("Сохранение файла…")
                elif postprocessor:
                    status_callback(f"Обработка: {postprocessor}…")
            elif status == "finished" and postprocessor == "Merger":
                status_callback("Объединение завершено")

        postprocessor_hooks.append(postprocessor_hook)
    if postprocessor_hooks:
        opts["postprocessor_hooks"] = postprocessor_hooks

    return _apply_direct_network(opts)


def fetch_video_info(
    url: str,
    cancel_event: Event | None = None,
    cookies: str = "",
) -> dict:
    url = url.strip()
    site = detect_site(url)
    if site is None:
        raise ValueError(
            "Неподдерживаемая ссылка. Доступны:\n" + SUPPORTED_SITES_HINT
        )
    _check_cancel(cancel_event)
    opts = {
        **_ydl_storage_opts(),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }
    js_runtimes = _ydl_js_runtimes()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes
    impersonate = get_impersonate_target()
    if impersonate is not None:
        opts["impersonate"] = impersonate
    if site == "youtube":
        # Save extension cookies for later, but don't attach them on the first try.
        # Account cookies make yt-dlp skip jsless clients and then fail with
        # "Requested format is not available" on many public videos.
        _ensure_youtube_cookiefile(cookies)
        opts = _with_youtube_preferred_clients(opts)
    if cancel_event is not None:
        opts["progress_hooks"] = [lambda data: _abort_if_cancelled(data, cancel_event)]
    _apply_direct_network(opts)
    try:
        with _youtube_dl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        message = str(exc)
        if site == "youtube" and _youtube_needs_client_fallback(message):
            cookie_file = _ensure_youtube_cookiefile(cookies)
            retry_opts = dict(opts)
            if _youtube_needs_cookies(message) and cookie_file is not None:
                _apply_youtube_cookies(retry_opts, cookie_file)
                retry_opts = _with_youtube_authed_clients(retry_opts)
            else:
                retry_opts = _strip_youtube_cookies(retry_opts)
                retry_opts = _with_youtube_preferred_clients(retry_opts)
            try:
                with _youtube_dl(retry_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as retry_exc:
                # Last resort: cookieless jsless clients for public videos.
                last_opts = _with_youtube_preferred_clients(_strip_youtube_cookies(dict(opts)))
                try:
                    with _youtube_dl(last_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                except Exception as last_exc:
                    if _is_proxy_error(str(last_exc)):
                        raise _proxy_error() from None
                    _reraise_youtube_error(retry_exc if _youtube_needs_cookies(message) else last_exc)
        elif _is_proxy_error(message):
            # Retry without browser impersonation (urllib path respects proxy="").
            retry_opts = dict(opts)
            retry_opts.pop("impersonate", None)
            try:
                with _youtube_dl(retry_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as retry_exc:
                if _is_proxy_error(str(retry_exc)):
                    raise _proxy_error() from None
                if site == "youtube":
                    _reraise_youtube_error(retry_exc)
                raise
        elif site == "youtube":
            _reraise_youtube_error(exc)
        else:
            raise
    _check_cancel(cancel_event)
    if info is None:
        raise RuntimeError("Не удалось получить информацию о видео.")
    return info


def _resolve_output_path(
    ydl: yt_dlp.YoutubeDL,
    info: dict,
    *,
    audio_only: bool = False,
    audio_format: str = "aac",
) -> Path:
    filepath = Path(ydl.prepare_filename(info))
    if audio_only:
        preferred = (".mp3",) if normalize_audio_format(audio_format) == "mp3" else (".m4a", ".aac")
        others = (".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav", ".flac")
        exts = list(preferred) + [ext for ext in others if ext not in preferred]
        for ext in exts:
            candidate = filepath.with_suffix(ext)
            if candidate.exists():
                return candidate
        # After extract, original media may be deleted; search by id/title stem.
        stem = filepath.with_suffix("").name
        matches = sorted(filepath.parent.glob(stem + ".*"))
        preferred_set = set(preferred)
        audio_matches = [
            path
            for path in matches
            if path.suffix.lower() in set(others)
        ]
        preferred_matches = [path for path in audio_matches if path.suffix.lower() in preferred_set]
        if preferred_matches:
            return preferred_matches[0]
        if audio_matches:
            return audio_matches[0]
        return filepath

    if filepath.suffix.lower() != ".mp4":
        mp4_path = filepath.with_suffix(".mp4")
        if mp4_path.exists():
            return mp4_path
    return filepath


def _unwrap_info(info: dict | None) -> dict:
    if not info:
        raise RuntimeError("Не удалось получить информацию о видео.")
    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            if entry:
                return entry
    return info


def _try_download(
    url: str,
    opts: dict,
    report: Callable[[str], None] | None = None,
    *,
    audio_only: bool = False,
    audio_format: str = "aac",
    cancel_event: Event | None = None,
) -> tuple[Path, dict]:
    _check_cancel(cancel_event)
    if report is not None:
        report("Получение информации о видео…")
    try:
        with _youtube_dl(opts) as ydl:
            info = _unwrap_info(ydl.extract_info(url, download=True))
            _check_cancel(cancel_event)
            return (
                _resolve_output_path(
                    ydl, info, audio_only=audio_only, audio_format=audio_format
                ),
                info,
            )
    except DownloadCancelled:
        raise
    except Exception as exc:
        if _is_proxy_error(str(exc)) and opts.get("impersonate") is not None:
            retry_opts = dict(opts)
            retry_opts.pop("impersonate", None)
            try:
                with _youtube_dl(retry_opts) as ydl:
                    info = _unwrap_info(ydl.extract_info(url, download=True))
                    _check_cancel(cancel_event)
                    return (
                        _resolve_output_path(
                            ydl, info, audio_only=audio_only, audio_format=audio_format
                        ),
                        info,
                    )
            except DownloadCancelled:
                raise
            except Exception as retry_exc:
                _reraise_download_error(retry_exc)
                raise
        _reraise_download_error(exc)
        raise


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", str(text or "")).strip(" .")
    return (cleaned[:180] or "track")


def _yandex_cookie_file() -> Path:
    from bridge import user_data_dir

    folder = user_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "yandex-cookies.txt"


def _yandex_token_cache_file() -> Path:
    from bridge import user_data_dir

    folder = user_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "yandex-music-token.json"


def _looks_like_yandex_cookies(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if text.startswith("["):
        lower = text.lower()
        return "session_id" in lower or "yandex" in lower
    return any(name in text for name in _YANDEX_SESSION_NAMES)


def _yandex_domain_ok(domain: str) -> bool:
    host = (domain or "").lstrip(".").lower()
    return host.endswith("yandex.ru") or host.endswith("yandex.net")


def _yandex_parse_cookie_header(header: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for piece in (header or "").split(";"):
        chunk = piece.strip()
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name = name.strip()
        if name:
            parsed[name] = value.strip()
    return parsed


def _yandex_trim_cookie_header(header: str) -> str:
    parsed = _yandex_parse_cookie_header(header)
    if not (parsed.keys() & _YANDEX_SESSION_NAMES):
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    for name in _YANDEX_OAUTH_COOKIE_ORDER:
        value = parsed.get(name)
        if not value or name in seen:
            continue
        seen.add(name)
        parts.append(f"{name}={value}")
    for name, value in parsed.items():
        if name in seen or name not in _YANDEX_OAUTH_COOKIE_NAMES or not value:
            continue
        seen.add(name)
        parts.append(f"{name}={value}")
    trimmed = "; ".join(parts)
    if len(trimmed.encode("utf-8")) > _YANDEX_MAX_COOKIE_HEADER:
        return ""
    return trimmed


def _yandex_cookie_header_from_jar(jar) -> str:
    parsed: dict[str, str] = {}
    for cookie in jar:
        if not cookie.value:
            continue
        if not _yandex_domain_ok(cookie.domain or ""):
            continue
        name = str(cookie.name or "")
        if not name or name not in _YANDEX_OAUTH_COOKIE_NAMES:
            continue
        parsed.setdefault(name, str(cookie.value))
    return _yandex_trim_cookie_header("; ".join(f"{name}={value}" for name, value in parsed.items()))


def _yandex_cookies_payload_to_header(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if not isinstance(items, list):
            return ""
        parsed: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "")
            domain = str(item.get("domain") or "")
            if not name or not value or not _yandex_domain_ok(domain):
                continue
            if name not in _YANDEX_OAUTH_COOKIE_NAMES:
                continue
            parsed.setdefault(name, value)
        return _yandex_trim_cookie_header("; ".join(f"{name}={value}" for name, value in parsed.items()))
    return _yandex_trim_cookie_header(text)


def _save_yandex_cookie_header(header: str) -> bool:
    text = _yandex_trim_cookie_header((header or "").strip())
    if not text:
        return False
    try:
        dest = _yandex_cookie_file()
        dest.write_text(text, encoding="utf-8")
        return dest.is_file() and dest.stat().st_size > 0
    except OSError:
        return False


def _load_yandex_cookie_header() -> str:
    path = _yandex_cookie_file()
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return _yandex_trim_cookie_header(text)


def _extract_yandex_cookies_from_browsers(
    report: Callable[[str], None] | None = None,
    *,
    force: bool = False,
) -> bool:
    global _YANDEX_BROWSER_COOKIES_TRIED
    if _YANDEX_BROWSER_COOKIES_TRIED and not force:
        return bool(_load_yandex_cookie_header())
    _YANDEX_BROWSER_COOKIES_TRIED = True
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception:
        return False
    logger = _QuietCookieLogger()
    if report is not None:
        report("Чтение cookies Яндекса из браузера…")
    for browser, profile, _label in _browser_cookie_specs():
        if not _browser_profile_exists(browser, profile):
            continue
        try:
            jar = extract_cookies_from_browser(browser, profile, logger)
        except Exception:
            continue
        header = _yandex_cookie_header_from_jar(jar)
        if header and _save_yandex_cookie_header(header):
            return True
    return bool(_load_yandex_cookie_header())


def _ensure_yandex_cookie_header(
    cookies: str = "",
    report: Callable[[str], None] | None = None,
    *,
    refresh_from_browser: bool = False,
) -> str:
    imported = _yandex_cookies_payload_to_header(cookies)
    if imported:
        _save_yandex_cookie_header(imported)
        if report is not None:
            report("Сессия Яндекс.Музыки из браузера…")
        return imported
    if not refresh_from_browser:
        cached = _load_yandex_cookie_header()
        if cached:
            return cached
    if _extract_yandex_cookies_from_browsers(report, force=refresh_from_browser):
        return _load_yandex_cookie_header()
    return ""


def _load_yandex_music_token_cache() -> str:
    path = _yandex_token_cache_file()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    token = str(data.get("access_token") or "")
    expires_at = float(data.get("expires_at") or 0)
    if token and expires_at > time.time() + 60:
        return token
    return ""


def _save_yandex_music_token_cache(token: str, expires_in: int) -> None:
    if not token:
        return
    payload = {
        "access_token": token,
        "expires_at": time.time() + max(int(expires_in or 0), 300),
    }
    try:
        _yandex_token_cache_file().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _http_form_post(url: str, data: dict[str, str], headers: dict[str, str]) -> dict:
    body = urlencode(data).encode("utf-8")
    req_headers = {
        **headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    req = Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urlopen(req, timeout=25) as resp:
            raw = resp.read()
    except HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 413:
            raise RuntimeError(
                "Яндекс.Музыка: слишком много cookies сессии.\n"
                "Обновите TubeSave до последней версии и перезагрузите расширение."
            ) from exc
        raise RuntimeError(detail or str(exc)) from exc
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def _yandex_x_token_from_cookies(cookie_header: str) -> str:
    cookie_header = _yandex_trim_cookie_header(cookie_header)
    if not cookie_header:
        raise RuntimeError("sessionid.invalid")
    payload = _http_form_post(
        "https://mobileproxy.passport.yandex.net/1/bundle/oauth/token_by_sessionid",
        {
            "client_id": YANDEX_TOKEN_BY_SESSION_CLIENT_ID,
            "client_secret": YANDEX_TOKEN_BY_SESSION_CLIENT_SECRET,
        },
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Ya-Client-Host": "passport.yandex.ru",
            "Ya-Client-Cookie": cookie_header,
        },
    )
    token = str(payload.get("access_token") or "")
    if token:
        return token
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        raise RuntimeError(", ".join(str(item) for item in errors))
    raise RuntimeError(str(payload.get("error") or "sessionid.invalid"))


def _yandex_music_token_from_x_token(x_token: str) -> tuple[str, int]:
    payload = _http_form_post(
        "https://oauth.mobile.yandex.net/1/token",
        {
            "client_id": YANDEX_MUSIC_CLIENT_ID,
            "client_secret": YANDEX_MUSIC_CLIENT_SECRET,
            "grant_type": "x-token",
            "access_token": x_token,
        },
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
        },
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError(str(payload.get("error") or "music-token-failed"))
    expires_in = int(payload.get("expires_in") or 3600)
    return token, expires_in


def _ensure_yandex_music_oauth(
    cookies: str = "",
    report: Callable[[str], None] | None = None,
) -> str:
    cached = _load_yandex_music_token_cache()
    if cached:
        return cached

    cookie_header = _ensure_yandex_cookie_header(cookies, report)
    if not cookie_header:
        return ""

    if report is not None:
        report("Авторизация Яндекс.Музыки…")
    try:
        x_token = _yandex_x_token_from_cookies(cookie_header)
        music_token, expires_in = _yandex_music_token_from_x_token(x_token)
    except Exception:
        return ""
    _save_yandex_music_token_cache(music_token, expires_in)
    return music_token


def prepare_yandex_cookies(raw: str) -> str:
    """Compact Yandex session cookies only — never pass a huge browser payload through."""
    return _yandex_cookies_payload_to_header(raw or "")


def _yandex_headers(page_url: str, oauth_token: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://music.yandex.ru",
        "Referer": page_url or "https://music.yandex.ru/",
        "X-Yandex-Music-Client": "YandexMusicAndroid/24023231",
    }
    if oauth_token:
        headers["Authorization"] = f"OAuth {oauth_token}"
    return headers


def _http_read(url: str, headers: dict[str, str], timeout: float = 25.0) -> bytes:
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                "Яндекс.Музыка: требуется вход в аккаунт.\n"
                "Откройте music.yandex.ru, войдите в аккаунт и нажмите «Скачать» снова."
            ) from exc
        if exc.code == 413:
            raise RuntimeError(
                "Яндекс.Музыка: слишком много cookies сессии.\n"
                "Перезагрузите расширение TubeSave и нажмите «Скачать» снова."
            ) from exc
        raise


def _http_json(url: str, headers: dict[str, str]) -> dict:
    raw = _http_read(url, headers)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _yandex_sign(message: str) -> str:
    digest = hmac.new(YANDEX_SIGN_KEY.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")[:-1]


def parse_yandex_track_id(url: str) -> str | None:
    text = url or ""
    match = YANDEX_TRACK_RE.search(text)
    if match:
        return match.group("track")
    fragment = urlparse(text).fragment or ""
    hash_match = re.search(r"/track/(?P<track>\d+)", fragment, re.IGNORECASE)
    if hash_match:
        return hash_match.group("track")
    try:
        query = parse_qs(urlparse(text).query)
        for key in ("trackId", "track_id", "track"):
            values = query.get(key) or []
            if values and re.fullmatch(r"\d+", str(values[0])):
                return str(values[0])
    except Exception:
        pass
    return None


def _yandex_file_info(track_id: str, headers: dict[str, str], quality: str, codecs: str, transport: str) -> dict:
    ts = int(time.time())
    message = f"{ts}{track_id}{quality}{codecs}{transport}".replace(",", "")
    params = {
        "ts": ts,
        "trackId": track_id,
        "quality": quality,
        "codecs": codecs,
        "transports": transport,
        "sign": _yandex_sign(message),
    }
    return _http_json(f"{YANDEX_API}/get-file-info?{urlencode(params)}", headers)


def _yandex_direct_from_download_info(track_id: str, headers: dict[str, str]) -> tuple[str, bool]:
    data = _http_json(f"{YANDEX_API}/tracks/{track_id}/download-info", headers)
    items = data.get("result") or []
    if not items:
        raise RuntimeError("Яндекс.Музыка не вернула ссылку на файл.")
    best = max(items, key=lambda item: int(item.get("bitrateInKbps") or 0))
    preview = bool(best.get("preview"))
    info_url = str(best.get("downloadInfoUrl") or "")
    if not info_url:
        raise RuntimeError("Яндекс.Музыка не вернула downloadInfoUrl.")
    sep = "&" if "?" in info_url else "?"
    payload = _http_read(info_url + sep + "format=json", headers)
    try:
        fd = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Не удалось разобрать ссылку Яндекс.Музыки.") from exc
    path = str(fd.get("path") or "")
    sig = str(fd.get("s") or "")
    host = str(fd.get("host") or "")
    ts = str(fd.get("ts") or "")
    if not (path and sig and host and ts):
        raise RuntimeError("Неполный ответ download-info Яндекс.Музыки.")
    key = hashlib.md5((YANDEX_MD5_SALT + path[1:] + sig).encode()).hexdigest()
    return f"https://{host}/get-mp3/{key}/{ts}{path}", preview


def _yandex_pick_stream(track_id: str, headers: dict[str, str]) -> tuple[str, str, bool]:
    preview_fallback: tuple[str, str, bool] | None = None
    attempts = (
        ("nq", "mp3", "raw"),
        ("high", "mp3,aac", "raw"),
        ("lossless", "mp3,aac,he-aac,flac", "raw"),
    )
    for quality, codecs, transport in attempts:
        try:
            payload = _yandex_file_info(track_id, headers, quality, codecs, transport)
            info = (payload.get("result") or {}).get("downloadInfo") or {}
            urls = info.get("urls") or []
            if not urls:
                continue
            preview = str(info.get("quality") or "").lower() == "preview"
            ext = "mp3"
            codec = str(info.get("codec") or "mp3").lower()
            if "aac" in codec:
                ext = "m4a"
            elif "flac" in codec:
                ext = "flac"
            if preview:
                preview_fallback = (str(urls[0]), ext, True)
                continue
            return str(urls[0]), ext, False
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError, OSError):
            continue
    try:
        url, preview = _yandex_direct_from_download_info(track_id, headers)
        if not preview:
            return url, "mp3", False
        preview_fallback = (url, "mp3", True)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError, OSError):
        pass
    if preview_fallback:
        return preview_fallback
    raise RuntimeError("Не удалось получить файл Яндекс.Музыки.")


def _download_binary(
    url: str,
    dest: Path,
    headers: dict[str, str],
    progress_hook: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> None:
    _check_cancel(cancel_event)
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=60) as resp, dest.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                _check_cancel(cancel_event)
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress_hook is not None:
                    progress_hook(
                        {
                            "status": "downloading",
                            "downloaded_bytes": done,
                            "total_bytes": total or None,
                        }
                    )
    except DownloadCancelled:
        dest.unlink(missing_ok=True)
        raise
    if progress_hook is not None:
        progress_hook({"status": "finished", "filename": str(dest)})


def _yandex_info_from_track(
    track: dict,
    page_url: str,
    *,
    artist: str = "",
    title: str = "",
) -> dict:
    artists = track.get("artists") or []
    artist_url = ""
    for item in artists:
        if isinstance(item, dict) and item.get("id"):
            artist_url = f"https://music.yandex.ru/artist/{item['id']}"
            break
    albums = track.get("albums") or []
    album0 = albums[0] if albums and isinstance(albums[0], dict) else {}
    album_title = str(album0.get("title") or "").strip()
    version = str(track.get("version") or "").strip()
    display_title = title or str(track.get("title") or "")
    if version and version.lower() not in display_title.lower():
        display_title = f"{display_title} ({version})"
    description_parts: list[str] = []
    if album_title:
        description_parts.append(album_title)
    if version:
        description_parts.append(version)
    year = album0.get("year")
    upload_date = f"{year}0101" if year else ""
    return {
        "title": display_title,
        "artist": artist,
        "album": album_title,
        "genre": str(album0.get("genre") or ""),
        "description": "\n".join(description_parts),
        "channel_url": artist_url,
        "webpage_url": page_url,
        "upload_date": upload_date,
    }


def download_yandex_music(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    cookies: str = "",
    cancel_event: Event | None = None,
    audio_format: str = "aac",
) -> Path:
    track_id = parse_yandex_track_id(url)
    if not track_id:
        raise ValueError(
            "Нужна ссылка на трек Яндекс.Музыки (…/track/123).\n"
            "Откройте страницу трека или запустите его в плеере и нажмите «Скачать» снова."
        )

    cookies = prepare_yandex_cookies(cookies)

    def report(message: str) -> None:
        _check_cancel(cancel_event)
        if status_callback is not None:
            status_callback(message)

    oauth_token = _ensure_yandex_music_oauth(cookies, report)
    headers = _yandex_headers(url, oauth_token)
    report("Подключение к Яндекс.Музыке…")
    meta = _http_json(f"{YANDEX_API}/tracks/{track_id}", headers)
    tracks = meta.get("result") or []
    track = tracks[0] if tracks else {}
    title = str(track.get("title") or f"track {track_id}")
    artists = track.get("artists") or []
    artist = ", ".join(
        str(item.get("name") or "") for item in artists if isinstance(item, dict) and item.get("name")
    )
    display = f"{artist} - {title}" if artist else title

    report(f"Трек: {display}")
    stream_url, ext, preview = _yandex_pick_stream(track_id, headers)
    if preview:
        raise RuntimeError(
            "Яндекс.Музыка отдала только превью (30 сек).\n"
            "Откройте music.yandex.ru под своим аккаунтом и нажмите «Скачать» ещё раз."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(f"{display} [{track_id}]") + f".{ext}"
    dest = output_dir / filename
    cleanup = DownloadCleanup(output_dir)
    cleanup.track(dest)
    report("Скачивание аудио…")
    try:
        _download_binary(stream_url, dest, headers, progress_hook, cancel_event)

        albums = track.get("albums") or []
        cover = ""
        if albums and isinstance(albums[0], dict):
            cover = str(albums[0].get("coverUri") or track.get("coverUri") or "")
        else:
            cover = str(track.get("coverUri") or "")
        thumb: Path | None = None
        if cover:
            if not cover.startswith("http"):
                cover = "https://" + cover.replace("%%", "400x400")
            else:
                cover = cover.replace("%%", "400x400")
            thumb = dest.with_suffix(".jpg")
            cleanup.track(thumb)
            try:
                report("Загрузка обложки…")
                _download_binary(cover, thumb, headers, None, cancel_event)
            except DownloadCancelled:
                raise
            except Exception:
                thumb.unlink(missing_ok=True)
                thumb = None
        if normalize_audio_format(audio_format) == "mp3" and dest.suffix.lower() != ".mp3":
            report("Перекодирование AAC → MP3…")
            dest = transcode_to_mp3(dest, cancel_event)
            cleanup.track(dest)
        report("Превью и метаданные…")
        embed_thumbnail(
            dest,
            thumb,
            cancel_event,
            info=_yandex_info_from_track(track, url, artist=artist, title=title),
        )
        return dest
    except DownloadCancelled:
        cleanup.purge()
        raise


def _is_youtube_bot_check(message: str) -> bool:
    lower = (message or "").lower().replace("’", "'")
    return "sign in to confirm" in lower or "not a bot" in lower


def _youtube_needs_client_fallback(message: str) -> bool:
    lower = (message or "").lower().replace("’", "'")
    return _is_youtube_bot_check(lower) or any(
        needle in lower
        for needle in (
            "403",
            "forbidden",
            "not available",
            "requested format is not available",
            "page needs to be reloaded",
            "private video",
            "login required",
            "please sign in",
        )
    )


def _apply_youtube_player_clients(opts: dict, clients: list[str]) -> dict:
    result = dict(opts)
    extractor_args = dict(result.get("extractor_args") or {})
    youtube_args = dict(extractor_args.get("youtube") or {})
    youtube_args["player_client"] = list(clients)
    extractor_args["youtube"] = youtube_args
    result["extractor_args"] = extractor_args
    return result


def _strip_youtube_cookies(opts: dict) -> dict:
    result = dict(opts)
    result.pop("cookiefile", None)
    return result


def _youtube_extractor_clients(attr: str, fallback: list[str]) -> list[str]:
    """Use yt-dlp's current default clients so Shorts keep 9:16 DASH (not muxed 360p)."""
    try:
        from yt_dlp.extractor.youtube._video import YoutubeIE

        values = getattr(YoutubeIE, attr, None)
        if values:
            return list(values)
    except Exception:
        pass
    return list(fallback)


def _with_youtube_preferred_clients(opts: dict) -> dict:
    # Jsless clients (android_vr / visionos) expose original 1080x1920 Shorts.
    # tv/ios/android currently collapse to muxed 360p (format 18). Skip web:
    # without a JS runtime those clients fail with "format is not available".
    return _apply_youtube_player_clients(
        opts,
        _youtube_extractor_clients("_DEFAULT_JSLESS_CLIENTS", ["android_vr"]),
    )


def _with_youtube_authed_clients(opts: dict) -> dict:
    """Clients that accept cookies (members-only / bot-check retries)."""
    return _apply_youtube_player_clients(
        opts,
        _youtube_extractor_clients(
            "_DEFAULT_AUTHED_CLIENTS",
            ["mweb", "web_safari", "web_embedded", "tv", "web"],
        ),
    )


def _with_youtube_fallback_clients(opts: dict, *, authed: bool) -> dict:
    fallback = dict(opts)
    fallback.pop("impersonate", None)
    if authed:
        return _with_youtube_authed_clients(fallback)
    return _with_youtube_preferred_clients(fallback)


def _youtube_needs_cookies(message: str) -> bool:
    lower = (message or "").lower().replace("’", "'")
    return _is_youtube_bot_check(lower) or any(
        needle in lower
        for needle in (
            "please sign in",
            "login required",
            "private video",
            "members only",
            "join this channel",
            "confirm your age",
            "age-restricted",
        )
    )


def download_video(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
    audio_format: str = "aac",
    cookies: str = "",
    cancel_event: Event | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
) -> Path:
    url = url.strip()
    site = detect_site(url)
    if site is None:
        raise ValueError(
            "Неподдерживаемая ссылка. Доступны:\n" + SUPPORTED_SITES_HINT
        )
    audio_format = normalize_audio_format(audio_format)
    start_time, end_time = normalize_time_range(start_time, end_time)
    # Yandex Music tracks are audio — always extract M4A/MP3.
    if site == "yandexmusic":
        path = download_yandex_music(
            url,
            output_dir,
            progress_hook,
            status_callback,
            cookies=cookies,
            cancel_event=cancel_event,
            audio_format=audio_format,
        )
        if start_time is not None or end_time is not None:
            if status_callback is not None:
                status_callback("Обрезка по времени…")
            return trim_existing_file(path, start_time, end_time, cancel_event)
        return path

    def report(message: str) -> None:
        _check_cancel(cancel_event)
        if status_callback is not None:
            status_callback(message)

    cleanup = DownloadCleanup(output_dir)
    try:
        label = SITE_LABELS.get(site, "сайт")
        report(f"Подключение к {label}…")
        opts = build_ydl_opts(
            output_dir,
            progress_hook,
            status_callback,
            audio_only=audio_only,
            quality=quality,
            audio_format=audio_format,
            site=site,
            url=url,
            start_time=start_time,
            end_time=end_time,
            cancel_event=cancel_event,
            cleanup=cleanup,
        )
        youtube_cookie_file = None
        youtube_used_cookies = False
        if site == "youtube":
            # Persist cookies from the extension, but start cookieless so DASH clients work.
            youtube_cookie_file = _ensure_youtube_cookiefile(cookies, report)
            opts = _with_youtube_preferred_clients(opts)

        last_error: Exception | None = None
        filepath: Path | None = None
        info: dict = {}
        for attempt in range(1, 4):
            try:
                if attempt > 1:
                    report(f"Повтор скачивания ({attempt}/3)…")
                    _interruptible_sleep(1.5 * attempt, cancel_event)
                filepath, info = _try_download(
                    url,
                    opts,
                    report,
                    audio_only=audio_only,
                    audio_format=audio_format,
                    cancel_event=cancel_event,
                )
                break
            except DownloadCancelled:
                raise
            except yt_dlp.utils.DownloadError as exc:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("Загрузка отменена") from None
                last_error = exc
                message = str(exc)
                # Retry only for transient CDN / player-client blocks.
                if site == "youtube":
                    if not _youtube_needs_client_fallback(message):
                        _reraise_youtube_error(exc)
                    if (
                        _youtube_needs_cookies(message)
                        and youtube_cookie_file is not None
                        and not youtube_used_cookies
                    ):
                        report("Нужна сессия YouTube…")
                        refreshed = _ensure_youtube_cookiefile(
                            cookies, report, refresh_from_browser=True
                        )
                        if refreshed is not None:
                            youtube_cookie_file = refreshed
                        _apply_youtube_cookies(opts, youtube_cookie_file)
                        opts = _with_youtube_authed_clients(opts)
                        youtube_used_cookies = True
                        try:
                            filepath, info = _try_download(
                                url,
                                opts,
                                report,
                                audio_only=audio_only,
                                audio_format=audio_format,
                                cancel_event=cancel_event,
                            )
                            break
                        except DownloadCancelled:
                            raise
                        except yt_dlp.utils.DownloadError as retry_exc:
                            last_error = retry_exc
                            message = str(retry_exc)
                    # Player-client blocks need a different client, not more retries.
                    break
                if "403" not in message and "Forbidden" not in message:
                    raise
                # Keep retrying 403 a few times before the client fallback.

        if filepath is None:
            report("Обход блокировки…")
            # Public videos: cookieless DASH client + loose format. Cookies often break formats.
            fallback = _with_youtube_preferred_clients(_strip_youtube_cookies(dict(opts)))
            fallback.pop("impersonate", None)
            fallback["format"] = fallback_format_selector(
                audio_only=audio_only, quality=quality, url=url
            )
            if site == "youtube" and youtube_used_cookies:
                # Already tried with cookies; stay cookieless.
                pass
            elif site == "youtube" and last_error is not None and _youtube_needs_cookies(str(last_error)):
                if youtube_cookie_file is not None:
                    _apply_youtube_cookies(fallback, youtube_cookie_file)
                    fallback = _with_youtube_authed_clients(fallback)
            try:
                filepath, info = _try_download(
                    url,
                    fallback,
                    report,
                    audio_only=audio_only,
                    audio_format=audio_format,
                    cancel_event=cancel_event,
                )
            except DownloadCancelled:
                raise
            except Exception as exc:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("Загрузка отменена") from None
                if site == "youtube" and fallback.get("cookiefile"):
                    # One more try without cookies for public clips.
                    report("Повтор без cookies…")
                    bare = _with_youtube_preferred_clients(_strip_youtube_cookies(dict(fallback)))
                    bare["format"] = fallback_format_selector(
                        audio_only=audio_only, quality=quality, url=url
                    )
                    try:
                        filepath, info = _try_download(
                            url,
                            bare,
                            report,
                            audio_only=audio_only,
                            audio_format=audio_format,
                            cancel_event=cancel_event,
                        )
                    except DownloadCancelled:
                        raise
                    except Exception:
                        _reraise_youtube_error(last_error or exc)
                elif site == "youtube":
                    _reraise_youtube_error(last_error or exc)
                else:
                    assert last_error is not None
                    raise last_error from None
                if filepath is None:
                    assert last_error is not None
                    raise last_error from None

        report("Проверка результата…")
        if audio_only:
            if audio_format == "mp3":
                if filepath.suffix.lower() != ".mp3":
                    mp3_path = filepath.with_suffix(".mp3")
                    if mp3_path.exists() and mp3_path != filepath:
                        filepath = mp3_path
                    else:
                        report("Перекодирование AAC → MP3…")
                        filepath = transcode_to_mp3(filepath, cancel_event)
            elif filepath.suffix.lower() not in {".m4a", ".mp3", ".aac"}:
                m4a_path = filepath.with_suffix(".m4a")
                if m4a_path.exists():
                    filepath = m4a_path
        elif filepath.suffix.lower() != ".mp4":
            mp4_path = filepath.with_suffix(".mp4")
            if mp4_path.exists():
                filepath = mp4_path

        cleanup.track(filepath)
        # Cover + tags (description, author URL) for audio; cover for video.
        if filepath.suffix.lower() in {".mp4", ".m4a", ".mkv", ".webm", ".mp3", ".aac"}:
            if audio_only:
                report("Превью и метаданные…")
                filepath = embed_thumbnail(
                    filepath, cancel_event=cancel_event, info=info
                )
            else:
                report("Встраивание превью…")
                filepath = embed_thumbnail(filepath, cancel_event=cancel_event)
        return filepath
    except DownloadCancelled:
        cleanup.purge()
        raise
