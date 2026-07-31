@echo off
REM Register weekly task: every Monday 08:30 (run as current user)
REM Change /D MON or /ST 08:30 as you like. /SC DAILY for daily runs.
schtasks /Create /F /TN "AI_Cycle_Monitor" /TR "\"%~dp0run_monitor.bat\"" /SC WEEKLY /D MON /ST 08:30
if %errorlevel%==0 (
  echo Registered: AI_Cycle_Monitor - every Monday 08:30
) else (
  echo Failed. Run this file as Administrator, or register manually in Task Scheduler.
)
pause
