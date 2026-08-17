# PyInstaller runtime hook: runs before app.py, still after the bootloader extract.
import boot_clean

if boot_clean.ensure_fresh_extract():
    raise SystemExit(0)
