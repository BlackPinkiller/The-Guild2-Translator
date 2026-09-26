@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "REBUILD_CHOICE="
set /p "REBUILD_CHOICE=Rebuild? [Y/n]: "
if /I "%REBUILD_CHOICE%"=="n" goto :build_skipped
ver >nul

if not exist ".venv\Scripts\python.exe" (
  set "BOOTSTRAP_PYTHON=py -3.12"
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "BOOTSTRAP_PYTHON="%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe""
  !BOOTSTRAP_PYTHON! -m venv .venv
  if errorlevel 1 goto :failed
)

set "VENV_IS_PYTHON_312="
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(not sys.version_info[:2] == (3, 12))" >nul 2>nul && set "VENV_IS_PYTHON_312=1"
if not defined VENV_IS_PYTHON_312 (
  echo The local virtual environment must use Python 3.12.
  goto :failed
)

".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

".venv\Scripts\python.exe" -m pip show PyInstaller PySide6-Essentials shiboken6

if exist build\dist rmdir /s /q build\dist
if exist build\work rmdir /s /q build\work
if exist build\spec rmdir /s /q build\spec
if exist build\release rmdir /s /q build\release

".venv\Scripts\python.exe" -m PyInstaller ^
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
  --add-data "%CD%\assets\interface;assets\interface" ^
  --add-data "%CD%\assets\guide_preview;assets\guide_preview" ^
  --add-data "%CD%\assets\preview_ui;assets\preview_ui" ^
  --add-data "%CD%\assets\preview_presets;assets\preview_presets" ^
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
if errorlevel 1 goto :failed

if exist build\TheGuild2Translator.zip del /f /q build\TheGuild2Translator.zip

powershell -NoProfile -Command "Compress-Archive -Path 'build\release\TheGuild2Translator.exe' -DestinationPath 'build\TheGuild2Translator.zip' -Force"
if errorlevel 1 goto :failed

echo.
echo Build complete:
echo   build\release\TheGuild2Translator.exe
echo   build\TheGuild2Translator.zip
goto :publish_prompt

:build_skipped
if not exist build\TheGuild2Translator.zip (
  echo Existing build\TheGuild2Translator.zip was not found.
  goto :failed
)

echo.
echo Build skipped:
echo   build\TheGuild2Translator.zip

:publish_prompt
echo.
set "PUBLISH_CHOICE="
set /p "PUBLISH_CHOICE=Publish to GitHub? [y/N]: "
if /I not "%PUBLISH_CHOICE%"=="y" exit /b 0

set "GH_CLI=gh"
where gh >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\GitHub CLI\gh.exe" (
    set "GH_CLI=C:\Program Files\GitHub CLI\gh.exe"
  ) else (
    echo GitHub CLI was not found.
    goto :failed
  )
)

for /f %%I in ('git rev-parse HEAD') do set "COMMIT_SHA=%%I"
if not defined COMMIT_SHA goto :failed

for /f %%I in ('git rev-parse --short HEAD') do set "SHORT_SHA=%%I"
if not defined SHORT_SHA goto :failed

for /f "delims=" %%I in ('"%GH_CLI%" repo view --json url --jq .url') do set "REPO_URL=%%I"
if not defined REPO_URL goto :failed

git push origin HEAD
if errorlevel 1 goto :failed

"%GH_CLI%" release view "build-%SHORT_SHA%" >nul 2>nul
if errorlevel 1 (
  "%GH_CLI%" release create "build-%SHORT_SHA%" ^
    "build\TheGuild2Translator.zip#TheGuild2Translator.zip" ^
    --target "%COMMIT_SHA%" ^
    --title "TheGuild2Translator %SHORT_SHA%" ^
    --notes "%REPO_URL%/commit/%COMMIT_SHA%"
) else (
  "%GH_CLI%" release upload "build-%SHORT_SHA%" ^
    "build\TheGuild2Translator.zip#TheGuild2Translator.zip" ^
    --clobber
)
if errorlevel 1 goto :failed

echo.
echo Published:
"%GH_CLI%" release view "build-%SHORT_SHA%" --json url --jq .url
exit /b 0

:failed
echo.
echo Build failed. See the messages above.
exit /b 1
