@echo off
REM Korea Market Cycle Model - run, alert, then commit+push outputs
REM Push 1: kc_cache with [skip ci]  (aicyclemonitor/** path would trigger the
REM         heavy weekly-aicycle workflow otherwise)
REM Push 2: reports copy WITHOUT skip ci (reports/** path triggers the
REM         daily-insights workflow = GitHub Pages deploy)
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

cd /d "%~dp0.."
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set BR=%%b
if not "%BR%"=="main" (
  echo [git] skip commit: current branch %BR% >> aicyclemonitor\korea_cycle_log.txt
  goto :done
)
git add aicyclemonitor/kc_cache >> aicyclemonitor\korea_cycle_log.txt 2>&1
git commit -m "korea-cycle: cache update [skip ci]" >> aicyclemonitor\korea_cycle_log.txt 2>&1
git pull --rebase --autostash origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
git push origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
git add reports/korea_cycle.html >> aicyclemonitor\korea_cycle_log.txt 2>&1
git commit -m "korea-cycle: daily dashboard update" >> aicyclemonitor\korea_cycle_log.txt 2>&1
git pull --rebase --autostash origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
git push origin main >> aicyclemonitor\korea_cycle_log.txt 2>&1
:done
cd /d "%~dp0"