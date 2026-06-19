@echo off
setlocal EnableExtensions
cd /d "%~dp0"

py -3.12 -m pip install "PyInstaller>=6.0"
if errorlevel 1 goto :failed

if exist build\package-data rmdir /s /q build\package-data
mkdir build\package-data\languages
mkdir build\package-data\encoder

robocopy languages build\package-data\languages /E /XD .git __pycache__ /XF *.pyc
if errorlevel 8 goto :failed
robocopy encoder build\package-data\encoder /E /XD .git __pycache__ /XF *.pyc
if errorlevel 8 goto :failed

py -3.12 -m PyInstaller --noconfirm --clean --windowed --onedir --name TheGuild2Translator --distpath build\dist --workpath build\work --specpath build\spec --add-data "%CD%\build\package-data\languages;languages" --add-data "%CD%\build\package-data\encoder;encoder" --add-data "%CD%\Translation-Kit.txt;." translator_tool_launcher.py
if errorlevel 1 goto :failed

echo.
echo Build complete: build\dist\TheGuild2Translator\TheGuild2Translator.exe
pause
exit /b 0

:failed
echo.
echo Build failed. See the messages above.
pause
exit /b 1
