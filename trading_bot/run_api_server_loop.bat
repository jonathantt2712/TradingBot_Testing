@echo off
REM Runs api_server.py, restarting it automatically if it exits/crashes.
REM The bot's background loop (auto-close trades, market scans, weight
REM updates) only runs while this process is alive, so a crash shouldn't
REM silently stop trade monitoring. Close this window to stop for good.
cd /d "%~dp0"

:loop
python -u -X utf8 api_server.py 2^>^&1 ^| python -u -X utf8 -c "import sys; sys.stdin.reconfigure(errors='replace'); f=open(r'%~dp0..\logs\api_server.log','a',encoding='utf-8',buffering=1); [(sys.stdout.write(l), f.write(l)) for l in sys.stdin]"
echo.
echo [%date% %time%] api_server.py exited -- restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
