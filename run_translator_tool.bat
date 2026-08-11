@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  set "BOOTSTRAP_PYTHON=py -3.12"
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "BOOTSTRAP_PYTHON="%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe""
  !BOOTSTRAP_PYTHON! -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv with Python 3.12.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo Installing the one-time desktop UI dependency...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
  )
)

start "" ".venv\Scripts\pythonw.exe" -m translator_tool.app
