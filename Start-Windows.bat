@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run: powershell -ExecutionPolicy Bypass -File Install-Windows.ps1
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py init
start "" ".venv\Scripts\pythonw.exe" launcher.py
