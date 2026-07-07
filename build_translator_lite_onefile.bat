@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist .build-venv rmdir /s /q .build-venv

py -3.12 -m venv .build-venv
if errorlevel 1 goto :failed

call .build-venv\Scripts\activate.bat
if errorlevel 1 goto :failed

python -m pip install --upgrade pip
if errorlevel 1 goto :failed

python -m pip install --no-cache-dir "PyInstaller>=6.0" PySide6-Essentials
if errorlevel 1 goto :failed

python -m pip show PySide6 PySide6-Addons PySide6-Essentials shiboken6

if exist build\dist rmdir /s /q build\dist
if exist build\work rmdir /s /q build\work
if exist build\spec rmdir /s /q build\spec
if exist build\release rmdir /s /q build\release

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name TheGuild2Translator ^
  --icon "%CD%\assets\app-icon.ico" ^
  --distpath build\dist ^
  --workpath build\work ^
  --specpath build\spec ^
  --add-data "%CD%\encoder\guild2_codec.py;encoder" ^
  --add-data "%CD%\encoder\data;encoder\data" ^
  --add-data "%CD%\assets\app-icon.ico;assets" ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtSvg ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  translator_tool_launcher.py

if errorlevel 1 goto :failed

mkdir build\release
copy /Y build\dist\TheGuild2Translator.exe build\release\TheGuild2Translator.exe
if errorlevel 8 goto :failed

if exist build\TheGuild2Translator.zip del /f /q build\TheGuild2Translator.zip

powershell -NoProfile -Command "Compress-Archive -Path 'build\release\TheGuild2Translator.exe' -DestinationPath 'build\TheGuild2Translator.zip' -Force"
if errorlevel 1 goto :failed

echo.
echo Build complete:
echo   build\release\TheGuild2Translator\
echo   build\TheGuild2Translator-distributable.zip
exit /b 0

:failed
echo.
echo Build failed. See the messages above.
exit /b 1
