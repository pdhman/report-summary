# 보유종목 가격 알림 실행 래퍼 (작업 스케줄러용)
# - 하루 40회 실행되므로 실행별 파일 대신 "일별 로그"에 append 한다.
# - exit code 3 = 카카오 재인증 필요 → 로그에 배너를 남긴다.

$ErrorActionPreference = 'Continue'

# UTF-8 콘솔 (run_leverage.ps1 패턴)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'
if (-not (Test-Path $py)) {  # 다른 PC(포터블)에서는 PATH의 python 사용
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command py -ErrorAction SilentlyContinue }
    if ($cmd) { $py = $cmd.Source }
}
$script  = Join-Path $PSScriptRoot 'alert_monitor.py'
$logDir  = Join-Path (Split-Path $PSScriptRoot -Parent) 'logs'
$logFile = Join-Path $logDir ("price_alert_{0:yyyyMMdd}.log" -f (Get-Date))

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

"===== 실행 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Add-Content -Encoding UTF8 $logFile
& $py -u $script 2>&1 | Add-Content -Encoding UTF8 $logFile
$code = $LASTEXITCODE

if ($code -eq 3) {
    $banner = @"
##############################################################
# NEEDS_REAUTH: 카카오 토큰 만료 — 알림이 발송되지 않습니다!  #
# 복구:  python price-alert\kakao_auth.py                     #
##############################################################
"@
    $banner | Add-Content -Encoding UTF8 $logFile
}

# 30일 지난 알림 로그 정리
Get-ChildItem $logDir -Filter 'price_alert_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
