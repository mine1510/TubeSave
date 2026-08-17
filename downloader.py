"""Media downloader powered by yt-dlp (YouTube, VK, Iwara, PornHub, Rule34, …)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
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
            r"^https?://(?:(?:www|m)\.)?(?:vk\.com|vkvideo\.ru)/",
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
            r"^https?://(?:[\w-]+\.)?pornhub\.com/",
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
    "vkvideo.ru / vk.com (видео и клипы)\n"
    "music.yandex.ru\n"
    "rule34.xxx / rule34video.com\n"
    "iwara.tv\n"
    "pornhub.com"
)

YANDEX_TRACK_RE = re.compile(
    r"music\.yandex\.(?:ru|com|by|kz|ua)/(?:album/(?P<album>\d+)/)?track/(?P<track>\d+)",
    re.IGNORECASE,
)
YANDEX_API = "https://api.music.yandex.net"
YANDEX_SIGN_KEY = "p93jhgh689SBReK6ghtw62"
YANDEX_MD5_SALT = "XGRlBW9FXlekgbPrRHuSiA"

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
    if host.endswith("vk.com") or host.endswith("vkvideo.ru"):
        return "vk"
    if "music.yandex." in host:
        return "yandexmusic"
    if host.endswith("rule34.xxx") or host.endswith("rule34video.com"):
        return "rule34"
    if host.endswith("iwara.tv"):
        return "iwara"
    if host.endswith("pornhub.com"):
        return "pornhub"
    return None


def is_supported_url(url: str) -> bool:
    return detect_site(url) is not None


def is_youtube_url(url: str) -> bool:
    """Backward-compatible alias."""
    return detect_site(url) == "youtube"


def site_label(url: str) -> str:
    site = detect_site(url)
    return SITE_LABELS.get(site or "", "Видео")


def get_ffmpeg_location() -> str:
    import imageio_ffmpeg

    # Full path to the binary (imageio names it ffmpeg-win-*.exe, not ffmpeg.exe)
    return imageio_ffmpeg.get_ffmpeg_exe()


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
    probe = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, **_ydl_storage_opts()})
    available = [target for target, _source in probe._get_available_impersonate_targets()]
    for target in preferred:
        if any(target in item or item in target for item in available):
            _IMPERSONATE_TARGET = target
            return target
    _IMPERSONATE_TARGET = available[0] if available else None
    return _IMPERSONATE_TARGET


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


def embed_thumbnail(
    video_path: Path,
    thumb_path: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Embed cover art so players/Explorer can show a preview."""
    thumb = thumb_path or _find_sidecar_thumbnail(video_path)
    if thumb is None or not video_path.exists():
        return video_path

    _check_cancel(cancel_event)
    ffmpeg = get_ffmpeg_location()
    temp_out = video_path.with_name(video_path.stem + ".thumb.tmp" + video_path.suffix)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(thumb),
        "-map",
        "0",
        "-map",
        "1",
        "-c",
        "copy",
        "-c:v:1",
        "mjpeg",
        "-disposition:v:1",
        "attached_pic",
        str(temp_out),
    ]
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
        video_path.unlink(missing_ok=True)
        temp_out.rename(video_path)
    else:
        temp_out.unlink(missing_ok=True)

    for leftover in video_path.parent.glob(video_path.stem + ".*"):
        if leftover.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            leftover.unlink(missing_ok=True)
    if thumb.exists() and thumb.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        if thumb.parent == video_path.parent:
            thumb.unlink(missing_ok=True)

    return video_path


def format_selector(
    *,
    audio_only: bool = False,
    quality: str = "best",
    site: str | None = None,
) -> str:
    """Build yt-dlp format string for video quality or audio-only."""
    if audio_only:
        return "ba[ext=m4a]/ba[acodec^=mp4a]/ba/b"

    quality = (quality or "best").strip().lower()
    prefer_avc = site in {None, "youtube"}

    def with_height(height: int) -> str:
        if prefer_avc:
            return (
                f"bv*[height<=?{height}][vcodec^=avc1]+ba[ext=m4a]/"
                f"bv*[height<=?{height}]+ba[ext=m4a]/"
                f"bv*[height<=?{height}]+ba/"
                f"best[height<=?{height}][ext=mp4]/best[height<=?{height}]/"
                f"bv*+ba/b"
            )
        return (
            f"bv*[height<=?{height}]+ba/"
            f"best[height<=?{height}][ext=mp4]/best[height<=?{height}]/"
            f"bv*+ba/b"
        )

    if quality in {"best", "max", "highest"}:
        if prefer_avc:
            return "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba[ext=m4a]/bv*+ba/b"
        return "bv*+ba/b"

    try:
        height = int(quality.rstrip("p"))
    except ValueError:
        return "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba[ext=m4a]/bv*+ba/b" if prefer_avc else "bv*+ba/b"

    return with_height(height)


def fallback_format_selector(
    *,
    audio_only: bool = False,
    quality: str = "best",
) -> str:
    if audio_only:
        return "ba/b"
    quality = (quality or "best").strip().lower()
    if quality in {"best", "max", "highest"}:
        return "best[ext=mp4]/best"
    try:
        height = int(quality.rstrip("p"))
    except ValueError:
        return "best[ext=mp4]/best"
    return f"best[height<=?{height}][ext=mp4]/best[ext=mp4]/best"


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


def build_ydl_opts(
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
    site: str | None = None,
    cancel_event: Event | None = None,
    cleanup: DownloadCleanup | None = None,
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
        "format": format_selector(audio_only=audio_only, quality=quality, site=site),
        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
        ],
    }

    if audio_only:
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            }
        )
    else:
        opts["merge_output_format"] = "mp4"

    # Browser impersonation helps YouTube CDN; also fine for most other sites.
    impersonate = get_impersonate_target()
    if impersonate is not None:
        opts["impersonate"] = impersonate

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
                    status_callback("Извлечение аудио…")
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

    return opts


def fetch_video_info(url: str, cancel_event: Event | None = None) -> dict:
    url = url.strip()
    if detect_site(url) is None:
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
    impersonate = get_impersonate_target()
    if impersonate is not None:
        opts["impersonate"] = impersonate
    if cancel_event is not None:
        opts["progress_hooks"] = [lambda data: _abort_if_cancelled(data, cancel_event)]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    _check_cancel(cancel_event)
    if info is None:
        raise RuntimeError("Не удалось получить информацию о видео.")
    return info


def _resolve_output_path(
    ydl: yt_dlp.YoutubeDL,
    info: dict,
    *,
    audio_only: bool = False,
) -> Path:
    filepath = Path(ydl.prepare_filename(info))
    if audio_only:
        for ext in (".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav"):
            candidate = filepath.with_suffix(ext)
            if candidate.exists():
                return candidate
        # After extract, original media may be deleted; search by id/title stem.
        stem = filepath.with_suffix("").name
        matches = sorted(filepath.parent.glob(stem + ".*"))
        audio_matches = [
            path
            for path in matches
            if path.suffix.lower() in {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav"}
        ]
        if audio_matches:
            return audio_matches[0]
        return filepath

    if filepath.suffix.lower() != ".mp4":
        mp4_path = filepath.with_suffix(".mp4")
        if mp4_path.exists():
            return mp4_path
    return filepath


def _try_download(
    url: str,
    opts: dict,
    report: Callable[[str], None] | None = None,
    *,
    audio_only: bool = False,
    cancel_event: Event | None = None,
) -> Path:
    _check_cancel(cancel_event)
    if report is not None:
        report("Получение информации о видео…")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        _check_cancel(cancel_event)
        if info is None:
            raise RuntimeError("Не удалось получить информацию о видео.")
        return _resolve_output_path(ydl, info, audio_only=audio_only)


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", str(text or "")).strip(" .")
    return (cleaned[:180] or "track")


def _yandex_headers(page_url: str, cookies: str = "") -> dict[str, str]:
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
    if cookies:
        headers["Cookie"] = cookies
    return headers


def _http_read(url: str, headers: dict[str, str], timeout: float = 25.0) -> bytes:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_json(url: str, headers: dict[str, str]) -> dict:
    raw = _http_read(url, headers)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _yandex_sign(message: str) -> str:
    digest = hmac.new(YANDEX_SIGN_KEY.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")[:-1]


def parse_yandex_track_id(url: str) -> str | None:
    match = YANDEX_TRACK_RE.search(url or "")
    if match:
        return match.group("track")
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


def download_yandex_music(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    cookies: str = "",
    cancel_event: Event | None = None,
) -> Path:
    track_id = parse_yandex_track_id(url)
    if not track_id:
        raise ValueError("Нужна ссылка на трек Яндекс.Музыки (…/track/123).")

    def report(message: str) -> None:
        _check_cancel(cancel_event)
        if status_callback is not None:
            status_callback(message)

    headers = _yandex_headers(url, cookies)
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
        if cover:
            if not cover.startswith("http"):
                cover = "https://" + cover.replace("%%", "400x400")
            else:
                cover = cover.replace("%%", "400x400")
            thumb = dest.with_suffix(".jpg")
            cleanup.track(thumb)
            try:
                report("Встраивание превью…")
                _download_binary(cover, thumb, headers, None, cancel_event)
                embed_thumbnail(dest, thumb, cancel_event)
            except DownloadCancelled:
                raise
            except Exception:
                thumb.unlink(missing_ok=True)
        return dest
    except DownloadCancelled:
        cleanup.purge()
        raise


def download_video(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
    cookies: str = "",
    cancel_event: Event | None = None,
) -> Path:
    url = url.strip()
    site = detect_site(url)
    if site is None:
        raise ValueError(
            "Неподдерживаемая ссылка. Доступны:\n" + SUPPORTED_SITES_HINT
        )
    # Yandex Music tracks are audio — always extract M4A/MP3.
    if site == "yandexmusic":
        return download_yandex_music(
            url,
            output_dir,
            progress_hook,
            status_callback,
            cookies=cookies,
            cancel_event=cancel_event,
        )

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
            site=site,
            cancel_event=cancel_event,
            cleanup=cleanup,
        )

        last_error: Exception | None = None
        filepath: Path | None = None
        for attempt in range(1, 4):
            try:
                if attempt > 1:
                    report(f"Повтор скачивания ({attempt}/3)…")
                    _interruptible_sleep(1.5 * attempt, cancel_event)
                filepath = _try_download(
                    url, opts, report, audio_only=audio_only, cancel_event=cancel_event
                )
                break
            except DownloadCancelled:
                raise
            except yt_dlp.utils.DownloadError as exc:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("Загрузка отменена") from None
                last_error = exc
                message = str(exc)
                # Retry only for transient CDN blocks; other errors fail fast.
                if "403" not in message and "Forbidden" not in message:
                    raise

        if filepath is None:
            report("Обход блокировки…")
            fallback = dict(opts)
            fallback["format"] = fallback_format_selector(audio_only=audio_only, quality=quality)
            if site == "youtube":
                fallback["extractor_args"] = {
                    "youtube": {"player_client": ["android", "android_sdkless"]},
                }
            try:
                filepath = _try_download(
                    url, fallback, report, audio_only=audio_only, cancel_event=cancel_event
                )
            except DownloadCancelled:
                raise
            except Exception:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("Загрузка отменена") from None
                assert last_error is not None
                raise last_error from None

        report("Проверка результата…")
        if audio_only:
            if filepath.suffix.lower() not in {".m4a", ".mp3", ".aac"}:
                m4a_path = filepath.with_suffix(".m4a")
                if m4a_path.exists():
                    filepath = m4a_path
        elif filepath.suffix.lower() != ".mp4":
            mp4_path = filepath.with_suffix(".mp4")
            if mp4_path.exists():
                filepath = mp4_path

        cleanup.track(filepath)
        # Cover embed is mainly useful for video/audio containers.
        if filepath.suffix.lower() in {".mp4", ".m4a", ".mkv", ".webm", ".mp3"}:
            report("Встраивание превью…")
            filepath = embed_thumbnail(filepath, cancel_event=cancel_event)
        return filepath
    except DownloadCancelled:
        cleanup.purge()
        raise
