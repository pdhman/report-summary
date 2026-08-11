# =====================================================================
# X 모니터링 요약 누락 감지·발송 (Windows 작업 스케줄러용)
#  - 작업명 X모니터링_누락감지_발송, 매일 10:00~19:00 매시 정각
#  - /x-monitor 스킬은 사람이 실행하는 작업이라 마지막 텔레그램 발송 단계가
#    누락될 수 있다. 이 작업이 오늘자 리포트가 채널에 안 나갔는지 확인해서
#    빠졌으면 대신 보낸다.
#  - 게시 채널: @daily_alphanote(데일리 알파노트) + @Rapha_n_advisory(라파엔투자자문).
#    한쪽만 나간 상태면 안 나간 쪽에만 보낸다 (sent.json 이 대상별로 기록됨).
#  - 실제 판단은 send_x_summary.py --if-missing 이 한다. 아래 경우 조용히 건너뛴다:
#      · 오늘자 리포트 없음        (스킬을 아직 안 돌림)
#      · 이미 발송됨               (telegram\sent.json 이력)
#      · 리포트 수정 30분 이내     (작성 중이거나 스킬이 곧 스스로 보냄)
#      · 게시 페이지 미배포        (메시지 링크가 404 가 됨)
#  - 건너뛴 경우에도 exit 0 이라 스케줄러에 실패로 뜨지 않는다.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("x_check_{0:yyyyMMdd}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    $out = & $py -X utf8 -u (Join-Path $proj 'telegram\send_x_summary.py') `
                 --if-missing --to channel,rapha 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $out) { Write-Log $line }
    if ($code -ne 0) { Write-Log "ERROR: exit $code"; exit 1 }
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
