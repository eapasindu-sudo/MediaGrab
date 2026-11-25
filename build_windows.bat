@echo off
REM Builds MediaGrab.exe (single file) using PyInstaller.
REM Needs Python 3.9+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH" when installing).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ & pause & exit /b 1)
python -m pip install --upgrade pip pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name MediaGrab --icon mediagrab.ico --add-data "mediagrab.ico;." mediagrab.py
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo.
echo Done! Your app is here: %~dp0dist\MediaGrab.exe
echo On first start it downloads yt-dlp and ffmpeg automatically.
pause
