"""Shared version constants for TubeSave app and updater."""

from __future__ import annotations

APP_VERSION = "1.2.0"
EXTENSION_VERSION = "1.2.0"

GITHUB_OWNER = "mine1510"
GITHUB_REPO = "TubeSave"
GITHUB_RELEASES_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
UPDATE_JSON_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/master/update.json"
)
# Fallback branch used by the feature branch until merged to master.
UPDATE_JSON_URL_FALLBACK = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
    "cursor/quality-audio-theme-ui/update.json"
)
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APP_ZIP_NAME = "TubeSave-Windows.zip"
EXTENSION_ZIP_NAME = "TubeSave-Extension.zip"
