@echo off
if exist "%~dp0dist\DateFix\DateFix.exe" (
  start "" "%~dp0dist\DateFix\DateFix.exe"
  exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  set "PYTHONPATH=%~dp0src"
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m datefix gui
  exit /b
)
echo DateFix is not installed yet. Follow README.md to set up the desktop app.
pause
