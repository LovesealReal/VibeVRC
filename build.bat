@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
if not exist .venv\custom-bootloader call bootloader.bat
if not exist .venv\custom-bootloader .venv\Scripts\python -m pip install -q pyinstaller || exit /b 1
.venv\Scripts\python -m pip install -q -r requirements.txt || exit /b 1
.venv\Scripts\python version_info.py || exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --name VibeVRC --icon icon.ico ^
  --version-file build\version_info.txt ^
  --add-data "vibevrc\ui.html;." --add-binary "intiface\intiface-engine.exe;." ^
  --distpath dist --workpath build\work main.py || exit /b 1
