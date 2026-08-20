@echo off
REM Korea Market Cycle Model - run, alert, then commit+push outputs
REM (repo automation reverts tracked files with checkout, and GitHub Pages
REM  only updates on push - so committing daily is required, not optional)
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

REM ---- persist outputs: only when on main (repo automation may switch branches)
cd /d "%~dp0.."
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set BR=%%b
if not "%BR%"=="main" (
  echo [git] skip commit: current branch %BR% >> aicyclemonitor\korea_cycle_log.txt
  goto :done
)
git add reports/korea_cycle.html aicyclemonitor/kc_cache >> aicyclemonitor\korea_cycle_log.txt 2>&1
git commit -m "korea-cycle: daily update [skip ci]" >> aicyclemonitor\korea_cycle_log.txt 2>&1
git pull --rebase --autostash origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
git push origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
:done
cd /d "%~dp0"
