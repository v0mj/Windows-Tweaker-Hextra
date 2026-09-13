@echo off
REM =====================================================================
REM  Hextra - PyInstaller build for Windows
REM  Faster alternative to build_hextra_nuitka.bat.
REM  Produces one self-contained executable: dist\Hextra.exe
REM
REM  Options:
REM    set HEXTRA_PYTHON=py -3.11   build with a specific interpreter
REM    set HEXTRA_SKIP_PIP=1        reuse already installed dependencies
REM    set HEXTRA_ONEFILE=0         folder build in dist\Hextra\ instead
REM =====================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHON=python"
if defined HEXTRA_PYTHON set "PYTHON=%HEXTRA_PYTHON%"

echo.
echo === Hextra / PyInstaller build ===
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

if "%HEXTRA_SKIP_PIP%"=="1" goto build

echo [1/3] Installing dependencies...
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto fail
echo.

:build
echo [2/3] Bundling with PyInstaller...
%PYTHON% -m PyInstaller --noconfirm --clean packaging\hextra.spec
if errorlevel 1 goto fail

set "EXE=dist\Hextra.exe"
if "%HEXTRA_ONEFILE%"=="0" set "EXE=dist\Hextra\Hextra.exe"
if not exist "%EXE%" (
    echo [ERROR] PyInstaller finished but %EXE% is missing.
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
