# =====================================================================
# 알파노트 요약 텔레그램 발송 (Windows 작업 스케줄러용)
#  - 평일 16:50 (작업명 텔레그램요약_1650_발송)
#  - reports\market_data.js / 수급모니터링\dashboard_data.js / reports\rs_data.js
#    를 읽어 핵심 지표만 요약해 텔레그램으로 보낸다. 수집은 하지 않는다.
#  - --to both: 개인 대화방 + 공개 채널(@daily_alphanote) 양쪽에 보낸다.
#    채널 발송은 봇이 채널 관리자여야 하므로, 관리자에서 빠지면 여기서 실패한다.
#  - 시장건전성_1635_수집 이 차트데이터를 기다리느라 16:50 을 넘길 수 있어
#    그 작업이 끝나고 market_data.js 가 오늘자로 갱신될 때까지 먼저 기다린다.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("telegram_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    # 시장건전성(16:35, market_data.js 생산자)이 오늘자를 만들 때까지 대기 (최대 15분).
    # 휴장일 등으로 갱신이 없으면 그대로 진행한다 — 요약 본문에 "최신 데이터가
    # N월 N일 기준" 경고가 붙으므로 조용히 옛 데이터를 보내는 일은 없다.
    $md = Join-Path $proj 'reports\market_data.js'
    for ($i = 0; $i -lt 90; $i++) {
        $t = Get-ScheduledTask -TaskName '시장건전성_1635_수집' -ErrorAction SilentlyContinue
        $running = ($t -and $t.State -eq 'Running')
        $fresh = (Test-Path $md) -and ((Get-Item $md).LastWriteTime.Date -eq (Get-Date).Date)
        if (-not $running -and $fresh) { break }
        if (-not $t) { break }
        if ($i -eq 0) { Write-Log 'waiting for market-health task...' }
        Start-Sleep -Seconds 10
    }

    Write-Log 'send_summary start'
    & $py -X utf8 -u (Join-Path $proj 'telegram\send_summary.py') --to both 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: send_summary exit $LASTEXITCODE"; exit 1 }

    Write-Log 'OK: sent'
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
