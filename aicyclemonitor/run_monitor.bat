@echo off
REM AI Cycle Monitor - run script and append log
cd /d "%~dp0"
echo ==== %date% %time% ==== >> monitor_log.txt
where py >nul 2>&1
if %errorlevel%==0 (
  py ai_cycle_monitor.py >> monitor_log.txt 2>&1
) else (
  python ai_cycle_monitor.py >> monitor_log.txt 2>&1
)
