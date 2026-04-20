@echo off
setlocal

cd /d "%~dp0"

echo.
echo Hextra Bootstrapper Nuitka Build
echo ================================
echo.

where nuitka >nul 2>nul
if errorlevel 1 (
    echo Nuitka wurde nicht gefunden. Installiere Build-Abhaengigkeiten...
    python -m pip install nuitka ordered-set zstandard
    if errorlevel 1 (
        echo.
        echo Installation der Build-Abhaengigkeiten ist fehlgeschlagen.
        pause
        exit /b 1
    )
)

if exist "build-bootstrapper" (
    echo Entferne alten build-bootstrapper Ordner...
    rmdir /s /q "build-bootstrapper"
)

if exist "dist-bootstrapper" (
    echo Entferne alten dist-bootstrapper Ordner...
    rmdir /s /q "dist-bootstrapper"
)

echo.
echo Baue HextraBootstrapper.exe...
echo.

nuitka --onefile --standalone --enable-plugin=tk-inter --windows-console-mode=disable --windows-icon-from-ico="%~dp0hextra.ico" --output-dir="%~dp0dist-bootstrapper" --output-filename=HextraBootstrapper.exe --remove-output "%~dp0HextraBootstrapper.py"

if errorlevel 1 (
    echo.
    echo Build fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo Build erfolgreich abgeschlossen.
echo Ausgabe:
echo %~dp0dist-bootstrapper\HextraBootstrapper.exe
echo.
pause
exit /b 0
