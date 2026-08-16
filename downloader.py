"""YouTube video downloader powered by yt-dlp."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import yt_dlp


ProgressCallback = Callable[[dict], None]
StatusCallback = Callable[[str], None]


YOUTUBE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)",
    re.IGNORECASE,
)

_IMPERSONATE_TARGET = None


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_PATTERN.match(url.strip()))


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
    temp_out = video_path.with_name(video_path.stem + ".thumb.tmp.mp4")
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


def build_ydl_opts(
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    opts: dict = {
        "outtmpl": str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        # Prefer H.264 + AAC for quality, Windows thumbnails and player compatibility.
        "format": "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba[ext=m4a]/bv*+ba/b",
        "merge_output_format": "mp4",
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
        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
        ],
    }

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
        return ydl.extract_info(url.strip(), download=False)


def _resolve_output_path(ydl: yt_dlp.YoutubeDL, info: dict) -> Path:
    filepath = Path(ydl.prepare_filename(info))
    if filepath.suffix.lower() != ".mp4":
        mp4_path = filepath.with_suffix(".mp4")
        if mp4_path.exists():
            return mp4_path
    return filepath


def _try_download(
    url: str,
    opts: dict,
    report: Callable[[str], None] | None = None,
) -> Path:
    if report is not None:
        report("Получение информации о видео…")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Не удалось получить информацию о видео.")
        return _resolve_output_path(ydl, info)


def download_video(
    url: str,
    output_dir: Path,
    progress_hook: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> Path:
    url = url.strip()
    if not is_youtube_url(url):
        raise ValueError("Укажите корректную ссылку на YouTube (видео или Shorts).")

    def report(message: str) -> None:
        if status_callback is not None:
            status_callback(message)

    report("Подключение к YouTube…")
    opts = build_ydl_opts(output_dir, progress_hook, status_callback)

    last_error: Exception | None = None
    filepath: Path | None = None
    for attempt in range(1, 4):
        try:
            if attempt > 1:
                report(f"Повтор скачивания ({attempt}/3)…")
                time.sleep(1.5 * attempt)
            filepath = _try_download(url, opts, report)
            break
        except yt_dlp.utils.DownloadError as exc:
            last_error = exc
            message = str(exc)
            if "403" not in message and "Forbidden" not in message:
                raise

    if filepath is None:
        report("Обход блокировки YouTube…")
        fallback = dict(opts)
        fallback["format"] = "best[ext=mp4]/best"
        fallback["extractor_args"] = {
            "youtube": {"player_client": ["android", "android_sdkless"]},
        }
        try:
            filepath = _try_download(url, fallback, report)
        except Exception:
            assert last_error is not None
            raise last_error from None

    report("Проверка результата…")
    if filepath.suffix.lower() != ".mp4":
        mp4_path = filepath.with_suffix(".mp4")
        if mp4_path.exists():
            filepath = mp4_path

    report("Встраивание превью…")
    filepath = embed_thumbnail(filepath)
    return filepath
