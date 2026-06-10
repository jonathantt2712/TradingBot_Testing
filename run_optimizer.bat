@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\itait\Claude\Projects\trading bot\trading_bot"
echo Running backtest optimizer... > "..\optimizer_output.log"
echo Started: %date% %time% >> "..\optimizer_output.log"
python -u optimize_backtest.py 2>&1 | powershell -Command "$input | Tee-Object -FilePath '..\optimizer_output.log'"
echo. >> "..\optimizer_output.log"
echo Finished: %date% %time% >> "..\optimizer_output.log"
echo Done! Results saved to backtest_optimal.json and OPTIMAL_CONFIG.txt
pause
