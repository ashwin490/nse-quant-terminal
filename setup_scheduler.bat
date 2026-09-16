@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
set "RETRAIN_SCRIPT=%~dp0cron_retrainer.py"
set "EOD_SCRIPT=%~dp0scripts\sync_eod_data.py"

echo =========================================================
echo Setting up Windows Task Scheduler for Stock Terminal AI
echo =========================================================

:: 1. Task: Daily EOD Ingestion at 16:00 (Mon-Fri)
schtasks /create /tn "StockTerminal_DailyEODSync" ^
    /tr "\"%PYTHON_EXE%\" \"%EOD_SCRIPT%\"" ^
    /sc weekly /d MON,TUE,WED,THU,FRI /st 16:00 /f

:: 2. Task: Weekly Self-Learning Model Retraining (Every Saturday at 10:00 AM)
schtasks /create /tn "StockTerminal_WeekendRetrain" ^
    /tr "\"%PYTHON_EXE%\" \"%RETRAIN_SCRIPT%\"" ^
    /sc weekly /d SAT /st 10:00 /f

echo.
echo =========================================================
echo Scheduled Tasks Successfully Registered:
echo  1. StockTerminal_DailyEODSync  (Mon-Fri at 4:00 PM)
echo  2. StockTerminal_WeekendRetrain (Every Sat at 10:00 AM)
echo =========================================================
pause