# =====================================================================
# 하이일드 스프레드 급변 텔레그램 DM 알림 (Windows 작업 스케줄러용)
#  - 매일 09:10 · 22:10 (작업명 HY스프레드_알림). FRED 는 하루 이틀 늦게 올라오므로
#    두 번 확인하고, 같은 데이터 날짜는 telegram\hy_alert_state.json 으로 한 번만 판정한다.
#  - 추적 파일을 고치지 않으므로 커밋·푸시는 없다.
#  - 발송은 개인 대화방(dm)만.
# =====================================================================

$proj = Split-Path $PSScriptRoot -Parent
Set-Location $proj

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("hy_alert_{0:yyyyMM}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'start'
    & $py -X utf8 -u (Join-Path $proj 'telegram\send_hy_alert.py') --to dm 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: exit $LASTEXITCODE"; exit 1 }
    Write-Log 'OK'
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
