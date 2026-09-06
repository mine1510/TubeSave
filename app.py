"""Modern minimal GUI for TubeSave — multi-site video/audio downloader."""

from __future__ import annotations

import boot_clean

if boot_clean.ensure_fresh_extract():
    raise SystemExit(0)

import contextlib
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

from downloader import (
    SUPPORTED_SITES_HINT,
    DownloadCancelled,
    download_video,
    extract_media_urls,
    fetch_video_info,
    is_supported_url,
    normalize_audio_format,
    normalize_time_range,
    parse_timestamp,
    short_media_label,
    site_label,
    time_range_label,
    timestamp_from_url,
)
from bridge import (
    BRIDGE_PORT,
    acquire_instance_lock,
    collect_launch_urls,
    extension_dir,
    is_bridge_alive,
    is_update_launch,
    prepare_user_data,
    register_native_host,
    register_protocol,
    run_native_host,
    start_bridge,
    try_apply_updates,
    try_handoff,
    try_focus,
)
from updater import (
    fetch_update_info,
    install_app_update,
    install_extension_update,
)
from version import APP_VERSION, EXTENSION_VERSION


def resolve_app_asset(*parts: str) -> Path | None:
    """Find a bundled icon/asset in the frozen extract or the source tree."""
    rel = Path(*parts)
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / rel,
        Path(getattr(sys, "_MEIPASS", "")) / "assets" / rel.name,
        Path(__file__).resolve().parent / rel,
        Path(__file__).resolve().parent / "assets" / rel.name,
        Path(__file__).resolve().parent / "browser-extension" / "icons" / rel.name,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def notify_windows(title: str, message: str) -> None:
    """Show a Windows toast notification (no blocking dialog)."""
    body = " ".join(str(message).split())
    if len(body) > 220:
        body = body[:217] + "…"

    try:
        from winotify import Notification

        toast = Notification(
            app_id="TubeSave",
            title=title,
            msg=body,
            duration="short",
        )
        toast.show()
        return
    except Exception:
        pass

    # Fallback via PowerShell WinRT toast
    import subprocess

    def xml_escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    title_xml = xml_escape(title)[:80]
    body_xml = xml_escape(body)[:220]
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title_xml}</text>
      <text>{body_xml}</text>
    </binding>
  </visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("TubeSave").Show($toast)
"""
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def settings_path() -> Path:
    return Path.home() / "AppData" / "Roaming" / "TubeSave" / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


LIGHT = {
    "bg": "#F7F6F3",
    "surface": "#FFFFFF",
    "border": "#E6E2DA",
    "text": "#1C1B1A",
    "muted": "#6F6A63",
    "accent": "#C45C26",
    "accent_hover": "#A84B1C",
    "accent_soft": "#F3E6DC",
    "track": "#EDE9E2",
    "success": "#2F6B4F",
    "danger": "#A33B2B",
    "ghost": "#F0EDE7",
    "ghost_hover": "#E7E2D8",
}

DARK = {
    "bg": "#161615",
    "surface": "#222220",
    "border": "#33332F",
    "text": "#F3F1EC",
    "muted": "#A39E96",
    "accent": "#D4784A",
    "accent_hover": "#E08A5C",
    "accent_soft": "#2A2A27",
    "track": "#2F2F2C",
    "success": "#5A9A78",
    "danger": "#C96A5C",
    "ghost": "#2A2A27",
    "ghost_hover": "#333330",
}

COLORS: dict[str, str] = dict(LIGHT)

FONTS = {
    "brand": ("Segoe UI Semibold", 22),
    "title": ("Segoe UI Semibold", 12),
    "body": ("Segoe UI", 10),
    "small": ("Segoe UI", 9),
    "mono": ("Consolas", 9),
}

QUALITY_OPTIONS: list[tuple[str, str]] = [
    ("best", "Лучшее"),
    ("2160", "4K"),
    ("1440", "1440p"),
    ("1080", "1080p"),
    ("720", "720p"),
    ("480", "480p"),
    ("360", "360p"),
]
QUALITY_CODES = {code for code, _label in QUALITY_OPTIONS}

AUDIO_FORMAT_OPTIONS: list[tuple[str, str]] = [
    ("aac", "AAC"),
    ("mp3", "MP3"),
]
AUDIO_FORMAT_CODES = {code for code, _label in AUDIO_FORMAT_OPTIONS}

QUEUE_KEEP_FINISHED = 30


@dataclass
class QueueJob:
    id: int
    url: str
    audio_only: bool
    quality: str
    audio_format: str
    folder: Path
    cookies: str = ""
    quiet: bool = False
    start_time: float | None = None
    end_time: float | None = None
    title: str = ""
    status: str = "pending"  # pending | running | done | error | cancelled
    message: str = ""
    kind: str = "MP4"

    def __post_init__(self) -> None:
        if not self.title:
            self.title = short_media_label(self.url)
        if self.audio_only:
            self.kind = "MP3" if self.audio_format == "mp3" else "AAC"
        else:
            self.kind = "MP4"

    def quality_label(self) -> str:
        if self.audio_only:
            return self.kind
        return next(
            (label for code, label in QUALITY_OPTIONS if code == self.quality),
            self.quality,
        )

    def trim_label(self) -> str:
        return time_range_label(self.start_time, self.end_time)

    def status_label(self) -> str:
        return {
            "pending": "В очереди",
            "running": "Скачивается",
            "done": "Готово",
            "error": "Ошибка",
            "cancelled": "Отменено",
        }.get(self.status, self.status)


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def format_bytes(num: float | None) -> str:
    if not num:
        return "—"
    units = ["Б", "КБ", "МБ", "ГБ"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "—"


class ProgressBar(tk.Canvas):
    def __init__(self, master: tk.Misc, height: int = 8, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            highlightthickness=0,
            bg=COLORS["surface"],
            **kwargs,
        )
        self._value = 0.0
        self._indeterminate = False
        self._pulse = 0.0
        self._animating = False
        self.bind("<Configure>", lambda _e: self._draw())

    def set_value(self, value: float) -> None:
        self._indeterminate = False
        self._value = max(0.0, min(100.0, value))
        self._draw()

    def start_indeterminate(self) -> None:
        self._indeterminate = True
        if not self._animating:
            self._animating = True
            self._animate()

    def stop(self) -> None:
        self._indeterminate = False
        self._animating = False
        self._draw()

    def _animate(self) -> None:
        if not self._indeterminate:
            self._animating = False
            return
        self._pulse = (self._pulse + 2.4) % 100
        self._draw()
        self.after(30, self._animate)

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        radius = height / 2
        self._rounded_rect(0, 0, width, height, radius, COLORS["track"])
        if self._indeterminate:
            bar_w = width * 0.28
            x = (self._pulse / 100) * (width + bar_w) - bar_w
            self._rounded_rect(x, 0, x + bar_w, height, radius, COLORS["accent"])
        elif self._value > 0:
            fill_w = max(height, width * self._value / 100)
            self._rounded_rect(0, 0, fill_w, height, radius, COLORS["accent"])

    def _rounded_rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        color: str,
    ) -> None:
        if x2 <= x1:
            return
        r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        self.create_oval(x1, y1, x1 + 2 * r, y2, fill=color, outline="")
        self.create_oval(x2 - 2 * r, y1, x2, y2, fill=color, outline="")
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline="")


class PillButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command,
        *,
        primary: bool = False,
        danger: bool = False,
        width: int = 128,
        height: int = 40,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bg=COLORS["bg"],
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._primary = primary
        self._danger = danger
        self._enabled = True
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _bg(self) -> str:
        if not self._enabled:
            return COLORS["ghost"]
        if self._danger:
            return COLORS["danger"]
        if self._primary:
            return COLORS["accent_hover"] if self._hover else COLORS["accent"]
        return COLORS["ghost_hover"] if self._hover else COLORS["ghost"]

    def _fg(self) -> str:
        if not self._enabled:
            return COLORS["muted"]
        if self._primary or self._danger:
            return "#FFFFFF"
        return COLORS["text"]

    def _draw(self) -> None:
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        r = h / 2
        color = self._bg()
        self.create_oval(0, 0, h, h, fill=color, outline="")
        self.create_oval(w - h, 0, w, h, fill=color, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=color, outline="")
        self.create_text(
            w / 2,
            h / 2,
            text=self._text,
            fill=self._fg(),
            font=FONTS["body"],
        )

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_click(self, _event=None) -> None:
        if self._enabled and self._command:
            self._command()


class QualityChip(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        code: str,
        label: str,
        command,
        *,
        width: int = 78,
        height: int = 32,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bg=COLORS["surface"],
            cursor="hand2",
        )
        self.code = code
        self._label = label
        self._command = command
        self._selected = False
        self._hover = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._command(self.code))
        self._draw()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        r = h / 2
        if self._selected:
            color = COLORS["accent"]
            fg = "#FFFFFF"
        elif self._hover:
            color = COLORS["ghost_hover"]
            fg = COLORS["text"]
        else:
            color = COLORS["ghost"]
            fg = COLORS["text"]
        self.create_oval(0, 0, h, h, fill=color, outline="")
        self.create_oval(w - h, 0, w, h, fill=color, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=color, outline="")
        self.create_text(w / 2, h / 2, text=self._label, fill=fg, font=FONTS["small"])


class ThemeToggle(tk.Canvas):
    """Compact pill switch for light/dark theme."""

    def __init__(self, master: tk.Misc, command, *, width: int = 44, height: int = 24) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bg=COLORS["bg"],
            cursor="hand2",
        )
        self._command = command
        self._on = False
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def set_on(self, on: bool) -> None:
        self._on = on
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        pad = 2
        track = COLORS["accent"] if self._on else COLORS["track"]
        self.create_oval(0, 0, h, h, fill=track, outline="")
        self.create_oval(w - h, 0, w, h, fill=track, outline="")
        self.create_rectangle(h / 2, 0, w - h / 2, h, fill=track, outline="")
        knob_r = (h - pad * 2) / 2
        cx = (w - h / 2) if self._on else (h / 2)
        cy = h / 2
        knob = COLORS["surface"] if self._on else COLORS["muted"]
        self.create_oval(
            cx - knob_r,
            cy - knob_r,
            cx + knob_r,
            cy + knob_r,
            fill=knob,
            outline="",
        )

    def _on_click(self, _event=None) -> None:
        if self._command:
            self._command()


class Card(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(
            master,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            **kwargs,
        )


class YouTubeDownloaderApp(tk.Tk):
    def __init__(self, apply_update_on_start: bool = False, start_hidden: bool = False) -> None:
        super().__init__()
        self._apply_update_on_start = apply_update_on_start
        self._start_hidden = start_hidden
        if start_hidden:
            self.withdraw()
        self.title(f"TubeSave {APP_VERSION}")
        self.resizable(True, True)

        default_dir = Path.home() / "Downloads" / "TubeSave"
        self._settings = load_settings()
        saved_dir = str(self._settings.get("download_dir") or "").strip()
        self.download_dir = Path(saved_dir) if saved_dir else default_dir
        theme = str(self._settings.get("theme") or "light").lower()
        self._theme = "dark" if theme == "dark" else "light"
        saved_quality = str(self._settings.get("quality") or "best").strip().lower()
        self._quality = saved_quality if saved_quality in QUALITY_CODES else "best"
        saved_audio = str(self._settings.get("audio_format") or "aac").strip().lower()
        self._audio_format = normalize_audio_format(saved_audio)
        COLORS.clear()
        COLORS.update(DARK if self._theme == "dark" else LIGHT)

        self.configure(bg=COLORS["bg"])
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._is_busy = False
        self._cancel_event = threading.Event()
        self._quiet_download = False
        self._last_external: tuple[str, float] | None = None
        self._started_at: float | None = None
        self._timer_job: str | None = None
        self._current_stage = "Ожидание"

        self._bg_frames: list[tk.Misc] = []
        self._surface_frames: list[tk.Misc] = []
        self._cards: list[Card] = []
        self._bg_text_labels: list[tk.Label] = []
        self._bg_muted_labels: list[tk.Label] = []
        self._surface_text_labels: list[tk.Label] = []
        self._surface_muted_labels: list[tk.Label] = []
        self._entries: list[tk.Entry] = []
        self._pill_buttons: list[tuple[PillButton, str]] = []
        self._quality_chips: list[QualityChip] = []
        self._audio_format_chips: list[QualityChip] = []
        self._context_menus: list[tk.Menu] = []
        self._bg_image_labels: list[tk.Label] = []
        self._is_downloading = False
        self._update_deferred_logged = False
        self._queue_jobs: list[QueueJob] = []
        self._queue_seq = 0
        self._active_job: QueueJob | None = None
        self._queue_rows: list[tk.Frame] = []

        self._set_window_icon()
        self._build_ui()
        self._sync_audio_button()
        self._apply_theme()
        self._fit_window()
        self.after(80, self._process_events)
        self._bridge = start_bridge(
            self._on_bridge_url,
            self._on_bridge_focus,
            on_check_update=self._bridge_check_update,
            on_update_extension=self._bridge_update_extension,
            on_update_app=self._bridge_update_app,
            on_apply_updates=self._bridge_apply_updates,
        )
        register_protocol()
        register_native_host()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tray = None
        self._tray_thread = None
        self._tray_notified = bool(self._settings.get("tray_hint_shown"))
        self._update_info = None
        self._update_applying = False
        self._ensure_tray()
        delay = 800 if getattr(self, "_apply_update_on_start", False) else 2500
        self.after(delay, self._check_updates_silent)
        if start_hidden:
            self._ensure_tray()
            self.withdraw()


    def _persist_folder(self, folder: str | Path) -> None:
        folder_str = str(folder).strip()
        if not folder_str:
            return
        self.download_dir = Path(folder_str)
        self._settings["download_dir"] = folder_str
        save_settings(self._settings)

    def _persist_theme(self) -> None:
        self._settings["theme"] = self._theme
        save_settings(self._settings)

    def _persist_quality(self) -> None:
        self._settings["quality"] = self._quality
        save_settings(self._settings)

    def _persist_audio_format(self) -> None:
        self._settings["audio_format"] = self._audio_format
        save_settings(self._settings)

    def _set_window_icon(self) -> None:
        ico = resolve_app_asset("assets", "tubesave.ico")
        png = resolve_app_asset("assets", "tubesave-32.png")
        if ico is not None:
            with contextlib.suppress(tk.TclError, OSError):
                self.iconbitmap(str(ico))
        if png is not None:
            with contextlib.suppress(tk.TclError, OSError):
                self._window_icon = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._window_icon)

    def _fit_window(self) -> None:
        """Size window so action buttons are always visible on first open."""
        self.update_idletasks()
        width = max(900, self.winfo_reqwidth())
        height = max(780, self.winfo_reqheight() + 24)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(width, max(640, screen_w - 80))
        height = min(height, max(560, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.minsize(880, 760)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _register_pill(self, button: PillButton, parent_key: str = "bg") -> PillButton:
        self._pill_buttons.append((button, parent_key))
        return button

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=COLORS["bg"])
        root.pack(fill="both", expand=True, padx=24, pady=20)
        self._bg_frames.append(root)

        # Actions first (side=bottom) so they never get clipped by expanding content
        actions = tk.Frame(root, bg=COLORS["bg"])
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        self._bg_frames.append(actions)

        self.info_btn = self._register_pill(
            PillButton(actions, "Проверить", self._fetch_info, width=120)
        )
        self.info_btn.pack(side="left")

        self.download_btn = self._register_pill(
            PillButton(actions, "Скачать", self._start_download, primary=True, width=120)
        )
        self.download_btn.pack(side="left", padx=(10, 0))

        self.audio_btn = self._register_pill(
            PillButton(
                actions,
                "Аудио AAC",
                lambda: self._start_download(audio_only=True),
                width=130,
            )
        )
        self.audio_btn.pack(side="left", padx=(10, 0))

        self.cancel_btn = self._register_pill(
            PillButton(
                actions,
                "Отмена",
                self._cancel_download,
                danger=True,
                width=110,
            )
        )
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.cancel_btn.set_enabled(False)

        exit_btn = self._register_pill(PillButton(actions, "Выход", self._quit_app, width=100))
        exit_btn.pack(side="right")

        browser_btn = self._register_pill(
            PillButton(actions, "Браузер", self._show_browser_help, width=100)
        )
        browser_btn.pack(side="right", padx=(0, 10))

        # Scrollable-feeling content above buttons
        content = tk.Frame(root, bg=COLORS["bg"])
        content.pack(side="top", fill="both", expand=True)
        self._bg_frames.append(content)

        header = tk.Frame(content, bg=COLORS["bg"])
        header.pack(fill="x")
        self._bg_frames.append(header)

        brand_row = tk.Frame(header, bg=COLORS["bg"])
        brand_row.pack(side="left", anchor="w")
        self._bg_frames.append(brand_row)

        logo_path = resolve_app_asset("assets", "tubesave-32.png")
        if logo_path is not None:
            self._brand_logo = tk.PhotoImage(file=str(logo_path))
            logo = tk.Label(brand_row, image=self._brand_logo, bg=COLORS["bg"], bd=0)
            logo.pack(side="left", padx=(0, 10))
            self._bg_image_labels.append(logo)

        brand = tk.Label(
            brand_row,
            text=f"TubeSave  v{APP_VERSION}",
            font=FONTS["brand"],
            fg=COLORS["text"],
            bg=COLORS["bg"],
        )
        brand.pack(side="left", anchor="w")
        self._bg_text_labels.append(brand)

        theme_row = tk.Frame(header, bg=COLORS["bg"])
        theme_row.pack(side="right", anchor="e")
        self._bg_frames.append(theme_row)

        self.theme_label = tk.Label(
            theme_row,
            text="Тёмная тема",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["bg"],
        )
        self.theme_label.pack(side="left", padx=(0, 8))
        self._bg_muted_labels.append(self.theme_label)

        self.theme_toggle = ThemeToggle(theme_row, self._toggle_theme)
        self.theme_toggle.set_on(self._theme == "dark")
        self.theme_toggle.pack(side="left")

        subtitle = tk.Label(
            content,
            text="YouTube · VK · Я.Музыка · Iwara · PornHub · Rule34",
            font=FONTS["body"],
            fg=COLORS["muted"],
            bg=COLORS["bg"],
        )
        subtitle.pack(anchor="w", pady=(2, 14))
        self._bg_muted_labels.append(subtitle)

        # URL card
        url_card = Card(content)
        url_card.pack(fill="x", pady=(0, 10))
        self._cards.append(url_card)
        url_inner = tk.Frame(url_card, bg=COLORS["surface"])
        url_inner.pack(fill="x", padx=16, pady=12)
        self._surface_frames.append(url_inner)

        url_title = tk.Label(
            url_inner,
            text="Ссылка",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        url_title.pack(anchor="w")
        self._surface_text_labels.append(url_title)

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(
            url_inner,
            textvariable=self.url_var,
            font=FONTS["body"],
            bg=COLORS["ghost"],
            fg=COLORS["text"],
            relief="flat",
            insertbackground=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.url_entry.pack(fill="x", pady=(6, 0), ipady=7, ipadx=8)
        self._entries.append(self.url_entry)
        self._bind_clipboard(self.url_entry, flatten_whitespace=True)
        self.url_entry.bind("<Return>", lambda _e: self._start_download())
        self.url_entry.focus_set()

        url_hint = tk.Label(
            url_inner,
            text="Можно вставить несколько ссылок сразу — они встанут в очередь.",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        url_hint.pack(anchor="w", pady=(6, 0))
        self._surface_muted_labels.append(url_hint)

        # Folder card
        folder_card = Card(content)
        folder_card.pack(fill="x", pady=(0, 10))
        self._cards.append(folder_card)
        folder_inner = tk.Frame(folder_card, bg=COLORS["surface"])
        folder_inner.pack(fill="x", padx=16, pady=12)
        self._surface_frames.append(folder_inner)

        folder_title = tk.Label(
            folder_inner,
            text="Папка сохранения",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        folder_title.pack(anchor="w")
        self._surface_text_labels.append(folder_title)

        folder_row = tk.Frame(folder_inner, bg=COLORS["surface"])
        folder_row.pack(fill="x", pady=(6, 0))
        self._surface_frames.append(folder_row)

        self.folder_var = tk.StringVar(value=str(self.download_dir))
        self.folder_entry = tk.Entry(
            folder_row,
            textvariable=self.folder_var,
            font=FONTS["body"],
            bg=COLORS["ghost"],
            fg=COLORS["text"],
            relief="flat",
            insertbackground=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._entries.append(self.folder_entry)
        self._bind_clipboard(self.folder_entry)
        self.folder_entry.bind("<FocusOut>", lambda _e: self._persist_folder(self.folder_var.get()))

        browse = self._register_pill(
            PillButton(folder_row, "Обзор", self._choose_folder, width=100, height=36),
            parent_key="surface",
        )
        browse.pack(side="left", padx=(10, 0))

        open_folder = self._register_pill(
            PillButton(folder_row, "Открыть", self._open_folder, width=100, height=36),
            parent_key="surface",
        )
        open_folder.pack(side="left", padx=(8, 0))

        # Quality card
        quality_card = Card(content)
        quality_card.pack(fill="x", pady=(0, 10))
        self._cards.append(quality_card)
        quality_inner = tk.Frame(quality_card, bg=COLORS["surface"])
        quality_inner.pack(fill="x", padx=16, pady=12)
        self._surface_frames.append(quality_inner)

        quality_title = tk.Label(
            quality_inner,
            text="Качество видео",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        quality_title.pack(anchor="w")
        self._surface_text_labels.append(quality_title)

        quality_hint = tk.Label(
            quality_inner,
            text="Если выбранного разрешения нет, будет взято ближайшее доступное. Несколько ссылок — через пробел.",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        quality_hint.pack(anchor="w", pady=(2, 8))
        self._surface_muted_labels.append(quality_hint)

        quality_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        quality_row.pack(fill="x")
        self._surface_frames.append(quality_row)

        self._quality_chips = []
        for code, label in QUALITY_OPTIONS:
            chip = QualityChip(quality_row, code, label, self._select_quality, width=78 if code != "best" else 86)
            chip.pack(side="left", padx=(0, 8))
            chip.set_selected(code == self._quality)
            self._quality_chips.append(chip)

        audio_title = tk.Label(
            quality_inner,
            text="Формат аудио",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        audio_title.pack(anchor="w", pady=(12, 0))
        self._surface_text_labels.append(audio_title)

        audio_hint = tk.Label(
            quality_inner,
            text="AAC копируется как есть. MP3 перекодируется из AAC — YouTube MP3 не отдаёт.",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        audio_hint.pack(anchor="w", pady=(2, 8))
        self._surface_muted_labels.append(audio_hint)

        audio_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        audio_row.pack(fill="x")
        self._surface_frames.append(audio_row)

        self._audio_format_chips = []
        for code, label in AUDIO_FORMAT_OPTIONS:
            chip = QualityChip(audio_row, code, label, self._select_audio_format, width=78)
            chip.pack(side="left", padx=(0, 8))
            chip.set_selected(code == self._audio_format)
            self._audio_format_chips.append(chip)

        trim_title = tk.Label(
            quality_inner,
            text="Обрезка по времени",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        trim_title.pack(anchor="w", pady=(12, 0))
        self._surface_text_labels.append(trim_title)

        trim_hint = tk.Label(
            quality_inner,
            text="Только в приложении. Пустое «С» — с начала, пустое «По» — до конца. Формат: 1:20, 1:20:05 или 90.",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        trim_hint.pack(anchor="w", pady=(2, 8))
        self._surface_muted_labels.append(trim_hint)

        trim_row = tk.Frame(quality_inner, bg=COLORS["surface"])
        trim_row.pack(fill="x")
        self._surface_frames.append(trim_row)

        trim_from_label = tk.Label(
            trim_row,
            text="С",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        trim_from_label.pack(side="left")
        self._surface_muted_labels.append(trim_from_label)

        self.trim_start_var = tk.StringVar()
        self.trim_start_entry = tk.Entry(
            trim_row,
            textvariable=self.trim_start_var,
            font=FONTS["body"],
            width=10,
            bg=COLORS["ghost"],
            fg=COLORS["text"],
            relief="flat",
            insertbackground=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.trim_start_entry.pack(side="left", padx=(8, 16), ipady=6, ipadx=8)
        self._entries.append(self.trim_start_entry)
        self._bind_clipboard(self.trim_start_entry)

        trim_to_label = tk.Label(
            trim_row,
            text="По",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        trim_to_label.pack(side="left")
        self._surface_muted_labels.append(trim_to_label)

        self.trim_end_var = tk.StringVar()
        self.trim_end_entry = tk.Entry(
            trim_row,
            textvariable=self.trim_end_var,
            font=FONTS["body"],
            width=10,
            bg=COLORS["ghost"],
            fg=COLORS["text"],
            relief="flat",
            insertbackground=COLORS["text"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.trim_end_entry.pack(side="left", padx=(8, 0), ipady=6, ipadx=8)
        self._entries.append(self.trim_end_entry)
        self._bind_clipboard(self.trim_end_entry)

        # Info + progress card
        status_card = Card(content)
        status_card.pack(fill="x", pady=(0, 10))
        self._cards.append(status_card)
        status_inner = tk.Frame(status_card, bg=COLORS["surface"])
        status_inner.pack(fill="x", padx=16, pady=12)
        self._surface_frames.append(status_inner)

        status_title = tk.Label(
            status_inner,
            text="Статус",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        status_title.pack(anchor="w")
        self._surface_text_labels.append(status_title)

        self.info_var = tk.StringVar(value="Вставьте ссылку и нажмите «Скачать».")
        self.info_label = tk.Label(
            status_inner,
            textvariable=self.info_var,
            font=FONTS["body"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
            justify="left",
            wraplength=700,
            anchor="w",
        )
        self.info_label.pack(anchor="w", pady=(6, 10))
        self._surface_muted_labels.append(self.info_label)

        self.progress = ProgressBar(status_inner, height=8)
        self.progress.pack(fill="x")

        metrics = tk.Frame(status_inner, bg=COLORS["surface"])
        metrics.pack(fill="x", pady=(10, 0))
        self._surface_frames.append(metrics)

        self.stage_var = tk.StringVar(value="Этап: ожидание")
        self.percent_var = tk.StringVar(value="0%")
        self.speed_var = tk.StringVar(value="Скорость: —")
        self.eta_var = tk.StringVar(value="ETA: —")
        self.elapsed_var = tk.StringVar(value="Прошло: 0:00")
        self.size_var = tk.StringVar(value="Размер: —")

        for col, var in enumerate(
            (
                self.stage_var,
                self.percent_var,
                self.speed_var,
                self.eta_var,
                self.elapsed_var,
                self.size_var,
            )
        ):
            label = tk.Label(
                metrics,
                textvariable=var,
                font=FONTS["small"],
                fg=COLORS["muted"],
                bg=COLORS["surface"],
                anchor="w",
            )
            label.grid(row=col // 3, column=col % 3, sticky="w", padx=(0, 18), pady=2)
            metrics.grid_columnconfigure(col % 3, weight=1)
            self._surface_muted_labels.append(label)

        self.status_var = tk.StringVar(value="Готово к работе")
        status_label = tk.Label(
            status_inner,
            textvariable=self.status_var,
            font=FONTS["body"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
            wraplength=700,
            justify="left",
            anchor="w",
        )
        status_label.pack(anchor="w", pady=(10, 0))
        self._surface_text_labels.append(status_label)

        # Download queue
        queue_card = Card(content)
        queue_card.pack(fill="x", pady=(0, 10))
        self._cards.append(queue_card)
        queue_inner = tk.Frame(queue_card, bg=COLORS["surface"])
        queue_inner.pack(fill="x", padx=16, pady=12)
        self._surface_frames.append(queue_inner)

        queue_header = tk.Frame(queue_inner, bg=COLORS["surface"])
        queue_header.pack(fill="x")
        self._surface_frames.append(queue_header)

        self.queue_title_var = tk.StringVar(value="Очередь")
        queue_title = tk.Label(
            queue_header,
            textvariable=self.queue_title_var,
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        queue_title.pack(side="left")
        self._surface_text_labels.append(queue_title)

        self.clear_done_btn = self._register_pill(
            PillButton(
                queue_header,
                "Готовые",
                self._clear_finished_jobs,
                width=88,
                height=28,
            ),
            parent_key="surface",
        )
        self.clear_done_btn.pack(side="right")

        self.clear_queue_btn = self._register_pill(
            PillButton(
                queue_header,
                "Очистить",
                self._clear_pending_jobs,
                width=88,
                height=28,
            ),
            parent_key="surface",
        )
        self.clear_queue_btn.pack(side="right", padx=(0, 8))

        queue_hint = tk.Label(
            queue_inner,
            text="Пока качается одно видео, новые ссылки ждут здесь. «Отмена» останавливает только текущее.",
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
        )
        queue_hint.pack(anchor="w", pady=(4, 8))
        self._surface_muted_labels.append(queue_hint)

        queue_list_wrap = tk.Frame(queue_inner, bg=COLORS["surface"])
        queue_list_wrap.pack(fill="x")
        self._surface_frames.append(queue_list_wrap)

        self._queue_canvas = tk.Canvas(
            queue_list_wrap,
            height=128,
            highlightthickness=0,
            bg=COLORS["ghost"],
        )
        self._queue_scroll = tk.Scrollbar(
            queue_list_wrap,
            orient="vertical",
            command=self._queue_canvas.yview,
        )
        self._queue_canvas.configure(yscrollcommand=self._queue_scroll.set)
        self._queue_canvas.pack(side="left", fill="x", expand=True)
        self._queue_scroll.pack(side="right", fill="y")

        self._queue_inner = tk.Frame(self._queue_canvas, bg=COLORS["ghost"])
        self._queue_canvas_window = self._queue_canvas.create_window(
            (0, 0), window=self._queue_inner, anchor="nw"
        )
        self._queue_inner.bind(
            "<Configure>",
            lambda _e: self._queue_canvas.configure(
                scrollregion=self._queue_canvas.bbox("all")
            ),
        )
        self._queue_canvas.bind(
            "<Configure>",
            lambda e: self._queue_canvas.itemconfigure(
                self._queue_canvas_window, width=e.width
            ),
        )
        self._queue_canvas.bind(
            "<MouseWheel>",
            lambda e: self._queue_canvas.yview_scroll(int(-e.delta / 120), "units"),
        )
        self._queue_inner.bind(
            "<MouseWheel>",
            lambda e: self._queue_canvas.yview_scroll(int(-e.delta / 120), "units"),
        )
        self._surface_frames.append(self._queue_inner)

        self.queue_empty_var = tk.StringVar(
            value="Пока пусто. Нажмите «Скачать» или кнопку в браузере."
        )
        self.queue_empty_label = tk.Label(
            self._queue_inner,
            textvariable=self.queue_empty_var,
            font=FONTS["small"],
            fg=COLORS["muted"],
            bg=COLORS["ghost"],
            anchor="w",
            padx=10,
            pady=12,
        )
        self.queue_empty_label.pack(fill="x")

        # Log card — expands, but never pushes buttons off-screen
        log_card = Card(content)
        log_card.pack(fill="both", expand=True)
        self._cards.append(log_card)
        log_inner = tk.Frame(log_card, bg=COLORS["surface"])
        log_inner.pack(fill="both", expand=True, padx=16, pady=12)
        self._surface_frames.append(log_inner)

        log_title = tk.Label(
            log_inner,
            text="Журнал",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        )
        log_title.pack(anchor="w")
        self._surface_text_labels.append(log_title)

        self.log_text = tk.Text(
            log_inner,
            height=5,
            wrap="word",
            font=FONTS["mono"],
            bg=COLORS["ghost"],
            fg=COLORS["text"],
            relief="flat",
            highlightthickness=0,
            padx=10,
            pady=8,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        self._bind_log_clipboard(self.log_text)

    def _bind_log_clipboard(self, widget: tk.Text) -> None:
        def copy_selection(_event: tk.Event | None = None) -> str:
            try:
                text = widget.get("sel.first", "sel.last")
            except tk.TclError:
                return "break"
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
            return "break"

        def copy_all(_event: tk.Event | None = None) -> str:
            was_disabled = str(widget.cget("state")) == "disabled"
            if was_disabled:
                widget.configure(state="normal")
            text = widget.get("1.0", "end-1c")
            if was_disabled:
                widget.configure(state="disabled")
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
            return "break"

        def select_all(_event: tk.Event | None = None) -> str:
            was_disabled = str(widget.cget("state")) == "disabled"
            if was_disabled:
                widget.configure(state="normal")
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
            if was_disabled:
                widget.configure(state="disabled")
            return "break"

        def on_control_key(event: tk.Event) -> str | None:
            keycode = getattr(event, "keycode", None)
            shift = bool(event.state & 0x1)
            if keycode == 67:
                return copy_all(event) if shift else copy_selection(event)
            if keycode == 65:
                return select_all(event)
            return None

        widget.bind("<Control-c>", copy_selection)
        widget.bind("<Control-C>", copy_selection)
        widget.bind("<Control-a>", select_all)
        widget.bind("<Control-A>", select_all)
        widget.bind("<Control-Shift-C>", copy_all)
        widget.bind("<Control-Shift-c>", copy_all)
        widget.bind("<Control-KeyPress>", on_control_key)

        menu = tk.Menu(widget, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"])
        menu.add_command(label="Копировать", command=lambda: copy_selection())
        menu.add_command(label="Копировать всё", command=lambda: copy_all())
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: select_all())
        self._context_menus.append(menu)
        widget.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

    def _apply_theme(self) -> None:
        palette = DARK if self._theme == "dark" else LIGHT
        COLORS.clear()
        COLORS.update(palette)

        self.configure(bg=COLORS["bg"])
        for label in self._bg_image_labels:
            label.configure(bg=COLORS["bg"])
        for frame in self._bg_frames:
            frame.configure(bg=COLORS["bg"])
        for frame in self._surface_frames:
            frame.configure(bg=COLORS["surface"])
        for card in self._cards:
            card.configure(bg=COLORS["surface"], highlightbackground=COLORS["border"])
        for label in self._bg_text_labels:
            label.configure(fg=COLORS["text"], bg=COLORS["bg"])
        for label in self._bg_muted_labels:
            label.configure(fg=COLORS["muted"], bg=COLORS["bg"])
        for label in self._surface_text_labels:
            label.configure(fg=COLORS["text"], bg=COLORS["surface"])
        for label in self._surface_muted_labels:
            if label is self.info_label:
                current = str(label.cget("fg"))
                if current in (LIGHT["text"], DARK["text"]):
                    label.configure(fg=COLORS["text"], bg=COLORS["surface"])
                else:
                    label.configure(fg=COLORS["muted"], bg=COLORS["surface"])
            else:
                label.configure(fg=COLORS["muted"], bg=COLORS["surface"])

        for entry in self._entries:
            entry.configure(
                bg=COLORS["ghost"],
                fg=COLORS["text"],
                insertbackground=COLORS["text"],
                highlightbackground=COLORS["border"],
                highlightcolor=COLORS["accent"],
            )

        self.log_text.configure(bg=COLORS["ghost"], fg=COLORS["text"])
        self._queue_canvas.configure(bg=COLORS["ghost"])
        self._queue_inner.configure(bg=COLORS["ghost"])
        self.queue_empty_label.configure(fg=COLORS["muted"], bg=COLORS["ghost"])

        for menu in self._context_menus:
            menu.configure(bg=COLORS["surface"], fg=COLORS["text"])

        self.theme_toggle.configure(bg=COLORS["bg"])
        self.theme_toggle.set_on(self._theme == "dark")

        self.progress.configure(bg=COLORS["surface"])
        self.progress._draw()

        for button, parent_key in self._pill_buttons:
            button.configure(bg=COLORS[parent_key])
            button._draw()

        for chip in self._quality_chips:
            chip.configure(bg=COLORS["surface"])
            chip.set_selected(chip.code == self._quality)
        for chip in self._audio_format_chips:
            chip.configure(bg=COLORS["surface"])
            chip.set_selected(chip.code == self._audio_format)
        self._refresh_queue_ui()

    def _select_quality(self, code: str) -> None:
        if code not in QUALITY_CODES:
            return
        self._quality = code
        self._persist_quality()
        for chip in self._quality_chips:
            chip.set_selected(chip.code == self._quality)

    def _select_audio_format(self, code: str) -> None:
        code = normalize_audio_format(code)
        if code not in AUDIO_FORMAT_CODES:
            return
        self._audio_format = code
        self._persist_audio_format()
        for chip in self._audio_format_chips:
            chip.set_selected(chip.code == self._audio_format)
        self._sync_audio_button()

    def _sync_audio_button(self) -> None:
        button = getattr(self, "audio_btn", None)
        if button is None:
            return
        button.set_text("Аудио MP3" if self._audio_format == "mp3" else "Аудио AAC")

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self._persist_theme()
        self._apply_theme()

    def _bind_clipboard(self, widget: tk.Entry, *, flatten_whitespace: bool = False) -> None:
        def paste(_event: tk.Event | None = None) -> str:
            try:
                text = self.clipboard_get()
            except tk.TclError:
                return "break"
            if flatten_whitespace:
                text = " ".join(text.replace("\r", "\n").split())
            try:
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            widget.insert(tk.INSERT, text)
            return "break"

        def copy(_event: tk.Event | None = None) -> str:
            if widget.selection_present():
                self.clipboard_clear()
                self.clipboard_append(widget.selection_get())
            return "break"

        def cut(_event: tk.Event | None = None) -> str:
            copy(_event)
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
            return "break"

        def select_all(_event: tk.Event | None = None) -> str:
            widget.select_range(0, tk.END)
            widget.icursor(tk.END)
            return "break"

        def on_control_key(event: tk.Event) -> str | None:
            keycode = getattr(event, "keycode", None)
            if keycode == 86:
                return paste(event)
            if keycode == 67:
                return copy(event)
            if keycode == 88:
                return cut(event)
            if keycode == 65:
                return select_all(event)
            return None

        widget.bind("<<Paste>>", paste)
        widget.bind("<<Copy>>", copy)
        widget.bind("<<Cut>>", cut)
        widget.bind("<Control-v>", paste)
        widget.bind("<Control-V>", paste)
        widget.bind("<Control-Key-v>", paste)
        widget.bind("<Shift-Insert>", paste)
        widget.bind("<Control-c>", copy)
        widget.bind("<Control-C>", copy)
        widget.bind("<Control-x>", cut)
        widget.bind("<Control-X>", cut)
        widget.bind("<Control-a>", select_all)
        widget.bind("<Control-A>", select_all)
        widget.bind("<Control-KeyPress>", on_control_key)

        menu = tk.Menu(widget, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"])
        menu.add_command(label="Вставить", command=lambda: paste())
        menu.add_command(label="Копировать", command=lambda: copy())
        menu.add_command(label="Вырезать", command=lambda: cut())
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: select_all())
        self._context_menus.append(menu)
        widget.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.folder_var.get())
        if selected:
            self.folder_var.set(selected)
            self._persist_folder(selected)

    def _open_folder(self) -> None:
        folder = Path(self.folder_var.get().strip() or str(self.download_dir))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Папка", f"Не удалось создать папку:\n{exc}")
            return
        self._persist_folder(folder)
        path = str(folder.resolve())
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except OSError as exc:
            messagebox.showerror("Папка", f"Не удалось открыть папку:\n{exc}")

    def _bridge_check_update(self) -> dict:
        info = fetch_update_info()
        self._update_info = info
        return {
            "ok": True,
            "local_app": APP_VERSION,
            "local_extension": EXTENSION_VERSION,
            "remote_app": info.app_version,
            "remote_extension": info.extension_version,
            "app_update": info.app_update_available,
            "extension_update": info.extension_update_available,
            "app_zip": info.app_zip_url,
            "extension_zip": info.extension_zip_url,
            "notes": info.notes,
            "release_page": info.release_page,
        }

    def _note_update_deferred(self) -> None:
        if self._update_deferred_logged:
            return
        self._update_deferred_logged = True
        self._log("Обновление отложено: идёт скачивание")

    def _retry_update_later(self, callback) -> None:
        self._note_update_deferred()
        self.after(15_000, callback)

    def _quit_after_update(self) -> None:
        if self._queue_blocks_update():
            self._update_applying = False
            if self._update_info is not None:
                self._auto_apply_update(self._update_info)
            return
        self._stop_tray()
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            with contextlib.suppress(Exception):
                bridge.shutdown()
            self._bridge = None
        with contextlib.suppress(Exception):
            self.destroy()
        # Hard-exit so the onefile exe unlocks and the updater can replace it.
        os._exit(0)

    def _bridge_update_extension(self, url: str | None) -> dict:
        def run() -> None:
            if self._queue_blocks_update():
                self._retry_update_later(run)
                return
            try:
                install_extension_update(url or None)
                self.status_var.set("Плагин обновлён")
            except Exception as exc:
                messagebox.showerror("Обновление", str(exc))

        if self._queue_blocks_update():
            self.after(0, lambda: self._retry_update_later(run))
            return {"ok": True, "queued": True, "deferred": True}
        path = install_extension_update(url or None)
        return {"ok": True, "path": str(path), "reload": True}

    def _bridge_update_app(self, url: str | None) -> dict:
        # Schedule UI-thread quit after updater bat is launched.
        def apply() -> None:
            if self._queue_blocks_update():
                self._retry_update_later(apply)
                return
            try:
                install_app_update(url or None, status=lambda m: self.status_var.set(m))
                if self._queue_blocks_update():
                    self._update_applying = False
                    self._retry_update_later(apply)
                    return
                notify_windows("TubeSave", "Обновление скачано. Перезапуск…")
                self.after(800, self._quit_after_update)
            except Exception as exc:
                messagebox.showerror("Обновление", str(exc))

        self.after(0, apply)
        return {"ok": True, "queued": True, "deferred": bool(self._queue_blocks_update())}

    def _bridge_apply_updates(self) -> dict:
        info = fetch_update_info()
        self._update_info = info
        self.after(0, lambda: self._auto_apply_update(info))
        return {
            "ok": True,
            "queued": True,
            "deferred": bool(self._queue_blocks_update()),
            "app_update": info.app_update_available,
            "extension_update": info.extension_update_available,
        }

    def _check_updates_silent(self) -> None:
        self.after(6 * 60 * 60 * 1000, self._check_updates_silent)

        def worker() -> None:
            try:
                info = fetch_update_info()
                self._update_info = info
                if info.app_update_available or info.extension_update_available:
                    self.after(0, lambda: self._auto_apply_update(info))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name="TubeSaveUpdateCheck").start()

    def _auto_apply_update(self, info) -> None:
        if self._queue_blocks_update():
            self._retry_update_later(lambda i=info: self._auto_apply_update(i))
            return
        if self._is_busy:
            self.after(2_000, lambda i=info: self._auto_apply_update(i))
            return
        self._update_deferred_logged = False
        if not (info.app_update_available or info.extension_update_available):
            self.status_var.set("Версия актуальна")
            return
        if self._update_applying:
            return
        self._update_applying = True
        parts = []
        if info.app_update_available:
            parts.append(f"приложение {APP_VERSION} → {info.app_version}")
        if info.extension_update_available:
            parts.append(f"плагин {EXTENSION_VERSION} → {info.extension_version}")
        msg = "Автообновление: " + ", ".join(parts)
        self.status_var.set(msg)
        self._log(msg)
        notify_windows("TubeSave — обновление", msg)
        self._apply_updates(info)

    def _apply_updates(self, info) -> None:
        def abort_for_download() -> bool:
            if not self._queue_blocks_update():
                return False
            self._update_applying = False
            self.after(0, lambda i=info: self._auto_apply_update(i))
            return True

        def worker() -> None:
            try:
                if abort_for_download():
                    return
                if info.extension_update_available:
                    self.after(0, lambda: self.status_var.set("Обновление плагина…"))
                    install_extension_update(info.extension_zip_url)
                    if abort_for_download():
                        return
                if info.app_update_available:
                    self.after(0, lambda: self.status_var.set("Обновление приложения…"))
                    install_app_update(
                        info.app_zip_url,
                        status=lambda m: self.after(0, lambda: self.status_var.set(m)),
                    )
                    if abort_for_download():
                        return
                    self.after(0, lambda: notify_windows("TubeSave", "Перезапуск…"))
                    self.after(600, self._quit_after_update)
                    return
                self.after(
                    0,
                    lambda: notify_windows(
                        "TubeSave",
                        "Плагин обновлён. Если кнопки в браузере не обновились — перезагрузите расширение.",
                    ),
                )
                self.after(0, lambda: self.status_var.set("Плагин обновлён"))
            except Exception as exc:
                self._update_applying = False
                self.after(0, lambda: messagebox.showerror("Обновление", str(exc)))

        threading.Thread(target=worker, daemon=True, name="TubeSaveApplyUpdate").start()

    def _tray_icon_image(self):
        from PIL import Image, ImageDraw

        # Prefer packaged extension icon when available.
        for candidate in (
            extension_dir() / "icons" / "icon128.png",
            Path(getattr(sys, "_MEIPASS", "")) / "browser-extension" / "icons" / "icon128.png",
            Path(__file__).resolve().parent / "browser-extension" / "icons" / "icon128.png",
        ):
            if candidate.exists():
                with contextlib.suppress(OSError):
                    return Image.open(candidate).convert("RGBA")

        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill=(47, 111, 237, 255))
        # Simple download arrow
        draw.rectangle((28, 14, 36, 34), fill=(255, 255, 255, 255))
        draw.polygon([(18, 32), (46, 32), (32, 48)], fill=(255, 255, 255, 255))
        draw.rectangle((18, 50, 46, 56), fill=(255, 255, 255, 255))
        return image

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        try:
            import pystray
            icon_image = self._tray_icon_image()
        except Exception:
            self._tray = None
            return

        menu = pystray.Menu(
            pystray.MenuItem("Открыть TubeSave", self._tray_show, default=True),
            pystray.MenuItem("Выход", self._tray_quit),
        )
        self._tray = pystray.Icon(
            "TubeSave",
            icon_image,
            "TubeSave",
            menu,
        )

        def run_tray() -> None:
            assert self._tray is not None
            try:
                self._tray.run()
            except Exception:
                return

        self._tray_thread = threading.Thread(target=run_tray, name="TubeSaveTray", daemon=True)
        self._tray_thread.start()

    def _tray_show(self, _icon=None, _item=None) -> None:
        self.after(0, self._bring_to_front)

    def _tray_quit(self, _icon=None, _item=None) -> None:
        self.after(0, self._quit_app)

    def _stop_tray(self) -> None:
        tray = self._tray
        self._tray = None
        if tray is not None:
            with contextlib.suppress(Exception):
                tray.stop()

    def _minimize_to_tray(self) -> None:
        self._ensure_tray()
        self.withdraw()
        if not self._tray_notified:
            self._tray_notified = True
            self._settings["tray_hint_shown"] = True
            save_settings(self._settings)
            notify_windows(
                "TubeSave",
                "Приложение свёрнуто в трей. Откройте через скрытые значки на панели задач.",
            )

    def _on_close(self) -> None:
        # Close button (X) hides to tray instead of quitting.
        self._minimize_to_tray()

    def _quit_app(self) -> None:
        self._cancel_event.set()
        self._stop_tray()
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            with contextlib.suppress(Exception):
                bridge.shutdown()
            self._bridge = None
        self.destroy()

    def _on_bridge_url(
        self,
        url: str,
        auto_start: bool,
        audio_only: bool = False,
        quality: str = "best",
        cookies: str = "",
        audio_format: str = "",
    ) -> None:
        # HTTP thread → UI thread
        self.after(
            0,
            lambda: self.receive_external_url(
                url,
                auto_start=auto_start,
                audio_only=audio_only,
                quality=quality,
                cookies=cookies,
                audio_format=audio_format,
            ),
        )

    def _on_bridge_focus(self) -> None:
        self.after(0, self._bring_to_front)

    def _bring_to_front(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        with contextlib.suppress(tk.TclError):
            self.focus_force()

    def receive_external_url(
        self,
        url: str,
        *,
        auto_start: bool = True,
        audio_only: bool = False,
        quality: str | None = None,
        cookies: str = "",
        audio_format: str | None = None,
    ) -> None:
        """Fill the URL field from browser extension / protocol / second instance."""
        url = (url or "").strip()
        if not url:
            return

        requested_quality = str(quality or "").strip().lower().rstrip("p")
        if requested_quality not in QUALITY_CODES:
            requested_quality = ""

        requested_format = ""
        if audio_format not in (None, ""):
            requested_format = normalize_audio_format(audio_format)

        now = time.monotonic()
        stamp = (
            f"{url}|{int(bool(audio_only))}|"
            f"{requested_quality or self._quality}|{requested_format or self._audio_format}"
        )
        last = self._last_external
        if last and last[0] == stamp and (now - last[1]) < 6:
            return
        self._last_external = (stamp, now)
        if "music.yandex." in url.lower() and cookies:
            from downloader import prepare_yandex_cookies

            cookies = prepare_yandex_cookies(cookies)
        self._pending_cookies = cookies or ""

        # Music links always go through audio pipeline.
        if "music.yandex." in url.lower():
            audio_only = True

        if requested_quality in QUALITY_CODES:
            self._quality = requested_quality
            self._persist_quality()
            for chip in getattr(self, "_quality_chips", []):
                chip.set_selected(chip.code == self._quality)

        if audio_only and requested_format in AUDIO_FORMAT_CODES:
            self._audio_format = requested_format
            self._persist_audio_format()
            for chip in getattr(self, "_audio_format_chips", []):
                chip.set_selected(chip.code == self._audio_format)
            self._sync_audio_button()

        if not self._is_downloading:
            self.url_var.set(url)
        if audio_only:
            kind = "MP3" if self._audio_format == "mp3" else "AAC"
        else:
            kind = "MP4"
        quality_label = next(
            (label for code, label in QUALITY_OPTIONS if code == self._quality),
            self._quality,
        )
        self._log(f"Из браузера ({kind}, {quality_label}): {url}")
        self.status_var.set(f"Ссылка получена · {site_label(url)} · {kind} · {quality_label}")

        if not is_supported_url(url):
            notify_windows(
                "TubeSave",
                "Сайт не поддерживается: " + site_label(url),
            )
            return

        if auto_start:
            self._enqueue_urls(
                [url],
                audio_only=audio_only,
                quiet=True,
                cookies=getattr(self, "_pending_cookies", "") or "",
            )
            waiting = self._is_downloading or len(self._pending_jobs()) > 1
            prefix = "В очередь" if waiting else "Скачивание в фоне"
            notify_windows("TubeSave", f"{prefix} · {kind} · {quality_label}")
            self._pump_queue()

    def _show_browser_help(self) -> None:
        folder = extension_dir()
        folder.mkdir(parents=True, exist_ok=True)
        register_protocol()
        register_native_host()
        with contextlib.suppress(OSError):
            os.startfile(folder)  # type: ignore[attr-defined]

        messagebox.showinfo(
            "Кнопка в браузере",
            "Расширение TubeSave для Chrome / Edge / Яндекс.Браузер:\n\n"
            "1. Откройте страницу расширений:\n"
            "   • Chrome: chrome://extensions\n"
            "   • Edge: edge://extensions\n"
            "   • Яндекс: browser://extensions\n"
            "2. Включите «Режим разработчика»\n"
            "3. «Загрузить распакованное расширение»\n"
            f"4. Выберите папку:\n{folder}\n\n"
            "На YouTube появится кнопка TubeSave; также работает иконка\n"
            "расширения на панели браузера.\n\n"
            "Яндекс.Браузер: распакованные расширения (не из магазина)\n"
            "отключаются при каждом перезапуске — это политика браузера.\n"
            "После запуска нажмите «Включить» на карточке TubeSave\n"
            "(не нужно загружать папку заново). Постоянно — только из\n"
            "Chrome Web Store или Яндекс.Браузер Beta для разработки.\n\n"
            f"Приложение можно не держать открытым: по кнопке «Скачать»\n"
            "браузер сам запустит TubeSave (если спросит разрешение — «Открыть»).\n"
            "Протокол tubesave:// регистрируется при каждом запуске.\n\n"
            "YouTube иногда просит «подтвердить, что вы не бот». Тогда откройте\n"
            "ролик в браузере (лучше войти в Google) и скачайте кнопкой TubeSave\n"
            "на странице — так подхватятся cookies сессии.",
        )

    def _queue_blocks_update(self) -> bool:
        return self._is_downloading or bool(self._pending_jobs())

    def _pending_jobs(self) -> list[QueueJob]:
        return [job for job in getattr(self, "_queue_jobs", []) if job.status == "pending"]

    def _finished_jobs(self) -> list[QueueJob]:
        return [
            job
            for job in getattr(self, "_queue_jobs", [])
            if job.status in {"done", "error", "cancelled"}
        ]

    def _queue_key(
        self,
        url: str,
        audio_only: bool,
        quality: str,
        audio_format: str,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> str:
        fmt = normalize_audio_format(audio_format) if audio_only else ""
        return f"{url}|{int(bool(audio_only))}|{quality}|{fmt}|{start_time}|{end_time}"

    def _job_already_queued(
        self,
        url: str,
        audio_only: bool,
        quality: str,
        audio_format: str,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> bool:
        key = self._queue_key(url, audio_only, quality, audio_format, start_time, end_time)
        for job in self._queue_jobs:
            if job.status not in {"pending", "running"}:
                continue
            if self._queue_key(
                job.url, job.audio_only, job.quality, job.audio_format, job.start_time, job.end_time
            ) == key:
                return True
        return False

    def _trim_finished_jobs(self) -> None:
        finished = self._finished_jobs()
        extra = len(finished) - QUEUE_KEEP_FINISHED
        if extra <= 0:
            return
        drop_ids = {job.id for job in finished[:extra]}
        self._queue_jobs = [job for job in self._queue_jobs if job.id not in drop_ids]

    def _enqueue_urls(
        self,
        urls: list[str],
        *,
        audio_only: bool,
        quiet: bool,
        cookies: str = "",
        quality: str | None = None,
        audio_format: str | None = None,
        folder: Path | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> int:
        selected_quality = quality or self._quality
        selected_format = normalize_audio_format(audio_format or self._audio_format)
        dest = folder or Path(self.folder_var.get().strip() or str(self.download_dir))
        added = 0
        skipped = 0
        for url in urls:
            url = url.strip()
            if not url or not is_supported_url(url):
                continue
            job_audio = True if "music.yandex." in url.lower() else audio_only
            if self._job_already_queued(
                url, job_audio, selected_quality, selected_format, start_time, end_time
            ):
                skipped += 1
                continue
            self._queue_seq += 1
            job = QueueJob(
                id=self._queue_seq,
                url=url,
                audio_only=job_audio,
                quality=selected_quality,
                audio_format=selected_format,
                folder=dest,
                cookies=cookies,
                quiet=quiet,
                start_time=start_time,
                end_time=end_time,
            )
            self._queue_jobs.append(job)
            added += 1
            clip = job.trim_label()
            extra = f", {clip}" if clip else ""
            self._log(f"В очередь ({job.kind}, {job.quality_label()}{extra}): {url}")
        if skipped and not added:
            self._log("Эта ссылка уже в очереди")
        self._trim_finished_jobs()
        self._refresh_queue_ui()
        return added

    def _clear_pending_jobs(self) -> None:
        if not self._pending_jobs():
            return
        self._queue_jobs = [job for job in self._queue_jobs if job.status != "pending"]
        self._log("Очередь ожидания очищена")
        self._refresh_queue_ui()

    def _clear_finished_jobs(self) -> None:
        if not self._finished_jobs():
            return
        self._queue_jobs = [
            job for job in self._queue_jobs if job.status in {"pending", "running"}
        ]
        self._refresh_queue_ui()

    def _remove_queue_job(self, job_id: int) -> None:
        self._queue_jobs = [
            job
            for job in self._queue_jobs
            if not (job.id == job_id and job.status == "pending")
        ]
        self._refresh_queue_ui()

    def _refresh_queue_ui(self) -> None:
        title = getattr(self, "queue_title_var", None)
        if title is None:
            return
        pending = len(self._pending_jobs())
        running = 1 if self._active_job is not None else 0
        if running or pending:
            title.set(f"Очередь · {running + pending}")
        else:
            title.set("Очередь")

        for row in getattr(self, "_queue_rows", []):
            row.destroy()
        self._queue_rows = []

        empty = getattr(self, "queue_empty_label", None)
        jobs = list(getattr(self, "_queue_jobs", []))
        if empty is not None:
            if jobs:
                empty.pack_forget()
            else:
                empty.configure(fg=COLORS["muted"], bg=COLORS["ghost"])
                empty.pack(fill="x")

        status_fg = {
            "pending": COLORS["muted"],
            "running": COLORS["accent"],
            "done": COLORS["success"],
            "error": COLORS["danger"],
            "cancelled": COLORS["muted"],
        }
        for job in jobs:
            row = tk.Frame(self._queue_inner, bg=COLORS["ghost"])
            row.pack(fill="x", padx=6, pady=3)
            self._queue_rows.append(row)

            text = f"{job.title}  ·  {job.kind}"
            if not job.audio_only:
                text += f" {job.quality_label()}"
            clip = job.trim_label()
            if clip:
                text += f"  {clip}"
            name = tk.Label(
                row,
                text=text,
                font=FONTS["small"],
                fg=COLORS["text"],
                bg=COLORS["ghost"],
                anchor="w",
            )
            name.pack(side="left", fill="x", expand=True)

            state = tk.Label(
                row,
                text=job.status_label(),
                font=FONTS["small"],
                fg=status_fg.get(job.status, COLORS["muted"]),
                bg=COLORS["ghost"],
                width=12,
                anchor="e",
            )
            state.pack(side="right")

            if job.status == "pending":
                remove = tk.Label(
                    row,
                    text="×",
                    font=FONTS["title"],
                    fg=COLORS["muted"],
                    bg=COLORS["ghost"],
                    cursor="hand2",
                    padx=8,
                )
                remove.pack(side="right")
                remove.bind("<Button-1>", lambda _e, jid=job.id: self._remove_queue_job(jid))

            row.bind(
                "<MouseWheel>",
                lambda e: self._queue_canvas.yview_scroll(int(-e.delta / 120), "units"),
            )

        self._queue_inner.update_idletasks()
        self._queue_canvas.configure(scrollregion=self._queue_canvas.bbox("all") or (0, 0, 0, 0))
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        if getattr(self, "info_btn", None) is None:
            return
        fetching = self._is_busy and not self._is_downloading
        self.info_btn.set_enabled(not self._is_busy)
        self.download_btn.set_enabled(not fetching)
        self.audio_btn.set_enabled(not fetching)
        self.cancel_btn.set_enabled(self._is_downloading or bool(self._pending_jobs()))
        clear_pending = getattr(self, "clear_queue_btn", None)
        clear_done = getattr(self, "clear_done_btn", None)
        if clear_pending is not None:
            clear_pending.set_enabled(bool(self._pending_jobs()))
        if clear_done is not None:
            clear_done.set_enabled(bool(self._finished_jobs()))

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        if not busy:
            self._is_downloading = False
            self._active_job = None
        self._sync_action_buttons()

    def _cancel_download(self) -> None:
        if self._is_downloading:
            self._cancel_event.set()
            self.cancel_btn.set_enabled(False)
            self.status_var.set("Отмена…")
            self._set_stage("Отмена")
            self._log("Отмена текущей загрузки…")
            return
        if self._pending_jobs():
            self._clear_pending_jobs()

    def _pump_queue(self) -> None:
        if self._is_busy:
            self._sync_action_buttons()
            return
        next_job = next((job for job in self._queue_jobs if job.status == "pending"), None)
        if next_job is None:
            self._sync_action_buttons()
            return
        self._run_queue_job(next_job)

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start_timer(self) -> None:
        self._started_at = time.monotonic()
        self._tick_timer()

    def _stop_timer(self) -> None:
        if self._timer_job is not None:
            self.after_cancel(self._timer_job)
            self._timer_job = None

    def _tick_timer(self) -> None:
        if self._started_at is None:
            return
        elapsed = time.monotonic() - self._started_at
        self.elapsed_var.set(f"Прошло: {format_duration(elapsed)}")
        self._timer_job = self.after(250, self._tick_timer)

    def _set_stage(self, stage: str) -> None:
        self._current_stage = stage
        self.stage_var.set(f"Этап: {stage}")

    def _reset_metrics(self) -> None:
        self.percent_var.set("0%")
        self.speed_var.set("Скорость: —")
        self.eta_var.set("ETA: —")
        self.elapsed_var.set("Прошло: 0:00")
        self.size_var.set("Размер: —")
        self.progress.set_value(0)

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break

            if event == "info":
                self.info_var.set(str(payload))
                self.info_label.configure(fg=COLORS["text"])
            elif event == "status":
                self.status_var.set(str(payload))
            elif event == "stage":
                self._set_stage(str(payload))
                self._log(str(payload))
            elif event == "log":
                self._log(str(payload))
            elif event == "progress":
                value = float(payload)
                self.progress.set_value(value)
                self.percent_var.set(f"{value:.1f}%")
            elif event == "progress_mode":
                if payload == "indeterminate":
                    self.progress.start_indeterminate()
                else:
                    self.progress.stop()
            elif event == "speed":
                self.speed_var.set(f"Скорость: {payload}")
            elif event == "eta":
                self.eta_var.set(f"ETA: {payload}")
            elif event == "size":
                self.size_var.set(f"Размер: {payload}")
            elif event == "done":
                success, message = payload  # type: ignore[misc]
                job = self._active_job
                self._set_busy(False)
                self._stop_timer()
                self.progress.stop()
                remaining = len(self._pending_jobs())
                if job is not None:
                    job.status = "done" if success else "error"
                    job.message = str(message)
                    if success and "\n" in str(message):
                        saved = str(message).rsplit("\n", 1)[-1].strip()
                        if saved:
                            job.title = Path(saved).name
                    self._refresh_queue_ui()
                if success:
                    self.progress.set_value(100)
                    self.percent_var.set("100%")
                    self._set_stage("Готово")
                    self.status_var.set(message.split("\n")[0])
                    self._log(message.replace("\n", " "))
                    if remaining:
                        notify_windows("TubeSave — готово", f"{message.splitlines()[0]} · ещё {remaining}")
                    else:
                        notify_windows("TubeSave — готово", message)
                else:
                    self.progress.set_value(0)
                    self.percent_var.set("0%")
                    self._set_stage("Ошибка")
                    self.status_var.set("Ошибка")
                    self._log(str(message))
                    quiet = bool(self._quiet_download or remaining)
                    if quiet:
                        notify_windows("TubeSave — ошибка", str(message))
                    else:
                        messagebox.showerror("Ошибка", message)
                self._pump_queue()
            elif event == "cancelled":
                job = self._active_job
                self._set_busy(False)
                self._stop_timer()
                self.progress.stop()
                self.progress.set_value(0)
                self.percent_var.set("0%")
                self._set_stage("Отменено")
                self.status_var.set("Отменено")
                self._log(str(payload) or "Загрузка отменена")
                if job is not None:
                    job.status = "cancelled"
                    job.message = str(payload or "Загрузка отменена")
                    self._refresh_queue_ui()
                remaining = len(self._pending_jobs())
                if self._quiet_download or remaining:
                    notify_windows("TubeSave", "Загрузка отменена")
                self._pump_queue()

        self.after(80, self._process_events)

    def _validate_folder(self, *, quiet: bool = False) -> Path | None:
        folder = Path(self.folder_var.get().strip())
        if not folder.exists():
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                if quiet:
                    notify_windows("TubeSave", f"Не удалось создать папку: {exc}")
                else:
                    messagebox.showerror("Папка", f"Не удалось создать папку:\n{exc}")
                return None
        self._persist_folder(folder)
        return folder

    def _read_trim_range(
        self,
        *,
        quiet: bool = False,
        urls: list[str] | None = None,
    ) -> tuple[float | None, float | None] | None:
        try:
            start = parse_timestamp(self.trim_start_var.get())
            end = parse_timestamp(self.trim_end_var.get())
            start, end = normalize_time_range(start, end)
        except ValueError as exc:
            if quiet:
                notify_windows("TubeSave", str(exc))
            else:
                messagebox.showwarning("Обрезка", str(exc))
            return None
        if start is None and urls and len(urls) == 1:
            start = timestamp_from_url(urls[0])
            try:
                start, end = normalize_time_range(start, end)
            except ValueError:
                start = None
        return start, end

    def _validate_inputs(self, *, quiet: bool = False) -> tuple[str, Path] | None:
        urls = extract_media_urls(self.url_var.get())
        if not urls:
            raw = self.url_var.get().strip()
            if not raw:
                if not quiet:
                    messagebox.showwarning("Ссылка", "Вставьте ссылку на видео.")
                return None
            if quiet:
                notify_windows("TubeSave", "Неверная ссылка")
            else:
                messagebox.showwarning(
                    "Ссылка",
                    "Неверная ссылка. Поддерживаются:\n" + SUPPORTED_SITES_HINT,
                )
            return None
        folder = self._validate_folder(quiet=quiet)
        if folder is None:
            return None
        return urls[0], folder

    def _fetch_info(self) -> None:
        validated = self._validate_inputs()
        if validated is None or self._is_busy:
            return

        url, _folder = validated
        self._cancel_event.clear()
        self._set_busy(True)
        self._reset_metrics()
        self._start_timer()
        self._events.put(("progress_mode", "indeterminate"))
        self._events.put(("stage", "Проверка ссылки"))
        self._events.put(("status", "Получение информации о видео…"))

        def worker() -> None:
            try:
                label = site_label(url)
                self._events.put(("stage", f"Запрос к {label}"))
                info = fetch_video_info(
                    url,
                    cancel_event=self._cancel_event,
                    cookies=getattr(self, "_pending_cookies", "") or "",
                )
                title = info.get("title", "Без названия")
                duration = info.get("duration")
                uploader = (
                    info.get("uploader")
                    or info.get("channel")
                    or info.get("creator")
                    or "Неизвестно"
                )
                is_short = "/shorts/" in url.lower()
                duration_text = format_duration(float(duration) if duration else None)
                kind = "Shorts" if is_short else label

                text = (
                    f"{title}\n"
                    f"{uploader} · {duration_text} · {kind}"
                )
                self._events.put(("info", text))
                self._events.put(("stage", "Информация получена"))
                self._events.put(("done", (True, "Можно начинать скачивание.")))
            except DownloadCancelled:
                self._events.put(("cancelled", "Проверка отменена"))
            except Exception as exc:
                if self._cancel_event.is_set():
                    self._events.put(("cancelled", "Проверка отменена"))
                else:
                    self._events.put(("done", (False, f"Не удалось получить информацию:\n{exc}")))

        threading.Thread(target=worker, daemon=True).start()

    def _start_download(self, audio_only: bool = False, *, quiet: bool = False) -> None:
        self._quiet_download = quiet
        fetching = self._is_busy and not self._is_downloading
        if fetching:
            return
        urls = extract_media_urls(self.url_var.get())
        if not urls:
            self._validate_inputs(quiet=quiet)
            return
        folder = self._validate_folder(quiet=quiet)
        if folder is None:
            return
        trim = self._read_trim_range(quiet=quiet, urls=urls)
        if trim is None:
            return
        start_time, end_time = trim
        added = self._enqueue_urls(
            urls,
            audio_only=audio_only,
            quiet=quiet,
            cookies=getattr(self, "_pending_cookies", "") or "",
            folder=folder,
            start_time=start_time,
            end_time=end_time,
        )
        if not added:
            if self._job_already_queued(
                urls[0], audio_only, self._quality, self._audio_format, start_time, end_time
            ):
                self.status_var.set("Уже в очереди")
            return
        pending = len(self._pending_jobs())
        if self._is_downloading and pending:
            self.status_var.set(f"Добавлено в очередь · {pending} в ожидании")
        self._pump_queue()

    def _run_queue_job(self, job: QueueJob) -> None:
        self._quiet_download = job.quiet
        self._active_job = job
        job.status = "running"
        self._cancel_event.clear()
        self._is_downloading = True
        self._set_busy(True)
        self.url_var.set(job.url)
        self._reset_metrics()
        self._start_timer()
        self._refresh_queue_ui()
        self._events.put(("progress_mode", "indeterminate"))
        self._events.put(("stage", "Подготовка"))
        remaining = len(self._pending_jobs())
        position = f" ({remaining + 1} в очереди)" if remaining else ""
        if job.audio_only:
            self._events.put(("status", f"Скачивание аудио ({job.kind}){position}…"))
            self._events.put(("log", f"Аудио {job.kind}: {job.url}"))
        else:
            self._events.put(("status", f"Подготовка к скачиванию{position}…"))
            self._events.put(("log", f"Ссылка: {job.url}"))
            self._events.put(("log", f"Качество: {job.quality_label()}"))
        clip = job.trim_label()
        if clip:
            self._events.put(("log", f"Фрагмент: {clip}"))
        info_line = f"{job.title}\n{site_label(job.url)} · {job.kind} · {job.quality_label()}"
        if clip:
            info_line += f" · {clip}"
        self.info_var.set(info_line)
        self.info_label.configure(fg=COLORS["text"])

        url = job.url
        folder = job.folder
        audio_only = job.audio_only
        selected_quality = job.quality
        selected_audio_format = job.audio_format
        cookies = job.cookies
        start_time = job.start_time
        end_time = job.end_time

        def status_callback(message: str) -> None:
            stage_map = {
                "Получение информации о видео…": "Метаданные",
                "Объединение видео и аудио…": "Слияние",
                "Конвертация в MP4…": "Конвертация",
                "Сохранение файла…": "Сохранение",
                "Проверка результата…": "Проверка",
                "Объединение завершено": "Слияние",
                "Встраивание превью…": "Превью",
                "Превью и метаданные…": "Метаданные",
                "Перекодирование в MP3…": "MP3",
                "Перекодирование AAC → MP3…": "MP3",
                "Обход блокировки…": "Обход",
                "Обрезка по времени…": "Обрезка",
            }
            stage = stage_map.get(message)
            if stage is None and message.startswith("Подключение к "):
                stage = "Подключение"
            elif stage is None and message.startswith("Повтор скачивания"):
                stage = "Повтор"
            if stage:
                self._events.put(("stage", stage))
            self._events.put(("status", message))

        def progress_hook(data: dict) -> None:
            status = data.get("status")
            if status == "downloading":
                self._events.put(("progress_mode", "determinate"))
                self._events.put(("stage", "Скачивание"))
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes", 0) or 0
                speed = data.get("_speed_str") or format_bytes(data.get("speed")) + "/с"
                eta_raw = data.get("eta")
                eta = data.get("_eta_str") or format_duration(
                    float(eta_raw) if eta_raw is not None else None
                )

                self._events.put(("speed", speed if speed != "—/с" else "—"))
                self._events.put(("eta", eta))
                if total:
                    percent = min(downloaded / total * 100, 99.0)
                    self._events.put(("progress", percent))
                    self._events.put(("size", f"{format_bytes(downloaded)} / {format_bytes(total)}"))
                    self._events.put(("status", f"Скачивание {percent:.1f}%"))
                else:
                    self._events.put(("progress", 50.0))
                    self._events.put(("size", format_bytes(downloaded)))
                    self._events.put(("status", f"Скачивание {format_bytes(downloaded)}"))
            elif status == "finished":
                filename = Path(str(data.get("filename") or "")).name
                self._events.put(("stage", "Обработка"))
                self._events.put(("status", "Фрагмент загружен, обработка…"))
                self._events.put(("progress", 95.0))
                if filename:
                    self._events.put(("log", f"Фрагмент: {filename}"))

        def worker() -> None:
            sink = io.StringIO()
            try:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    filepath = download_video(
                        url,
                        folder,
                        progress_hook=progress_hook,
                        status_callback=status_callback,
                        audio_only=audio_only,
                        quality=selected_quality,
                        audio_format=selected_audio_format,
                        cookies=cookies,
                        cancel_event=self._cancel_event,
                        start_time=start_time,
                        end_time=end_time,
                    )
                size = format_bytes(filepath.stat().st_size) if filepath.exists() else "—"
                self._events.put(("size", size))
                self._events.put(("log", f"Сохранено: {filepath}"))
                if audio_only:
                    self._events.put(("done", (True, f"Аудио сохранено:\n{filepath}")))
                else:
                    self._events.put(("done", (True, f"Видео сохранено:\n{filepath}")))
            except DownloadCancelled:
                self._events.put(("cancelled", "Загрузка отменена, файлы удалены"))
            except Exception as exc:
                if self._cancel_event.is_set():
                    self._events.put(("cancelled", "Загрузка отменена, файлы удалены"))
                    return
                log_output = sink.getvalue().strip()
                if log_output:
                    self._events.put(("log", log_output[-1200:]))
                self._events.put(("done", (False, str(exc))))

        threading.Thread(target=worker, daemon=True).start()


def _handoff_to_running(pending: list[tuple[str, bool, bool, str, str]], timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pending:
            if all(
                try_handoff(url, auto, audio, quality, audio_format)
                for url, auto, audio, quality, audio_format in pending
            ):
                return True
        elif is_bridge_alive() and try_focus():
            return True
        time.sleep(0.35)
    if pending:
        return all(
            try_handoff(url, auto, audio, quality, audio_format)
            for url, auto, audio, quality, audio_format in pending
        )
    return is_bridge_alive() and try_focus()


def main() -> None:
    if "--native-messaging" in sys.argv:
        run_native_host()
        return
    prepare_user_data()

    pending = collect_launch_urls()
    want_update = any(is_update_launch(arg) for arg in sys.argv[1:])

    if not acquire_instance_lock():
        if want_update:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if try_apply_updates():
                    return
                time.sleep(0.35)
            _handoff_to_running(pending)
            return
        _handoff_to_running(pending)
        return

    register_protocol()
    register_native_host()

    app = YouTubeDownloaderApp(
        apply_update_on_start=want_update,
        start_hidden=bool(pending) and not want_update,
    )
    for url, auto, audio, quality, audio_format in pending:
        app.after(
            400,
            lambda u=url, a=auto, au=audio, q=quality, f=audio_format: app.receive_external_url(
                u, auto_start=a, audio_only=au, quality=q, audio_format=f
            ),
        )
    app.mainloop()


if __name__ == "__main__":
    main()
