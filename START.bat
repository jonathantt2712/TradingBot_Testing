@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  START.bat — one-click launcher for the whole trading stack
REM
REM  Opens up to four windows and then your browser:
REM    [1] API Server   — dashboard backend (api_server.py)
REM    [2] Trading Bot  — live_runner.py (DRY RUN unless EXECUTE_LIVE=true in .env)
REM    [3] Dashboard    — Next.js UI (npm run dev)
REM    [4] Tunnel       — ngrok (only if NGROK_DOMAIN is set in .env);
REM                       lets your Vercel dashboard reach this PC.
REM  Browser opens http://localhost:3000 once the dashboard is up.
REM
REM  Every window also writes to logs\  (api_server.log, bot.log,
REM  dashboard.log, tunnel.log) so crashes can be debugged after the fact.
REM
REM  To stop everything: close the windows.
REM ─────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

echo.
echo  Starting the trading stack...
echo.

REM Debug logs — one file per component, appended across runs
if not exist "logs" mkdir logs
echo [%date% %time%] START.bat launched >> "logs\start.log"
for %%F in (api_server bot dashboard) do (
    echo. >> "logs\%%F.log"
    echo ======== session start %date% %time% ======== >> "logs\%%F.log"
)

REM Read NGROK_DOMAIN from .env (used by the Vercel-facing tunnel)
set "NGROK_DOMAIN="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b "NGROK_DOMAIN=" .env`) do set "NGROK_DOMAIN=%%B"
)

REM [1] API server (dashboard backend) — output shown live AND teed to logs\
REM     (tee via python: PowerShell's Tee-Object writes UTF-16 and garbles logs)
REM     run_api_server_loop.bat auto-restarts api_server.py if it crashes,
REM     so the trade-monitoring background loop doesn't go stale silently.
start "API Server" /D "%~dp0trading_bot" cmd /k run_api_server_loop.bat

REM [2] Trading bot (analysis + orders; dry-run by default)
start "Trading Bot" /D "%~dp0trading_bot" cmd /k python -u -X utf8 live_runner.py 2^>^&1 ^| python -u -X utf8 -c "import sys; sys.stdin.reconfigure(errors='replace'); f=open(r'%~dp0logs\bot.log','a',encoding='utf-8',buffering=1); [(sys.stdout.write(l), f.write(l)) for l in sys.stdin]"

REM [3] Dashboard UI (install deps automatically on first run)
if not exist "%~dp0trading-dashboard\node_modules" (
    echo  First run: installing dashboard dependencies, this takes a minute...
    pushd "%~dp0trading-dashboard"
    call npm install
    popd
)
start "Dashboard" /D "%~dp0trading-dashboard" cmd /k npm run dev 2^>^&1 ^| python -u -X utf8 -c "import sys; sys.stdin.reconfigure(errors='replace'); f=open(r'%~dp0logs\dashboard.log','a',encoding='utf-8',buffering=1); [(sys.stdout.write(l), f.write(l)) for l in sys.stdin]"

REM [4] Tunnel for the Vercel dashboard (optional — needs NGROK_DOMAIN in .env)
REM     tunnel.bat keeps itself alive across network drops.
if defined NGROK_DOMAIN (
    start "Tunnel (Vercel link)" "%~dp0tunnel.bat"
    echo  Tunnel: https://%NGROK_DOMAIN%  -^>  this PC :8000
) else (
    echo  [INFO] No NGROK_DOMAIN in .env — skipping tunnel. Local use only.
)

REM [5] Wait for the dashboard port, then open the browser (max ~2 min)
echo  Waiting for the dashboard to come up at http://localhost:3000 ...
set /a tries=0
:wait
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',3000); exit 0}catch{exit 1}finally{$c.Close()}" >nul 2>&1
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% geq 60 goto ready
timeout /t 2 /nobreak >nul
goto wait

:ready
start "" http://localhost:3000
echo.
echo  Done! Browser opened. Close the three windows to stop everything.
timeout /t 5 >nul
exit /b 0
