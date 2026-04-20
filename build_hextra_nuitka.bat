@echo off
setlocal

cd /d "%~dp0"

echo.
echo Hextra Nuitka Build
echo ===================
echo.
echo Reminder: bump VERSION in hextra\legacy.py before building a new release.
echo.

where nuitka >nul 2>nul
if errorlevel 1 (
    echo Nuitka was not found. Installing required build packages...
    python -m pip install nuitka ordered-set zstandard
    if errorlevel 1 (
        echo.
        echo Failed to install Nuitka build dependencies.
        pause
        exit /b 1
    )
)

if exist "build-nuitka" (
    echo Removing old build-nuitka folder...
    rmdir /s /q "build-nuitka"
)

if exist "dist-nuitka" (
    echo Removing old dist-nuitka folder...
    rmdir /s /q "dist-nuitka"
)

echo.
echo Building Hextra.exe with Nuitka...
echo.

nuitka --onefile --standalone --enable-plugin=pyqt6 --windows-console-mode=disable --windows-icon-from-ico="%~dp0hextra.ico" --output-dir="%~dp0dist-nuitka" --output-filename=Hextra.exe --remove-output "%~dp0Hexa.py"

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build finished successfully.
echo Output:
echo %~dp0dist-nuitka\Hextra.exe
echo.
pause
exit /b 0
