# =====================================================================
# 리서치 종목 새 공시 텔레그램 DM 알림 (Windows 작업 스케줄러용)
#  - 매일 08:20 (작업명 리서치공시_0820_알림). 전날 장 마감 후~아침까지 올라온 DART 공시를
#    docs/thesis/index.json 에 게시된 종목 기준으로 모아 개인 대화방(dm)에 보낸다.
#  - 같은 공시는 telegram\dart_alert_state.json 으로 한 번만 보낸다. 새 공시가 없으면 조용하다.
#  - 추적 파일을 고치지 않으므로 커밋·푸시는 없다.
# =====================================================================

$proj = Split-Path $PSScriptRoot -Parent
Set-Location $proj

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("dart_alert_{0:yyyyMM}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'start'
    & $py -X utf8 -u (Join-Path $proj 'telegram\send_dart_alert.py') --to dm 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: exit $LASTEXITCODE"; exit 1 }
    Write-Log 'OK'
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
