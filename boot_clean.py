"""Relaunch a frozen build if it inherited a dying PyInstaller extract folder.

Must run before ``import tkinter``: the C module ``_tkinter`` lives in ``_MEIPASS``,
and a stale folder from the previous process is already being deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_STRIP_ENV = {
    "PYTHONHOME",
    "PYTHONPATH",
    "TCL_LIBRARY",
    "TK_LIBRARY",
    "TCLLIBPATH",
}


def _mei_root() -> Path | None:
    mei = getattr(sys, "_MEIPASS", "") or os.environ.get("_MEIPASS", "")
    return Path(mei) if mei else None


def _has_binary(root: Path, prefix: str) -> bool:
    for path in root.glob(prefix + "*"):
        if path.suffix.lower() in {".pyd", ".dll", ".so"} and path.is_file():
            try:
                if path.stat().st_size > 0:
                    return True
            except OSError:
                return False
    return False


def extract_is_broken() -> bool:
    if os.environ.get("TUBESAVE_CLEAN_START") == "1":
        return False
    if not getattr(sys, "frozen", False):
        return False
    root = _mei_root()
    if root is None or not root.is_dir():
        return True
    if not _has_binary(root, "_tkinter"):
        return True
    for key in ("TCL_LIBRARY", "TK_LIBRARY"):
        raw = os.environ.get(key) or ""
        if not raw:
            continue
        first = Path(raw.split(os.pathsep)[0])
        if not first.exists():
            return True
    return False


def _clean_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("_MEI") and key not in _STRIP_ENV
    }
    env["TUBESAVE_CLEAN_START"] = "1"
    return env


def _runtime_cwd() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    path = Path(base) / "TubeSave" if base else Path.home() / "AppData" / "Local" / "TubeSave"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_fresh_extract() -> bool:
    """Restart this exe with a clean env. True if this process should exit."""
    if not extract_is_broken():
        return False
    flags = 0
    if sys.platform == "win32":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        )
    subprocess.Popen(
        [sys.executable, *sys.argv[1:]],
        cwd=str(_runtime_cwd()),
        env=_clean_env(),
        close_fds=True,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True
