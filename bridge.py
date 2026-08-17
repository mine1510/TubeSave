"""Local bridge for browser extension + single-instance URL handoff."""

from __future__ import annotations

import ctypes
import json
import os
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

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 17834
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
NATIVE_HOST_NAME = "com.tubesave.host"
PINNED_EXTENSION_ID = "hmddmgmenbnhoeghphinmmnoeklgbhgg"
INSTANCE_MUTEX_NAME = "Local\\TubeSave.mine1510.single"
_INSTANCE_MUTEX_HANDLE = None

UrlHandler = Callable[[str, bool, bool, str], None]


KNOWN_QUALITIES = {"best", "2160", "1440", "1080", "720", "480", "360"}


def _normalize_quality(value: object) -> str:
    text = str(value or "best").strip().lower().rstrip("p")
    if text in {"max", "highest", "best"}:
        return "best"
    if text in KNOWN_QUALITIES:
        return text
    return "best"


def _as_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() not in {"0", "false", "no"}


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        # Prefer extension shipped next to the exe (user-installable folder).
        if (beside / "browser-extension" / "manifest.json").exists():
            return beside
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "browser-extension" / "manifest.json").exists():
            return Path(meipass)
        return beside
    return Path(__file__).resolve().parent


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
    return app_root() / "tubesave-native-host.bat"


def _native_host_manifest_path() -> Path:
    return app_root() / f"{NATIVE_HOST_NAME}.json"


def _write_native_host_launcher() -> Path:
    root = app_root()
    bat = _native_host_bat_path()
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
    return app_root() / "native-extension-ids.json"


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
    _extra_ids_path().write_text(
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
        extra = [protocol]
    else:
        extra = []
    if getattr(sys, "frozen", False):
        args = [str(Path(sys.executable).resolve()), *extra]
        cwd = str(Path(sys.executable).resolve().parent)
    else:
        script = (Path(__file__).resolve().parent / "app.py").resolve()
        args = [str(Path(sys.executable).resolve()), str(script), *extra]
        cwd = str(script.parent)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        args,
        cwd=cwd,
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
    audio = _as_bool(data.get("audio", data.get("audio_only")), "music.yandex." in url.lower())
    quality = _normalize_quality(data.get("quality") or "best")
    if action == "update":
        if is_bridge_alive():
            payload = {"ok": True, "alive": True, "apply": try_apply_updates()}
        else:
            launch_app_detached(extra_args=["tubesave://update"])
            payload = {"ok": True, "launched": True, "action": "update"}
    elif is_bridge_alive():
        ok = try_handoff(url, auto, audio, quality) if url else try_focus()
        payload = {"ok": True, "alive": True, "handed": bool(ok)}
    else:
        launch_app_detached(url, auto, audio, quality)
        payload = {"ok": True, "launched": True}

    raw = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(raw)))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def parse_incoming_arg(raw: str) -> tuple[str | None, bool, bool, str]:
    """
    Parse CLI / protocol argument into (url, auto_start, audio_only, quality).
    Supports:
      https://...
      tubesave://download?url=...&audio=1&quality=1080
      tubesave://add?url=...&auto=0
      tubesave://https://...
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return None, True, False, "best"

    lower = text.lower()
    if lower.startswith("tubesave:"):
        rest = text.split(":", 1)[1]
        if rest.startswith("//"):
            rest = rest[2:]
        if rest.lower().startswith(("http://", "https://")):
            audio = "music.yandex." in rest.lower()
            return rest, True, audio, "best"
        query = rest.split("?", 1)[1] if "?" in rest else ""
        qs = parse_qs(query)
        url = (qs.get("url") or [None])[0]
        if not url:
            return None, True, False, "best"
        url = unquote(url)
        auto = _as_bool((qs.get("auto") or qs.get("auto_start") or ["1"])[0], True)
        audio = _as_bool(
            (qs.get("audio") or qs.get("audio_only") or [None])[0],
            default="music.yandex." in url.lower(),
        )
        quality = _normalize_quality((qs.get("quality") or qs.get("q") or ["best"])[0])
        return url, auto, audio, quality

    if lower.startswith("http://") or lower.startswith("https://"):
        return text, True, "music.yandex." in lower, "best"

    return None, True, False, "best"


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


def collect_launch_urls(argv: list[str] | None = None) -> list[tuple[str, bool, bool, str]]:
    args = list(sys.argv[1:] if argv is None else argv)
    found: list[tuple[str, bool, bool, str]] = []
    for arg in args:
        url, auto, audio, quality = parse_incoming_arg(arg)
        if url:
            found.append((url, auto, audio, quality))
    return found


def try_handoff(
    url: str,
    auto_start: bool = True,
    audio_only: bool = False,
    quality: str = "best",
) -> bool:
    """Send URL to an already running TubeSave. Returns True on success."""
    payload = json.dumps(
        {
            "url": url,
            "auto_start": auto_start,
            "audio_only": audio_only,
            "quality": _normalize_quality(quality),
        }
    ).encode("utf-8")
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
            audio = _as_bool(
                (qs.get("audio") or qs.get("audio_only") or [None])[0],
                default="music.yandex." in url.lower(),
            )
            quality = _normalize_quality((qs.get("quality") or qs.get("q") or ["best"])[0])
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio, quality)
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
            audio_default = "music.yandex." in url.lower()
            audio = _as_bool(data.get("audio_only", data.get("audio")), audio_default)
            quality = _normalize_quality(data.get("quality") or data.get("q") or "best")
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio, quality)
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
    return app_root() / "browser-extension"
