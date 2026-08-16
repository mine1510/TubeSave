@echo off
cd /d "%~dp0"
if exist "dist\TubeSave.exe" (
  start "" "dist\TubeSave.exe"
) else (
  echo Сначала соберите приложение: build.bat
  pause
)
