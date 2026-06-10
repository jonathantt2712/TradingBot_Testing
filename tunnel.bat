@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  tunnel.bat — keeps the ngrok tunnel to this PC alive.
REM  Started automatically by START.bat (or double-click it on its own).
REM  If the network drops, it retries every 15 seconds until it's back.
REM ─────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

REM Read NGROK_DOMAIN from .env
set "NGROK_DOMAIN="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b "NGROK_DOMAIN=" .env`) do set "NGROK_DOMAIN=%%B"
)
if not defined NGROK_DOMAIN (
    echo [ERROR] NGROK_DOMAIN is not set in .env — cannot start tunnel.
    pause
    exit /b 1
)

REM Pin the known-good ngrok install + config (avoids PATH surprises)
set "NGROK_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
if not exist "%NGROK_EXE%" set "NGROK_EXE=ngrok"
set "NGROK_CFG=%LOCALAPPDATA%\ngrok\ngrok.yml"

echo Tunnel: https://%NGROK_DOMAIN%  -^>  this PC :8000
echo (Keep this window open. Ctrl+C twice to stop.)
echo.

if not exist "logs" mkdir logs
echo [%date% %time%] tunnel.bat launched >> "logs\tunnel.log"

:loop
if exist "%NGROK_CFG%" (
    "%NGROK_EXE%" http --config "%NGROK_CFG%" --url=%NGROK_DOMAIN% 8000 --log "%~dp0logs\tunnel.log" --log-level info
) else (
    "%NGROK_EXE%" http --url=%NGROK_DOMAIN% 8000 --log "%~dp0logs\tunnel.log" --log-level info
)
echo.
echo Tunnel stopped (no internet?) — retrying in 15 seconds...  Ctrl+C to quit.
timeout /t 15 /nobreak >nul
goto loop
