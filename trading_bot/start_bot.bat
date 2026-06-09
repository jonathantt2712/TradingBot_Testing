@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  start_bot.bat  –  One-click launcher for api_server.py (paper trading)
REM
REM  Place this file in the trading_bot\ directory.
REM  Double-click or run from any terminal to start the bot server.
REM
REM  What it does:
REM    1. Loads environment variables from .env (if present in this folder)
REM    2. Activates a virtualenv named "venv" (if present), otherwise uses
REM       the system Python
REM    3. Starts api_server.py and keeps the window open
REM ─────────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  ████████╗██████╗  █████╗ ██████╗ ██╗███╗   ██╗ ██████╗     ██████╗  ██████╗ ████████╗
echo  ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██║████╗  ██║██╔════╝     ██╔══██╗██╔═══██╗╚══██╔══╝
echo     ██║   ██████╔╝███████║██║  ██║██║██╔██╗ ██║██║  ███╗    ██████╔╝██║   ██║   ██║
echo     ██║   ██╔══██╗██╔══██║██║  ██║██║██║╚██╗██║██║   ██║    ██╔══██╗██║   ██║   ██║
echo     ██║   ██║  ██║██║  ██║██████╔╝██║██║ ╚████║╚██████╔╝    ██████╔╝╚██████╔╝   ██║
echo     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝     ╚═════╝  ╚═════╝    ╚═╝
echo.
echo  [ PAPER TRADING ONLY — no real money ]
echo  ─────────────────────────────────────
echo.

REM ── Step 1: Load .env file if present ─────────────────────────────────────
if exist ".env" (
    echo  Loading environment from .env ...
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /v "^#" .env`) do (
        if not "%%A"=="" (
            set "%%A=%%B"
        )
    )
    echo  Done.
) else (
    echo  [INFO] No .env file found — using system environment variables.
    echo         Create .env in this folder to set BROKER, ALPACA_KEY_ID, etc.
)
echo.

REM ── Step 2: Activate virtualenv if present ────────────────────────────────
if exist "venv\Scripts\activate.bat" (
    echo  Activating virtualenv ...
    call venv\Scripts\activate.bat
) else if exist "..\venv\Scripts\activate.bat" (
    echo  Activating virtualenv (parent folder) ...
    call ..\venv\Scripts\activate.bat
) else (
    echo  [INFO] No venv found — using system Python.
)

REM ── Step 3: Check Python is available ─────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found on PATH.
    echo          Install Python 3.11+ or activate your virtualenv first.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  Using %%v
echo.

REM ── Step 4: Safety guard — never run with real money ──────────────────────
if /i "%ALPACA_PAPER%"=="false" (
    echo  ╔══════════════════════════════════════════════════════════════════╗
    echo  ║  WARNING: ALPACA_PAPER=false detected!                          ║
    echo  ║  This bot is designed for PAPER TRADING only.                   ║
    echo  ║  Refusing to start with real-money mode enabled.                ║
    echo  ╚══════════════════════════════════════════════════════════════════╝
    pause
    exit /b 1
)

REM ── Step 5: Start the bot ──────────────────────────────────────────────────
echo  Starting api_server.py  (Ctrl+C to stop)
echo  ─────────────────────────────────────
echo.
python api_server.py

echo.
echo  Bot server stopped.
pause
