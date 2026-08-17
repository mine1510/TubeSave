"""Check GitHub for newer TubeSave / extension builds and apply updates."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bridge import app_root, extension_dir, user_data_dir
from version import (
    APP_VERSION,
    APP_ZIP_NAME,
    EXTENSION_VERSION,
    EXTENSION_ZIP_NAME,
    GITHUB_RELEASES_LATEST,
    RELEASES_PAGE,
    UPDATE_JSON_URL,
    UPDATE_JSON_URL_FALLBACK,
)


StatusFn = Callable[[str], None]


@dataclass
class UpdateInfo:
    app_version: str
    extension_version: str
    app_zip_url: str | None
    extension_zip_url: str | None
    notes: str
    release_page: str

    @property
    def app_update_available(self) -> bool:
        return _is_newer(self.app_version, APP_VERSION)

    @property
    def extension_update_available(self) -> bool:
        return _is_newer(self.extension_version, EXTENSION_VERSION)


def _parse_version(text: str) -> tuple[int, ...]:
    cleaned = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def _is_newer(remote: str, local: str) -> bool:
    try:
        return _parse_version(remote) > _parse_version(local)
    except Exception:
        return False


def _http_get_json(url: str, timeout: float = 12.0) -> dict:
    req = Request(
        url,
        headers={
            "User-Agent": f"TubeSave/{APP_VERSION}",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _http_download(url: str, dest: Path, status: StatusFn | None = None) -> None:
    req = Request(url, headers={"User-Agent": f"TubeSave/{APP_VERSION}"})
    with urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if status and total > 0:
                status(f"Скачивание обновления… {done * 100 // total}%")


def _asset_url(assets: list[dict], name: str) -> str | None:
    for asset in assets:
        if str(asset.get("name") or "") == name:
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def _info_from_update_json(data: dict) -> UpdateInfo:
    app_ver = str(data.get("app") or data.get("app_version") or APP_VERSION)
    ext_ver = str(
        data.get("extension") or data.get("extension_version") or EXTENSION_VERSION
    )
    return UpdateInfo(
        app_version=app_ver.lstrip("vV"),
        extension_version=ext_ver.lstrip("vV"),
        app_zip_url=data.get("app_zip") or data.get("app_download_url"),
        extension_zip_url=data.get("extension_zip") or data.get("extension_download_url"),
        notes=str(data.get("notes") or data.get("release_notes") or ""),
        release_page=str(data.get("release_page") or RELEASES_PAGE),
    )


def fetch_update_info() -> UpdateInfo:
    """Read all version sources and keep the newest app version."""
    candidates: list[UpdateInfo] = []

    for url in (UPDATE_JSON_URL, UPDATE_JSON_URL_FALLBACK):
        try:
            data = _http_get_json(url)
            if data.get("app") or data.get("app_version"):
                candidates.append(_info_from_update_json(data))
        except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
            continue

    try:
        data = _http_get_json(GITHUB_RELEASES_LATEST)
        tag = str(data.get("tag_name") or "").lstrip("vV")
        if tag:
            assets = data.get("assets") if isinstance(data.get("assets"), list) else []
            candidates.append(
                UpdateInfo(
                    app_version=tag,
                    extension_version=tag,
                    app_zip_url=_asset_url(assets, APP_ZIP_NAME),
                    extension_zip_url=_asset_url(assets, EXTENSION_ZIP_NAME),
                    notes=str(data.get("body") or "")[:500],
                    release_page=str(data.get("html_url") or RELEASES_PAGE),
                )
            )
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        pass

    if not candidates:
        return UpdateInfo(
            app_version=APP_VERSION,
            extension_version=EXTENSION_VERSION,
            app_zip_url=None,
            extension_zip_url=None,
            notes="",
            release_page=RELEASES_PAGE,
        )

    return max(candidates, key=lambda item: _parse_version(item.app_version))


def install_extension_update(
    zip_url: str | None = None,
    status: StatusFn | None = None,
) -> Path:
    """Download extension zip and extract into browser-extension folder."""
    info = fetch_update_info() if not zip_url else None
    url = zip_url or (info.extension_zip_url if info else None)
    if not url:
        raise RuntimeError(
            "Нет ссылки на архив расширения. Загрузите TubeSave-Extension.zip в Release."
        )

    target = extension_dir()
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tubesave-ext-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "extension.zip"
        if status:
            status("Скачивание расширения…")
        _http_download(url, archive, status)
        extract_to = tmp_path / "extracted"
        extract_to.mkdir()
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(extract_to)

        # Zip may contain browser-extension/ root or flat files.
        source = extract_to
        nested = extract_to / "browser-extension"
        if nested.is_dir() and (nested / "manifest.json").exists():
            source = nested
        elif not (extract_to / "manifest.json").exists():
            # Single top-level folder
            kids = [p for p in extract_to.iterdir() if p.is_dir()]
            if len(kids) == 1 and (kids[0] / "manifest.json").exists():
                source = kids[0]

        if not (source / "manifest.json").exists():
            raise RuntimeError("В архиве расширения нет manifest.json")

        if status:
            status("Установка расширения…")
        # Replace files carefully: clear then copy
        if target.exists():
            for child in target.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)

    return target


def install_app_update(
    zip_url: str | None = None,
    status: StatusFn | None = None,
) -> None:
    """
    Download app zip and schedule replace-on-restart via a helper .bat.
    Quits the current process after launching the updater script.
    """
    info = fetch_update_info() if not zip_url else None
    url = zip_url or (info.app_zip_url if info else None)
    if not url:
        raise RuntimeError(
            "Нет ссылки на архив приложения. Загрузите TubeSave-Windows.zip в Release."
        )

    root = app_root()
    exe_name = "TubeSave.exe"
    current_exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None

    with tempfile.TemporaryDirectory(prefix="tubesave-app-") as tmp:
        # Keep staging outside TemporaryDirectory deletion — copy to persistent folder.
        pass

    staging = Path(tempfile.gettempdir()) / "TubeSaveUpdateStaging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    archive = staging / "app.zip"
    if status:
        status("Скачивание TubeSave…")
    _http_download(url, archive, status)

    extract_to = staging / "extracted"
    extract_to.mkdir()
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(extract_to)

    # Locate new exe inside archive
    candidates = list(extract_to.rglob(exe_name))
    if not candidates:
        # Dev-friendly: maybe only sources; still copy browser-extension if present
        raise RuntimeError(f"В архиве не найден {exe_name}")
    new_exe = candidates[0]
    new_root = new_exe.parent
    new_ext = new_root / "browser-extension"
    if not new_ext.exists():
        nested = extract_to / "browser-extension"
        if nested.exists():
            new_ext = nested

    target_exe = (current_exe if current_exe and current_exe.name.lower() == exe_name.lower() else root / exe_name)
    target_dir = target_exe.parent

    bat = staging / "apply_update.bat"
    # Escape paths for batch
    def q(path: Path) -> str:
        return str(path).replace('"', "")

    old_pid = os.getpid()
    lines = [
        "@echo off",
        "setlocal",
        "set n=0",
        f"set OLD_PID={old_pid}",
        "timeout /t 2 /nobreak >nul",
        ":wait_old",
        'tasklist /FI "PID eq %OLD_PID%" | find "%OLD_PID%" >nul',
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  set /a n+=1",
        "  if %n% lss 30 goto wait_old",
        ")",
        "set n=0",
        ":copy_exe",
        f'copy /Y "{q(new_exe)}" "{q(target_exe)}" >nul',
        "if errorlevel 1 (",
        "  set /a n+=1",
        "  if %n% geq 15 goto copy_fail",
        "  timeout /t 1 /nobreak >nul",
        "  goto copy_exe",
        ")",
    ]
    data_dir = user_data_dir()
    dest_ext = extension_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    dest_ext.mkdir(parents=True, exist_ok=True)
    if new_ext.exists():
        lines.append(
            f'xcopy /E /I /Y "{q(new_ext)}" "{q(dest_ext)}\\" >nul'
        )
    if target_dir.resolve() != data_dir.resolve():
        lines.extend(
            [
                f'del /Q "{q(target_dir / "tubesave-native-host.bat")}" >nul 2>&1',
                f'del /Q "{q(target_dir / "com.tubesave.host.json")}" >nul 2>&1',
                f'del /Q "{q(target_dir / "native-extension-ids.json")}" >nul 2>&1',
                f'if exist "{q(target_dir / "browser-extension")}" rmdir /S /Q "{q(target_dir / "browser-extension")}" >nul 2>&1',
            ]
        )
    lines.extend(
        [
            "for /f \"tokens=1 delims==\" %%V in ('set _MEI 2^>nul') do set \"%%V=\"",
            "set _MEIPASS=",
            "set PYTHONHOME=",
            "set PYTHONPATH=",
            "set TCL_LIBRARY=",
            "set TK_LIBRARY=",
            "set TCLLIBPATH=",
            "timeout /t 2 /nobreak >nul",
            f'cd /d "{q(data_dir)}"',
            f'start "" /D "{q(data_dir)}" "{q(target_exe)}"',
            f'rmdir /S /Q "{q(staging)}" >nul 2>&1',
            "endlocal",
            "exit /b 0",
            ":copy_fail",
            "endlocal",
            "exit /b 1",
        ]
    )
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

    if status:
        status("Перезапуск для установки обновления…")

    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("_MEI")
        and key not in {"PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH"}
    }
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=flags,
        cwd=str(target_dir),
        env=clean_env,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_releases_page() -> None:
    import webbrowser

    webbrowser.open(RELEASES_PAGE)
