"""Relaunch a frozen build if it inherited a dying PyInstaller extract folder.

Must run before ``import tkinter``: the C module ``_tkinter`` lives in ``_MEIPASS``,
and a stale folder from the previous process is already being deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def windows_detach_flags(*, hide_window: bool = False) -> int:
    """Flags so a child outlives this frozen process.

    ``hide_window=True`` is for helper cmd/powershell. Direct GUI relaunch
    uses a detached process instead — combining both flags can fail on Windows.
    """
    if sys.platform != "win32":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    if hide_window:
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        flags |= subprocess.DETACHED_PROCESS
    return flags


_STRIP_ENV = {
    "PYTHONHOME",
    "PYTHONPATH",
    "TCL_LIBRARY",
    "TK_LIBRARY",
    "TCLLIBPATH",
}


def frozen_restart_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a new top-level TubeSave.exe that must outlive this process.

    PyInstaller 6.9+ treats a spawn of the same exe as a worker that reuses the
    parent's extract. After an update the parent is cmd.exe / a replaced binary,
    which then fails with: "Security validation failure: parent process has
    different executable!".
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("_MEI", "_PYI")) and key not in _STRIP_ENV
    }
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if extra:
        env.update(extra)
    return env


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


def _runtime_cwd() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    path = Path(base) / "TubeSave" if base else Path.home() / "AppData" / "Local" / "TubeSave"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_fresh_extract() -> bool:
    """Restart this exe with a clean env. True if this process should exit."""
    if not extract_is_broken():
        return False
    flags = windows_detach_flags()
    subprocess.Popen(
        [sys.executable, *sys.argv[1:]],
        cwd=str(_runtime_cwd()),
        env=frozen_restart_env({"TUBESAVE_CLEAN_START": "1"}),
        close_fds=True,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True
