"""Modern minimal GUI for downloading YouTube videos and Shorts as MP4."""

from __future__ import annotations

import contextlib
import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from downloader import download_video, fetch_video_info, is_youtube_url


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

    def _bg(self) -> str:
        if not self._enabled:
            return COLORS["ghost"]
        if self._primary:
            return COLORS["accent_hover"] if self._hover else COLORS["accent"]
        return COLORS["ghost_hover"] if self._hover else COLORS["ghost"]

    def _fg(self) -> str:
        if not self._enabled:
            return COLORS["muted"]
        return "#FFFFFF" if self._primary else COLORS["text"]

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
    def __init__(self) -> None:
        super().__init__()
        self.title("TubeSave")
        self.resizable(True, True)

        default_dir = Path.home() / "Downloads" / "YouTube"
        self._settings = load_settings()
        saved_dir = str(self._settings.get("download_dir") or "").strip()
        self.download_dir = Path(saved_dir) if saved_dir else default_dir
        theme = str(self._settings.get("theme") or "light").lower()
        self._theme = "dark" if theme == "dark" else "light"
        saved_quality = str(self._settings.get("quality") or "best").strip().lower()
        self._quality = saved_quality if saved_quality in QUALITY_CODES else "best"
        COLORS.clear()
        COLORS.update(DARK if self._theme == "dark" else LIGHT)

        self.configure(bg=COLORS["bg"])
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._is_busy = False
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
        self._context_menus: list[tk.Menu] = []

        self._build_ui()
        self._apply_theme()
        self._fit_window()
        self.after(80, self._process_events)

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

    def _fit_window(self) -> None:
        """Size window so action buttons are always visible on first open."""
        self.update_idletasks()
        width = max(800, self.winfo_reqwidth())
        height = max(720, self.winfo_reqheight() + 24)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(width, max(640, screen_w - 80))
        height = min(height, max(560, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.minsize(780, 700)
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
                "Только аудио",
                lambda: self._start_download(audio_only=True),
                width=130,
            )
        )
        self.audio_btn.pack(side="left", padx=(10, 0))

        exit_btn = self._register_pill(PillButton(actions, "Выход", self.destroy, width=100))
        exit_btn.pack(side="right")

        # Scrollable-feeling content above buttons
        content = tk.Frame(root, bg=COLORS["bg"])
        content.pack(side="top", fill="both", expand=True)
        self._bg_frames.append(content)

        header = tk.Frame(content, bg=COLORS["bg"])
        header.pack(fill="x")
        self._bg_frames.append(header)

        brand = tk.Label(
            header,
            text="TubeSave",
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
            text="Видео и Shorts в MP4 — максимальное качество со звуком",
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
        self._bind_clipboard(self.url_entry)
        self.url_entry.bind("<Return>", lambda _e: self._start_download())
        self.url_entry.focus_set()

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
            text="Если выбранного разрешения нет, будет взято ближайшее доступное.",
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

    def _apply_theme(self) -> None:
        palette = DARK if self._theme == "dark" else LIGHT
        COLORS.clear()
        COLORS.update(palette)

        self.configure(bg=COLORS["bg"])
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

    def _select_quality(self, code: str) -> None:
        if code not in QUALITY_CODES:
            return
        self._quality = code
        self._persist_quality()
        for chip in self._quality_chips:
            chip.set_selected(chip.code == self._quality)

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self._persist_theme()
        self._apply_theme()

    def _bind_clipboard(self, widget: tk.Entry) -> None:
        def paste(_event: tk.Event | None = None) -> str:
            try:
                text = self.clipboard_get()
            except tk.TclError:
                return "break"
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

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        enabled = not busy
        self.info_btn.set_enabled(enabled)
        self.download_btn.set_enabled(enabled)
        self.audio_btn.set_enabled(enabled)

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
                self._set_busy(False)
                self._stop_timer()
                self.progress.stop()
                if success:
                    self.progress.set_value(100)
                    self.percent_var.set("100%")
                    self._set_stage("Готово")
                    self.status_var.set(message.split("\n")[0])
                    self._log(message.replace("\n", " "))
                    messagebox.showinfo("Готово", message)
                else:
                    self.progress.set_value(0)
                    self.percent_var.set("0%")
                    self._set_stage("Ошибка")
                    self.status_var.set("Ошибка")
                    self._log(str(message))
                    messagebox.showerror("Ошибка", message)

        self.after(80, self._process_events)

    def _validate_inputs(self) -> tuple[str, Path] | None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Ссылка", "Вставьте ссылку на видео.")
            return None
        if not is_youtube_url(url):
            messagebox.showwarning(
                "Ссылка",
                "Неверная ссылка. Поддерживаются:\n"
                "youtube.com/watch · youtube.com/shorts · youtu.be",
            )
            return None

        folder = Path(self.folder_var.get().strip())
        if not folder.exists():
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Папка", f"Не удалось создать папку:\n{exc}")
                return None

        self._persist_folder(folder)
        return url, folder

    def _fetch_info(self) -> None:
        validated = self._validate_inputs()
        if validated is None or self._is_busy:
            return

        url, _folder = validated
        self._set_busy(True)
        self._reset_metrics()
        self._start_timer()
        self._events.put(("progress_mode", "indeterminate"))
        self._events.put(("stage", "Проверка ссылки"))
        self._events.put(("status", "Получение информации о видео…"))

        def worker() -> None:
            try:
                self._events.put(("stage", "Запрос к YouTube"))
                info = fetch_video_info(url)
                title = info.get("title", "Без названия")
                duration = info.get("duration")
                uploader = info.get("uploader", "Неизвестно")
                is_short = "/shorts/" in url.lower()
                duration_text = format_duration(float(duration) if duration else None)

                text = (
                    f"{title}\n"
                    f"{uploader} · {duration_text} · "
                    f"{'Shorts' if is_short else 'Видео'}"
                )
                self._events.put(("info", text))
                self._events.put(("stage", "Информация получена"))
                self._events.put(("done", (True, "Можно начинать скачивание.")))
            except Exception as exc:
                self._events.put(("done", (False, f"Не удалось получить информацию:\n{exc}")))

        threading.Thread(target=worker, daemon=True).start()

    def _start_download(self, audio_only: bool = False) -> None:
        validated = self._validate_inputs()
        if validated is None or self._is_busy:
            return

        url, folder = validated
        self._set_busy(True)
        self._reset_metrics()
        self._start_timer()
        self._events.put(("progress_mode", "indeterminate"))
        self._events.put(("stage", "Подготовка"))
        if audio_only:
            self._events.put(("status", "Скачивание аудио…"))
            self._events.put(("log", f"Аудио: {url}"))
        else:
            quality_label = next(
                (label for code, label in QUALITY_OPTIONS if code == self._quality),
                self._quality,
            )
            self._events.put(("status", "Подготовка к скачиванию…"))
            self._events.put(("log", f"Ссылка: {url}"))
            self._events.put(("log", f"Качество: {quality_label}"))

        selected_quality = self._quality

        def status_callback(message: str) -> None:
            stage_map = {
                "Подключение к YouTube…": "Подключение",
                "Получение информации о видео…": "Метаданные",
                "Объединение видео и аудио…": "Слияние",
                "Конвертация в MP4…": "Конвертация",
                "Сохранение файла…": "Сохранение",
                "Проверка результата…": "Проверка",
                "Объединение завершено": "Слияние",
            }
            stage = stage_map.get(message)
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
                    )
                size = format_bytes(filepath.stat().st_size) if filepath.exists() else "—"
                self._events.put(("size", size))
                self._events.put(("log", f"Сохранено: {filepath}"))
                if audio_only:
                    self._events.put(("done", (True, f"Аудио сохранено:\n{filepath}")))
                else:
                    self._events.put(("done", (True, f"Видео сохранено:\n{filepath}")))
            except Exception as exc:
                log_output = sink.getvalue().strip()
                if log_output:
                    self._events.put(("log", log_output[-1200:]))
                self._events.put(("done", (False, str(exc))))

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    app = YouTubeDownloaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
