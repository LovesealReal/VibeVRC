@echo off
cd /d "%~dp0"
if not exist .venv py -3.11 -m venv .venv 2>nul
if not exist .venv python -m venv .venv
if not exist .venv\Scripts\python.exe (echo Couldn't make the Python environment in .venv & exit /b 1)
.venv\Scripts\python -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))" || (echo VibeVRC needs Python 3.11. Install it, delete the .venv folder and run this again & exit /b 1)
if not exist .venv\custom-bootloader call bootloader.bat
if not exist .venv\custom-bootloader .venv\Scripts\python -m pip install -q pyinstaller==6.22.3 || exit /b 1
.venv\Scripts\python -m pip install -q -r requirements.txt || exit /b 1
.venv\Scripts\python version_info.py || exit /b 1
.venv\Scripts\python notices.py || exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name VibeVRC --icon icon.ico ^
  --version-file build\version_info.txt ^
  --exclude-module setuptools --exclude-module distutils --exclude-module pydoc ^
  --add-data "vibevrc\ui.html;." --add-data "build\THIRD-PARTY-NOTICES.txt;." --add-binary "intiface\intiface-engine.exe;." ^
  --distpath dist --workpath build\work main.py || exit /b 1
copy /y build\THIRD-PARTY-NOTICES.txt dist\ >nul
