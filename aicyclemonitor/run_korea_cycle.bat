@echo off
REM Korea Market Cycle Model - run script and append log
cd /d "%~dp0"
echo ==== %date% %time% ==== >> korea_cycle_log.txt
where py >nul 2>&1
if %errorlevel%==0 (
  py korea_cycle_monitor.py >> korea_cycle_log.txt 2>&1
  py "%~dp0..\telegram\send_cycle_alert.py" >> korea_cycle_log.txt 2>&1
) else (
  python korea_cycle_monitor.py >> korea_cycle_log.txt 2>&1
  python "%~dp0..\telegram\send_cycle_alert.py" >> korea_cycle_log.txt 2>&1
)
