# =====================================================================
# Daily blog (investment strategy) sync for Windows Task Scheduler
#  - Every day 18:00 KST: scrape new posts from the Naver blog,
#    rebuild the strategy pages/hub, then commit & push.
#  - The push triggers the daily-insights workflow (paths: blog/**,
#    reports/**), which redeploys the GitHub Pages site.
#  - ASCII-only on purpose: powershell.exe 5.1 misreads BOM-less UTF-8.
#    Paths come from $PSScriptRoot, never hard-coded.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("blog_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'scrape_blog.py start'
    & $py -u (Join-Path $proj 'scrape_blog.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: scrape_blog exit $LASTEXITCODE"; exit 1 }

    & $py -u (Join-Path $proj 'make_blog.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: make_blog exit $LASTEXITCODE"; exit 1 }

    git add blog reports 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("blog: {0:yyyy-MM-dd} auto update" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        # generated files may conflict with bot commits; prefer our fresh build
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { Write-Log 'ERROR: git push failed'; exit 1 }
        Write-Log 'OK: pushed new blog content'
    }
    else {
        Write-Log 'OK: no new posts, nothing to push'
    }
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
