@echo off
echo ============================================================
echo  Trading Bot - Git Push to GitHub
echo ============================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_helper.ps1" > "%~dp0push_log.txt" 2>&1
type "%~dp0push_log.txt"
echo.
echo Done! Press any key to close.
pause
