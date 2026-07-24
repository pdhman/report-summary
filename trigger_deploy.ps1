# =====================================================================
# GitHub Pages 배포(daily-insights) 명시적 트리거
#  - push 이벤트가 간헐적으로 워크플로를 걸지 못해 사이트가 최신
#    커밋으로 배포되지 않는 문제를 보완한다.
#  - run_screener / run_blog 가 push 성공 직후 이 스크립트를 호출한다.
#  - 토큰은 Git Credential Manager 에서 실행 시점에 꺼내 쓴다(파일에
#    비밀값 없음). ASCII-only 주석.
# =====================================================================
$ErrorActionPreference = 'Stop'

$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("deploy_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    # Git Credential Manager 에서 토큰 조회. PowerShell 파이프는 중첩 호출 시
    # stdin 인코딩 문제가 있어, Git 동봉 bash 의 printf 로 안정적으로 처리.
    $bash = @('C:\Program Files\Git\bin\bash.exe',
              'C:\Program Files\Git\usr\bin\bash.exe') |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $bash) { Write-Log 'ERROR: bash.exe not found'; exit 1 }
    $token = (& $bash -c "printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2-").Trim()
    if (-not $token) { Write-Log 'ERROR: no GitHub token'; exit 1 }

    $resp = Invoke-WebRequest -UseBasicParsing -Method Post `
        -Uri 'https://api.github.com/repos/pdhman/report-summary/actions/workflows/daily-insights.yml/dispatches' `
        -Headers @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json' } `
        -ContentType 'application/json' -Body '{"ref":"main"}'
    Write-Log "OK: deploy dispatch HTTP $($resp.StatusCode)"
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
