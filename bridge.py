"""Local bridge for browser extension + single-instance URL handoff."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 17834
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

UrlHandler = Callable[[str, bool, bool], None]


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


def parse_incoming_arg(raw: str) -> tuple[str | None, bool, bool]:
    """
    Parse CLI / protocol argument into (url, auto_start, audio_only).
    Supports:
      https://...
      tubesave://download?url=...&audio=1
      tubesave://add?url=...&auto=0
      tubesave://https://...
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return None, True, False

    lower = text.lower()
    if lower.startswith("tubesave:"):
        rest = text.split(":", 1)[1]
        if rest.startswith("//"):
            rest = rest[2:]
        if rest.lower().startswith(("http://", "https://")):
            audio = "music.yandex." in rest.lower()
            return rest, True, audio
        query = rest.split("?", 1)[1] if "?" in rest else ""
        qs = parse_qs(query)
        url = (qs.get("url") or [None])[0]
        if not url:
            return None, True, False
        url = unquote(url)
        auto = _as_bool((qs.get("auto") or qs.get("auto_start") or ["1"])[0], True)
        audio = _as_bool(
            (qs.get("audio") or qs.get("audio_only") or [None])[0],
            default="music.yandex." in url.lower(),
        )
        return url, auto, audio

    if lower.startswith("http://") or lower.startswith("https://"):
        return text, True, "music.yandex." in lower

    return None, True, False


def collect_launch_urls(argv: list[str] | None = None) -> list[tuple[str, bool, bool]]:
    args = list(sys.argv[1:] if argv is None else argv)
    found: list[tuple[str, bool, bool]] = []
    for arg in args:
        url, auto, audio = parse_incoming_arg(arg)
        if url:
            found.append((url, auto, audio))
    return found


def try_handoff(url: str, auto_start: bool = True, audio_only: bool = False) -> bool:
    """Send URL to an already running TubeSave. Returns True on success."""
    payload = json.dumps(
        {"url": url, "auto_start": auto_start, "audio_only": audio_only}
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
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio)
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
            if not url:
                self._json(400, {"ok": False, "error": "missing url"})
                return
            cb = _BRIDGE_CALLBACKS.get("on_url")
            if cb is not None:
                cb(url, auto, audio)
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
) -> ThreadingHTTPServer | None:
    _BRIDGE_CALLBACKS["on_url"] = on_url
    _BRIDGE_CALLBACKS["on_focus"] = on_focus
    _BRIDGE_CALLBACKS["on_check_update"] = on_check_update
    _BRIDGE_CALLBACKS["on_update_extension"] = on_update_extension
    _BRIDGE_CALLBACKS["on_update_app"] = on_update_app
    try:
        server = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), _BridgeHandler)
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, name="TubeSaveBridge", daemon=True)
    thread.start()
    return server


def extension_dir() -> Path:
    return app_root() / "browser-extension"
