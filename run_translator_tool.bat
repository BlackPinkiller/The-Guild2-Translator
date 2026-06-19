@echo off
cd /d "%~dp0"
py -3.12 -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo Installing the one-time desktop UI dependency...
  py -3.12 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies. Run: py -3.12 -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)
py -3.12 -m translator_tool.app
pause
