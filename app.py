"""Modern minimal GUI for downloading YouTube videos and Shorts as MP4."""

from __future__ import annotations

import contextlib
import io
import json
import queue
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


# Visual system — light, calm, utility-focused (no purple / no dark theme)
COLORS = {
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

FONTS = {
    "brand": ("Segoe UI Semibold", 22),
    "title": ("Segoe UI Semibold", 12),
    "body": ("Segoe UI", 10),
    "small": ("Segoe UI", 9),
    "mono": ("Consolas", 9),
}


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
        self.configure(bg=COLORS["bg"])
        self.resizable(True, True)

        default_dir = Path.home() / "Downloads" / "YouTube"
        self._settings = load_settings()
        saved_dir = str(self._settings.get("download_dir") or "").strip()
        self.download_dir = Path(saved_dir) if saved_dir else default_dir
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._is_busy = False
        self._started_at: float | None = None
        self._timer_job: str | None = None
        self._current_stage = "Ожидание"

        self._build_ui()
        self._fit_window()
        self.after(80, self._process_events)

    def _persist_folder(self, folder: str | Path) -> None:
        folder_str = str(folder).strip()
        if not folder_str:
            return
        self.download_dir = Path(folder_str)
        self._settings["download_dir"] = folder_str
        save_settings(self._settings)

    def _fit_window(self) -> None:
        """Size window so action buttons are always visible on first open."""
        self.update_idletasks()
        width = max(760, self.winfo_reqwidth())
        height = max(720, self.winfo_reqheight() + 24)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(width, max(640, screen_w - 80))
        height = min(height, max(560, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.minsize(700, 620)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=COLORS["bg"])
        root.pack(fill="both", expand=True, padx=24, pady=20)

        # Actions first (side=bottom) so they never get clipped by expanding content
        actions = tk.Frame(root, bg=COLORS["bg"])
        actions.pack(side="bottom", fill="x", pady=(12, 0))

        self.info_btn = PillButton(actions, "Проверить", self._fetch_info, width=120)
        self.info_btn.pack(side="left")

        self.download_btn = PillButton(
            actions, "Скачать", self._start_download, primary=True, width=140
        )
        self.download_btn.pack(side="left", padx=(10, 0))

        exit_btn = PillButton(actions, "Выход", self.destroy, width=100)
        exit_btn.pack(side="right")

        # Scrollable-feeling content above buttons
        content = tk.Frame(root, bg=COLORS["bg"])
        content.pack(side="top", fill="both", expand=True)

        brand = tk.Label(
            content,
            text="TubeSave",
            font=FONTS["brand"],
            fg=COLORS["text"],
            bg=COLORS["bg"],
        )
        brand.pack(anchor="w")

        subtitle = tk.Label(
            content,
            text="Видео и Shorts в MP4 — максимальное качество со звуком",
            font=FONTS["body"],
            fg=COLORS["muted"],
            bg=COLORS["bg"],
        )
        subtitle.pack(anchor="w", pady=(2, 14))

        # URL card
        url_card = Card(content)
        url_card.pack(fill="x", pady=(0, 10))
        url_inner = tk.Frame(url_card, bg=COLORS["surface"])
        url_inner.pack(fill="x", padx=16, pady=12)

        tk.Label(
            url_inner,
            text="Ссылка",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        ).pack(anchor="w")

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
        self._bind_clipboard(self.url_entry)
        self.url_entry.bind("<Return>", lambda _e: self._start_download())
        self.url_entry.focus_set()

        # Folder card
        folder_card = Card(content)
        folder_card.pack(fill="x", pady=(0, 10))
        folder_inner = tk.Frame(folder_card, bg=COLORS["surface"])
        folder_inner.pack(fill="x", padx=16, pady=12)

        tk.Label(
            folder_inner,
            text="Папка сохранения",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        ).pack(anchor="w")

        folder_row = tk.Frame(folder_inner, bg=COLORS["surface"])
        folder_row.pack(fill="x", pady=(6, 0))

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
        self._bind_clipboard(self.folder_entry)
        self.folder_entry.bind("<FocusOut>", lambda _e: self._persist_folder(self.folder_var.get()))

        browse = PillButton(folder_row, "Обзор", self._choose_folder, width=100, height=36)
        browse.pack(side="left", padx=(10, 0))
        browse.configure(bg=COLORS["surface"])

        # Info + progress card
        status_card = Card(content)
        status_card.pack(fill="x", pady=(0, 10))
        status_inner = tk.Frame(status_card, bg=COLORS["surface"])
        status_inner.pack(fill="x", padx=16, pady=12)

        tk.Label(
            status_inner,
            text="Статус",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        ).pack(anchor="w")

        self.info_var = tk.StringVar(value="Вставьте ссылку и нажмите «Скачать».")
        self.info_label = tk.Label(
            status_inner,
            textvariable=self.info_var,
            font=FONTS["body"],
            fg=COLORS["muted"],
            bg=COLORS["surface"],
            justify="left",
            wraplength=680,
            anchor="w",
        )
        self.info_label.pack(anchor="w", pady=(6, 10))

        self.progress = ProgressBar(status_inner, height=8)
        self.progress.pack(fill="x")

        metrics = tk.Frame(status_inner, bg=COLORS["surface"])
        metrics.pack(fill="x", pady=(10, 0))

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

        self.status_var = tk.StringVar(value="Готово к работе")
        tk.Label(
            status_inner,
            textvariable=self.status_var,
            font=FONTS["body"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
            wraplength=680,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(10, 0))

        # Log card — expands, but never pushes buttons off-screen
        log_card = Card(content)
        log_card.pack(fill="both", expand=True)
        log_inner = tk.Frame(log_card, bg=COLORS["surface"])
        log_inner.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(
            log_inner,
            text="Журнал",
            font=FONTS["title"],
            fg=COLORS["text"],
            bg=COLORS["surface"],
        ).pack(anchor="w")

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
        widget.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.folder_var.get())
        if selected:
            self.folder_var.set(selected)
            self._persist_folder(selected)

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.info_btn.set_enabled(not busy)
        self.download_btn.set_enabled(not busy)

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

    def _start_download(self) -> None:
        validated = self._validate_inputs()
        if validated is None or self._is_busy:
            return

        url, folder = validated
        self._set_busy(True)
        self._reset_metrics()
        self._start_timer()
        self._events.put(("progress_mode", "indeterminate"))
        self._events.put(("stage", "Подготовка"))
        self._events.put(("status", "Подготовка к скачиванию…"))
        self._events.put(("log", f"Ссылка: {url}"))

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
                    )
                size = format_bytes(filepath.stat().st_size) if filepath.exists() else "—"
                self._events.put(("size", size))
                self._events.put(("log", f"Сохранено: {filepath}"))
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
