@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSDIR=%%i
if not defined VSDIR (echo Visual Studio Build Tools not found, using the normal PyInstaller launcher & exit /b 0)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist .pyi mkdir .pyi
cd .pyi
if not exist pyinstaller-6.22.3 (
  ..\.venv\Scripts\python -m pip download pyinstaller==6.22.3 --no-binary :all: --no-deps -q -d . || exit /b 1
  tar -xzf pyinstaller-6.22.3.tar.gz || exit /b 1
)
cd pyinstaller-6.22.3\bootloader
..\..\..\.venv\Scripts\python waf distclean all --target-arch=64bit --msvc_targets=x64 || exit /b 1
cd ..
..\..\.venv\Scripts\python -m pip install -q --force-reinstall --no-build-isolation . || exit /b 1
cd ..\..
echo done> .venv\custom-bootloader
