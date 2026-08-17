"""Media downloader powered by yt-dlp (YouTube, VK, Iwara, PornHub, Rule34, …)."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yt_dlp


ProgressCallback = Callable[[dict], None]
StatusCallback = Callable[[str], None]


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
    probe = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
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


def embed_thumbnail(video_path: Path, thumb_path: Path | None = None) -> Path:
    """Embed cover art so players/Explorer can show a preview."""
    thumb = thumb_path or _find_sidecar_thumbnail(video_path)
    if thumb is None or not video_path.exists():
        return video_path

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
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 0:
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


def build_ydl_opts(
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
    site: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    opts: dict = {
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

    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]

    if status_callback is not None:

        def postprocessor_hook(data: dict) -> None:
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

        opts["postprocessor_hooks"] = [postprocessor_hook]

    return opts


def fetch_video_info(url: str) -> dict:
    url = url.strip()
    if detect_site(url) is None:
        raise ValueError(
            "Неподдерживаемая ссылка. Доступны:\n" + SUPPORTED_SITES_HINT
        )
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }
    impersonate = get_impersonate_target()
    if impersonate is not None:
        opts["impersonate"] = impersonate
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


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
) -> Path:
    if report is not None:
        report("Получение информации о видео…")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Не удалось получить информацию о видео.")
        return _resolve_output_path(ydl, info, audio_only=audio_only)


def download_video(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    *,
    audio_only: bool = False,
    quality: str = "best",
) -> Path:
    url = url.strip()
    site = detect_site(url)
    if site is None:
        raise ValueError(
            "Неподдерживаемая ссылка. Доступны:\n" + SUPPORTED_SITES_HINT
        )
    # Yandex Music tracks are audio — always extract M4A/MP3.
    if site == "yandexmusic":
        audio_only = True

    def report(message: str) -> None:
        if status_callback is not None:
            status_callback(message)

    label = SITE_LABELS.get(site, "сайт")
    report(f"Подключение к {label}…")
    opts = build_ydl_opts(
        output_dir,
        progress_hook,
        status_callback,
        audio_only=audio_only,
        quality=quality,
        site=site,
    )

    last_error: Exception | None = None
    filepath: Path | None = None
    for attempt in range(1, 4):
        try:
            if attempt > 1:
                report(f"Повтор скачивания ({attempt}/3)…")
                time.sleep(1.5 * attempt)
            filepath = _try_download(url, opts, report, audio_only=audio_only)
            break
        except yt_dlp.utils.DownloadError as exc:
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
            filepath = _try_download(url, fallback, report, audio_only=audio_only)
        except Exception:
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

    # Cover embed is mainly useful for video/audio containers.
    if filepath.suffix.lower() in {".mp4", ".m4a", ".mkv", ".webm", ".mp3"}:
        report("Встраивание превью…")
        filepath = embed_thumbnail(filepath)
    return filepath
