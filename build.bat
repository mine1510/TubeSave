@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

echo Installing / updating dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo Building TubeSave.exe ...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name TubeSave ^
  --collect-all imageio_ffmpeg ^
  --collect-all yt_dlp ^
  --hidden-import imageio_ffmpeg ^
  app.py

if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Ready: "%~dp0dist\TubeSave.exe"
echo Double-click TubeSave.exe to launch.
pause
