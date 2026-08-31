"""Local bridge for browser extension + single-instance URL handoff."""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

from boot_clean import frozen_restart_env, windows_detach_flags

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 17834
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
NATIVE_HOST_NAME = "com.tubesave.host"
PINNED_EXTENSION_ID = "hmddmgmenbnhoeghphinmmnoeklgbhgg"
INSTANCE_MUTEX_NAME = "Local\\TubeSave.mine1510.single"
_INSTANCE_MUTEX_HANDLE = None

UrlHandler = Callable[..., None]


KNOWN_QUALITIES = {"best", "2160", "1440", "1080", "720", "480", "360"}


def _normalize_quality(value: object) -> str:
    text = str(value or "best").strip().lower().rstrip("p")
    if text in {"max", "highest", "best"}:
        return "best"
    if text in KNOWN_QUALITIES:
        return text
    return "best"


def _normalize_audio_format(value: object) -> str:
    text = str(value or "aac").strip().lower().lstrip(".")
    if text in {"mp3", "mpeg", "mpga"}:
        return "mp3"
    return "aac"


def _split_audio(
    audio_value: object,
    format_value: object = None,
    url: str = "",
) -> tuple[bool, str]:
    yandex = "music.yandex." in (url or "").lower()
    fmt = _normalize_audio_format(format_value) if format_value not in (None, "") else ""
    if audio_value is None:
        return yandex, fmt
    text = str(audio_value).strip().lower()
    if text in {"mp3", "mpeg", "mpga"}:
        return True, "mp3"
    if text in {"aac", "m4a"}:
        return True, fmt or "aac"
    return _as_bool(audio_value, yandex), fmt


def _as_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() not in {"0", "false", "no"}


def user_data_dir() -> Path:
    """Writable app data. Frozen builds must not drop files next to the .exe."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "TubeSave"
        return Path.home() / "AppData" / "Local" / "TubeSave"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "TubeSave"
    return Path.home() / ".local" / "share" / "TubeSave"


def cache_dir() -> Path:
    return user_data_dir() / "cache"


def temp_dir() -> Path:
    return user_data_dir() / "tmp"


def app_root() -> Path:
    """Directory that contains TubeSave.exe (or the source tree in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def runtime_cwd() -> Path:
    path = user_data_dir() if getattr(sys, "frozen", False) else app_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_extension_dir() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "browser-extension")
        candidates.append(Path(sys.executable).resolve().parent / "browser-extension")
    else:
        candidates.append(Path(__file__).resolve().parent / "browser-extension")
    for path in candidates:
        if (path / "manifest.json").exists():
            return path
    return None


def _extension_version(folder: Path) -> tuple[int, ...]:
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        text = str(data.get("version") or "").strip().lstrip("v")
    except (OSError, json.JSONDecodeError, TypeError):
        return (0,)
    parts: list[int] = []
    for bit in text.split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def _copy_extension_files(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if not path.is_file() or path.name == "extension.pem":
            continue
        target = dest / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _sync_bundled_extension() -> None:
    if not getattr(sys, "frozen", False):
        return
    source = bundled_extension_dir()
    dest = extension_dir()
    if source is None:
        return
    if source.resolve() == dest.resolve():
        return
    need_copy = not (dest / "manifest.json").exists()
    if not need_copy and _extension_version(source) > _extension_version(dest):
        need_copy = True
    if need_copy:
        _copy_extension_files(source, dest)


def _migrate_legacy_sidecars() -> None:
    if not getattr(sys, "frozen", False):
        return
    old_root = Path(sys.executable).resolve().parent
    data = user_data_dir()
    if old_root.resolve() == data.resolve():
        return
    old_ids = old_root / "native-extension-ids.json"
    new_ids = _extra_ids_path()
    if old_ids.exists() and not new_ids.exists():
        new_ids.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            shutil.copy2(old_ids, new_ids)


def _cleanup_install_dir_clutter() -> None:
    """Remove helper files that used to appear next to TubeSave.exe."""
    if not getattr(sys, "frozen", False):
        return
    root = Path(sys.executable).resolve().parent
    data = user_data_dir()
    if root.resolve() == data.resolve():
        return
    for name in (
        "tubesave-native-host.bat",
        "com.tubesave.host.json",
        "native-extension-ids.json",
    ):
        with contextlib.suppress(OSError):
            (root / name).unlink(missing_ok=True)
    leftover_ext = root / "browser-extension"
    dest_ext = extension_dir()
    if (
        leftover_ext.is_dir()
        and leftover_ext.resolve() != dest_ext.resolve()
        and (dest_ext / "manifest.json").exists()
    ):
        shutil.rmtree(leftover_ext, ignore_errors=True)


def prepare_user_data() -> Path:
    data = user_data_dir()
    cache_dir().mkdir(parents=True, exist_ok=True)
    temp_dir().mkdir(parents=True, exist_ok=True)
    _migrate_legacy_sidecars()
    _sync_bundled_extension()
    _cleanup_install_dir_clutter()
    if getattr(sys, "frozen", False):
        with contextlib.suppress(OSError):
            os.chdir(data)
    return data


def launch_command_for_protocol() -> str:
    """Command line registered for tubesave:// links."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" "%1"'
    python = Path(sys.executable).resolve()
    script = (Path(__file__).resolve().parent / "app.py").resolve()
    return f'"{python}" "{script}" "%1"'


def register_protocol() -> bool:
    """Register tubesave:// under HKCU so the browser can launch TubeSave."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    try:
        command = launch_command_for_protocol()
        root = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\tubesave")
        winreg.SetValueEx(root, "", 0, winreg.REG_SZ, "URL:TubeSave Protocol")
        winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.SetValueEx(root, "FriendlyTypeName", 0, winreg.REG_SZ, "TubeSave")
        icon = winreg.CreateKey(root, "DefaultIcon")
        if getattr(sys, "frozen", False):
            winreg.SetValueEx(icon, "", 0, winreg.REG_SZ, f"{Path(sys.executable).resolve()},0")
        else:
            winreg.SetValueEx(icon, "", 0, winreg.REG_SZ, command.split('"')[1] + ",0")
        shell = winreg.CreateKey(root, r"shell\open\command")
        winreg.SetValueEx(shell, "", 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def _native_host_bat_path() -> Path:
    return user_data_dir() / "tubesave-native-host.bat"


def _native_host_manifest_path() -> Path:
    return user_data_dir() / f"{NATIVE_HOST_NAME}.json"


def _write_native_host_launcher() -> Path:
    bat = _native_host_bat_path()
    bat.parent.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        body = (
            "@echo off\r\n"
            f'"{exe}" --native-messaging\r\n'
        )
    else:
        python = Path(sys.executable).resolve()
        script = (Path(__file__).resolve().parent / "app.py").resolve()
        body = (
            "@echo off\r\n"
            f'"{python}" "{script}" --native-messaging\r\n'
        )
    bat.write_text(body, encoding="utf-8")
    return bat


def _extra_ids_path() -> Path:
    return user_data_dir() / "native-extension-ids.json"


def _load_extra_extension_ids() -> list[str]:
    path = _extra_ids_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _save_extra_extension_ids(ids: list[str]) -> None:
    unique: list[str] = []
    for item in ids:
        if item and item not in unique:
            unique.append(item)
    path = _extra_ids_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(unique, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def register_native_host(extra_extension_ids: list[str] | None = None) -> bool:
    """Register a Chrome/Edge/Yandex native host that can start TubeSave."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    try:
        prepare_user_data()
        saved = _load_extra_extension_ids()
        for ext_id in extra_extension_ids or []:
            ext_id = str(ext_id).strip()
            if ext_id and ext_id not in saved:
                saved.append(ext_id)
        if extra_extension_ids:
            _save_extra_extension_ids(saved)

        bat = _write_native_host_launcher()
        origins = [f"chrome-extension://{PINNED_EXTENSION_ID}/"]
        for ext_id in saved:
            origin = f"chrome-extension://{ext_id}/"
            if origin not in origins:
                origins.append(origin)
        manifest = {
            "name": NATIVE_HOST_NAME,
            "description": "TubeSave launcher",
            "path": str(bat.resolve()),
            "type": "stdio",
            "allowed_origins": origins,
        }
        manifest_path = _native_host_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        keys = [
            rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}",
            rf"Software\Chromium\NativeMessagingHosts\{NATIVE_HOST_NAME}",
            rf"Software\Microsoft\Edge\NativeMessagingHosts\{NATIVE_HOST_NAME}",
            rf"Software\Yandex\YandexBrowser\NativeMessagingHosts\{NATIVE_HOST_NAME}",
            rf"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{NATIVE_HOST_NAME}",
        ]
        for key_path in keys:
            handle = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, str(manifest_path.resolve()))
            winreg.CloseKey(handle)
        return True
    except OSError:
        return False


def acquire_instance_lock() -> bool:
    """True if this process should run the UI (primary instance)."""
    global _INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return not is_bridge_alive()
    kernel32 = ctypes.windll.kernel32
    _INSTANCE_MUTEX_HANDLE = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    already = kernel32.GetLastError() == 183
    return not already


def launch_app_detached(
    url: str = "",
    auto_start: bool = True,
    audio_only: bool = False,
    quality: str = "best",
    extra_args: list[str] | None = None,
    audio_format: str = "aac",
) -> None:
    """Start TubeSave in a new process (used by the native messaging host)."""
    extra: list[str]
    if extra_args:
        extra = list(extra_args)
    elif url:
        protocol = (
            "tubesave://download?"
            f"url={quote(url, safe='')}&auto={1 if auto_start else 0}"
            f"&audio={1 if audio_only else 0}&quality={quote(quality or 'best', safe='')}"
        )
        if audio_format:
            protocol += f"&audio_format={quote(_normalize_audio_format(audio_format), safe='')}"
        extra = [protocol]
    else:
        extra = []
    if getattr(sys, "frozen", False):
        args = [str(Path(sys.executable).resolve()), *extra]
        cwd = str(runtime_cwd())
    else:
        script = (Path(__file__).resolve().parent / "app.py").resolve()
        args = [str(Path(sys.executable).resolve()), str(script), *extra]
        cwd = str(script.parent)
    flags = windows_detach_flags()
    subprocess.Popen(
        args,
        cwd=cwd,
        env=frozen_restart_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def run_native_host() -> None:
    """Chrome native messaging: launch TubeSave, reply, exit."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    data: dict = {}
    try:
        raw_len = sys.stdin.buffer.read(4)
        if len(raw_len) == 4:
            n = struct.unpack("<I", raw_len)[0]
            body = sys.stdin.buffer.read(n) if n else b"{}"
            parsed = json.loads(body.decode("utf-8") or "{}")
            if isinstance(parsed, dict):
                data = parsed
    except (OSError, json.JSONDecodeError, ValueError):
        data = {}

    url = str(data.get("url") or "").strip()
    action = str(data.get("action") or "").strip().lower()
    auto = _as_bool(data.get("auto", data.get("auto_start", True)), True)
    audio, audio_format = _split_audio(
        data.get("audio", data.get("audio_only")),
        data.get("audio_format", data.get("format")),
        url,
    )
    quality = _normalize_quality(data.get("quality") or "best")
    if action == "update":
        if is_bridge_alive():
            payload = {"ok": True, "alive": True, "apply": try_apply_updates()}
        else:
            launch_app_detached(extra_args=["tubesave://update"])
            payload = {"ok": True, "launched": True, "action": "update"}
    elif is_bridge_alive():
        ok = try_handoff(url, auto, audio, quality, audio_format) if url else try_focus()
        payload = {"ok": True, "alive": True, "handed": bool(ok)}
    else:
        launch_app_detached(url, auto, audio, quality, audio_format=audio_format)
        payload = {"ok": True, "launched": True}

    raw = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(raw)))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def parse_incoming_arg(raw: str) -> tuple[str | None, bool, bool, str, str]:
    """
    Parse CLI / protocol argument into (url, auto_start, audio_only, quality, audio_format).
    Supports:
      https://...
      tubesave://download?url=...&audio=1&quality=1080&audio_format=mp3
      tubesave://add?url=...&auto=0
      tubesave://https://...
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return None, True, False, "best", ""

    lower = text.lower()
    if lower.startswith("tubesave:"):
        rest = text.split(":", 1)[1]
        if rest.startswith("//"):
            rest = rest[2:]
        if rest.lower().startswith(("http://", "https://")):
            audio = "music.yandex." in rest.lower()
            return rest, True, audio, "best", ""
        query = rest.split("?", 1)[1] if "?" in rest else ""
        qs = parse_qs(query)
        url = (qs.get("url") or [None])[0]
        if not url:
            return None, True, False, "best", ""
        url = unquote(url)
        auto = _as_bool((qs.get("auto") or qs.get("auto_start") or ["1"])[0], True)
        audio, audio_format = _split_audio(
            (qs.get("audio") or qs.get("audio_only") or [None])[0],
            (qs.get("audio_format") or qs.get("afmt") or [None])[0],
            url,
        )
        quality = _normalize_quality((qs.get("quality") or qs.get("q") or ["best"])[0])
        return url, auto, audio, quality, audio_format

    if lower.startswith("http://") or lower.startswith("https://"):
        return text, True, "music.yandex." in lower, "best", ""

    return None, True, False, "best", ""


def is_update_launch(raw: str) -> bool:
    text = (raw or "").strip().strip('"').lower()
    if text in {"--update", "/update"}:
        return True
    if not text.startswith("tubesave:"):
        return False
    rest = text.split(":", 1)[1]
    if rest.startswith("//"):
        rest = rest[2:]
    path = rest.split("?", 1)[0].strip("/")
    return path == "update"


def collect_launch_urls(argv: list[str] | None = None) -> list[tuple[str, bool, bool, str, str]]:
    args = list(sys.argv[1:] if argv is None else argv)
    found: list[tuple[str, bool, bool, str, str]] = []
    for arg in args:
        url, auto, audio, quality, audio_format = parse_incoming_arg(arg)
        if url:
            found.append((url, auto, audio, quality, audio_format))
    return found


def try_handoff(
    url: str,
    auto_start: bool = True,
    audio_only: bool = False,
    quality: str = "best",
    audio_format: str = "aac",
) -> bool:
    """Send URL to an already running TubeSave. Returns True on success."""
    body = {
        "url": url,
        "auto_start": auto_start,
        "audio_only": audio_only,
        "quality": _normalize_quality(quality),
    }
    if audio_format:
        body["audio_format"] = _normalize_audio_format(audio_format)
    payload = json.dumps(body).encode("utf-8")
    req = Request(
        f"{BRIDGE_BASE}/download",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": "tubesave-local",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=1.5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            return bool(data.get("ok"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def try_apply_updates() -> bool:
    req = Request(
        f"{BRIDGE_BASE}/apply-updates",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": "tubesave-local",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(data.get("ok"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def is_bridge_alive() -> bool:
    try:
        with urlopen(f"{BRIDGE_BASE}/ping", timeout=0.8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(data.get("ok"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


_BRIDGE_CALLBACKS: dict[str, Callable | None] = {
    "on_url": None,
    "on_focus": None,
    "on_update_extension": None,
    "on_update_app": None,
    "on_check_update": None,
    "on_apply_updates": None,
}


class _BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/ping", "/health"}:
            self._json(200, {"ok": True, "app": "TubeSave", "port": BRIDGE_PORT})
            return
        if parsed.path == "/version":
            try:
                from version import APP_VERSION, EXTENSION_VERSION
            except Exception:
                APP_VERSION, EXTENSION_VERSION = "0", "0"
            self._json(
                200,
                {
                    "ok": True,
                    "app_version": APP_VERSION,
                    "extension_version": EXTENSION_VERSION,
                },
            )
            return
        if parsed.path == "/check-update":
            cb = _BRIDGE_CALLBACKS.get("on_check_update")
            if cb is None:
                self._json(503, {"ok": False, "error": "updater unavailable"})
                return
            try:
                result = cb()
                self._json(200, result if isinstance(result, dict) else {"ok": True})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/focus":
            cb = _BRIDGE_CALLBACKS.get("on_focus")
            if cb is not None:
                cb()
            self._json(200, {"ok": True, "focused": True})
            return
        if parsed.path == "/download":
            qs = parse_qs(parsed.query)
            url = (qs.get("url") or [""])[0].strip()
            auto = _as_bool((qs.get("auto") or qs.get("auto_start") or ["1"])[0], True)
            audio, audio_format = _split_audio(
                (qs.get("audio") or qs.get("audio_only") or [None])[0],
                (qs.get("audio_format") or qs.get("afmt") or [None])[0],
                url,
            )
            quality = _normalize_quality((qs.get("quality") or qs.get("q") or ["best"])[0])
            cookies = (qs.get("cookies") or [""])[0]
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio, quality, cookies, audio_format)
            self._json(200, {"ok": True, "queued": True})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        data = self._read_json_body()

        if parsed.path == "/download":
            url = str(data.get("url") or "").strip()
            if not url:
                qs = parse_qs(parsed.query)
                url = (qs.get("url") or [""])[0].strip()
            auto = _as_bool(data.get("auto_start", data.get("auto", True)), True)
            audio, audio_format = _split_audio(
                data.get("audio_only", data.get("audio")),
                data.get("audio_format", data.get("afmt")),
                url,
            )
            quality = _normalize_quality(data.get("quality") or data.get("q") or "best")
            cookies = str(data.get("cookies") or "")
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio, quality, cookies, audio_format)
            ext_id = str(data.get("extension_id") or "").strip()
            if ext_id:
                register_native_host([ext_id])
            self._json(200, {"ok": True, "queued": True})
            return

        if parsed.path == "/update-extension":
            cb = _BRIDGE_CALLBACKS.get("on_update_extension")
            if cb is None:
                self._json(503, {"ok": False, "error": "updater unavailable"})
                return
            try:
                result = cb(str(data.get("url") or "") or None)
                self._json(200, result if isinstance(result, dict) else {"ok": True})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/update-app":
            cb = _BRIDGE_CALLBACKS.get("on_update_app")
            if cb is None:
                self._json(503, {"ok": False, "error": "updater unavailable"})
                return
            try:
                result = cb(str(data.get("url") or "") or None)
                self._json(200, result if isinstance(result, dict) else {"ok": True})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/apply-updates":
            cb = _BRIDGE_CALLBACKS.get("on_apply_updates")
            if cb is None:
                self._json(503, {"ok": False, "error": "updater unavailable"})
                return
            try:
                result = cb()
                self._json(200, result if isinstance(result, dict) else {"ok": True})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return

        self._json(404, {"ok": False, "error": "not found"})


def try_focus() -> bool:
    try:
        with urlopen(f"{BRIDGE_BASE}/focus", timeout=0.8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(data.get("ok"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def start_bridge(
    on_url: UrlHandler,
    on_focus: Callable[[], None] | None = None,
    *,
    on_check_update: Callable[[], dict] | None = None,
    on_update_extension: Callable[[str | None], dict] | None = None,
    on_update_app: Callable[[str | None], dict] | None = None,
    on_apply_updates: Callable[[], dict] | None = None,
) -> ThreadingHTTPServer | None:
    _BRIDGE_CALLBACKS["on_url"] = on_url
    _BRIDGE_CALLBACKS["on_focus"] = on_focus
    _BRIDGE_CALLBACKS["on_check_update"] = on_check_update
    _BRIDGE_CALLBACKS["on_update_extension"] = on_update_extension
    _BRIDGE_CALLBACKS["on_update_app"] = on_update_app
    _BRIDGE_CALLBACKS["on_apply_updates"] = on_apply_updates
    try:
        server = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), _BridgeHandler)
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, name="TubeSaveBridge", daemon=True)
    thread.start()
    return server


def extension_dir() -> Path:
    if getattr(sys, "frozen", False):
        return user_data_dir() / "browser-extension"
    return Path(__file__).resolve().parent / "browser-extension"
