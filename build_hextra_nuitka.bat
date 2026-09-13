@echo off
REM =====================================================================
REM  Hextra - Nuitka build for Windows
REM  Produces one self-contained executable: dist-nuitka\Hextra.exe
REM
REM  Needs: Windows 10/11 + Python 3.11+.
REM  Nuitka uses the installed Visual Studio Build Tools, or offers to
REM  download MinGW64 (answered automatically by --assume-yes-for-downloads).
REM
REM  Options:
REM    set HEXTRA_PYTHON=py -3.11   build with a specific interpreter
REM    set HEXTRA_SKIP_PIP=1        reuse already installed dependencies
REM =====================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHON=python"
if defined HEXTRA_PYTHON set "PYTHON=%HEXTRA_PYTHON%"

echo.
echo === Hextra / Nuitka build ===
echo.

%PYTHON% -c "import sys; print('Python ' + sys.version.split()[0])"
if errorlevel 1 (
    echo [ERROR] "%PYTHON%" did not run. Install Python 3.11+ or set HEXTRA_PYTHON.
    goto fail
)

set "VERSION="
for /f "usebackq delims=" %%v in (`%PYTHON% packaging\build_info.py --field version 2^>nul`) do set "VERSION=%%v"
if not defined VERSION set "VERSION=0.0.0"
echo App version %VERSION%
echo.

if "%HEXTRA_SKIP_PIP%"=="1" goto compile

echo [1/3] Installing dependencies...
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto fail
echo.

:compile
echo [2/3] Compiling with Nuitka - this takes a few minutes...
%PYTHON% -m nuitka ^
    --standalone ^
    --onefile ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --enable-plugin=pyqt6 ^
    --output-dir=dist-nuitka ^
    --output-filename=Hextra.exe ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\hextra.ico ^
    --company-name=Hextra ^
    --product-name=Hextra ^
    --file-description="Hextra - offline Windows desktop tweaker" ^
    --file-version=%VERSION% ^
    --product-version=%VERSION% ^
    --include-package=hextra ^
    --include-package=replica_ui ^
    Hexa.py
if errorlevel 1 goto fail

set "EXE=dist-nuitka\Hextra.exe"
if not exist "%EXE%" (
    echo [ERROR] Nuitka finished but %EXE% is missing.
    goto fail
)
echo.

echo [3/3] Smoke testing the exe headlessly...
set "QT_QPA_PLATFORM=offscreen"
"%EXE%" --smoke-test
set "SMOKE=%ERRORLEVEL%"
set "QT_QPA_PLATFORM="
if not "%SMOKE%"=="0" echo [WARN] Smoke test exited with %SMOKE% - see %TEMP%\hextra_crash.log
if "%SMOKE%"=="0" echo Smoke test passed.
echo.

echo === Done ===
echo   %CD%\%EXE%
for %%F in ("%EXE%") do echo   %%~zF bytes
echo.
echo Launch with:  "%EXE%"
endlocal
exit /b 0

:fail
echo.
echo === Build failed ===
if exist "%TEMP%\hextra_crash.log" type "%TEMP%\hextra_crash.log"
endlocal
exit /b 1
