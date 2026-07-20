# =====================================================================
# GitHub Actions "Daily Report Summary" remote trigger
#  - Runs from Windows Task Scheduler at 09:23 KST on weekdays.
#  - Fires workflow_dispatch via GitHub API so the crawl starts on time
#    (GitHub's own cron schedule stays as a fallback for PC-off days).
#  - Token is pulled from Git Credential Manager at runtime;
#    nothing secret is stored in this file.
#  - ASCII-only on purpose: powershell.exe 5.1 misreads BOM-less UTF-8.
#    Project paths are derived from $PSScriptRoot, never hard-coded.
# =====================================================================

$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("dispatch_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    $cred  = "protocol=https", "host=github.com", "" | git credential fill 2>$null
    $token = ($cred | Select-String '^password=(.+)$').Matches.Groups[1].Value
    if (-not $token) { Write-Log 'ERROR: no GitHub token from credential manager'; exit 1 }

    $resp = Invoke-WebRequest -UseBasicParsing -Method Post `
        -Uri 'https://api.github.com/repos/pdhman/report-summary/actions/workflows/daily-report.yml/dispatches' `
        -Headers @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json' } `
        -ContentType 'application/json' -Body '{"ref":"main"}'
    Write-Log "OK: dispatch HTTP $($resp.StatusCode)"
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
