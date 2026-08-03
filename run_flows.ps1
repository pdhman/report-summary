# =====================================================================
# Investor flow daily collector wrapper (for Windows Task Scheduler)
#  - Weekdays 17:40: collect KOSPI/KOSDAQ/futures investor flows into
#    수급모니터링\ (CSV master + 수급동향.xlsx + dashboard_data.js).
#  - Python writes its own UTF-8 log to 수급모니터링\logs\, so this
#    wrapper needs no output redirection. ASCII-only comments.
# =====================================================================

$proj = 'C:\Users\SAMSUNG\Desktop\클로드코드'
$py   = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

Set-Location (Join-Path $proj '수급모니터링')

$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

& $py -X utf8 -u 'collect.py'
exit $LASTEXITCODE
