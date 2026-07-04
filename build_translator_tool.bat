@echo off
setlocal EnableExtensions
cd /d "%~dp0"

py -3.12 -m pip install "PyInstaller>=6.0"
if errorlevel 1 goto :failed

if exist build\dist rmdir /s /q build\dist
if exist build\work rmdir /s /q build\work
if exist build\spec rmdir /s /q build\spec
if exist build\release rmdir /s /q build\release

py -3.12 -m PyInstaller --noconfirm --clean --windowed --onedir --name TheGuild2Translator --distpath build\dist --workpath build\work --specpath build\spec --add-data "%CD%\encoder\guild2_codec.py;encoder" --add-data "%CD%\encoder\data;encoder\data" translator_tool_launcher.py
if errorlevel 1 goto :failed

mkdir build\release\TheGuild2Translator
robocopy build\dist\TheGuild2Translator build\release\TheGuild2Translator /E
if errorlevel 8 goto :failed

if exist build\TheGuild2Translator-distributable.zip del /f /q build\TheGuild2Translator-distributable.zip
powershell -NoProfile -Command "Compress-Archive -Path 'build\release\TheGuild2Translator\*' -DestinationPath 'build\TheGuild2Translator-distributable.zip' -Force"
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
