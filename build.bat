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
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean TubeSave.spec
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo Copying browser extension...
if exist "dist\browser-extension" rmdir /S /Q "dist\browser-extension"
xcopy /E /I /Y "browser-extension" "dist\browser-extension\" >nul

echo Packing extension zip...
if not exist "download" mkdir "download"
if exist "download\TubeSave-Extension.zip" del /F /Q "download\TubeSave-Extension.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'browser-extension\*' -DestinationPath 'download\TubeSave-Extension.zip' -Force"

echo Packing Windows zip...
if exist "download\TubeSave-Windows.zip" del /F /Q "download\TubeSave-Windows.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\TubeSave.exe','dist\browser-extension' -DestinationPath 'download\TubeSave-Windows.zip' -Force"

echo.
echo Ready: "%~dp0dist\TubeSave.exe"
echo Zips:  "%~dp0download\TubeSave-Windows.zip"
echo        "%~dp0download\TubeSave-Extension.zip"
echo Double-click TubeSave.exe to launch.
pause
