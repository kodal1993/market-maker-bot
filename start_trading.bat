@echo off
setlocal

cd /d "%~dp0"
title Market Maker Bot - Manual Start

set "CHECK_ONLY=0"
if /I "%~1"=="--check" set "CHECK_ONLY=1"

echo [market_maker_bot] Manual launcher
echo Repo: %CD%
echo.

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [INFO] .env letrehozva a .env.example alapjan.
        echo [INFO] Toltse ki a szukseges ertekeket, majd inditsa ujra.
    ) else (
        echo [ERROR] Nem talalhato .env vagy .env.example.
    )
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Nem talalhato a virtualis kornyezet: .venv\Scripts\python.exe
    echo [INFO] Hozd letre peldaul ezzel: py -m venv .venv
    goto :fail
)

".\.venv\Scripts\python.exe" "src\startup_validation.py"
if errorlevel 1 (
    echo [ERROR] A startup konfiguracio ervenytelen vagy hianyos.
    echo [INFO] Ellenorizd a fenti hibakat a .env es .env.example alapjan.
    goto :fail
)

if "%CHECK_ONLY%"=="1" (
    echo [OK] A launcher ellenorzese sikeres.
    exit /b 0
)

echo [INFO] Bot inditasa a jelenlegi .env beallitasokkal...
echo [INFO] Leallitas: Ctrl+C
echo.

".\.venv\Scripts\python.exe" "src\main.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [INFO] A bot normalisan leallt.
) else (
    echo [ERROR] A bot hibaval allt le. Exit code: %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%

:fail
if "%CHECK_ONLY%"=="1" exit /b 1
echo.
pause
exit /b 1
