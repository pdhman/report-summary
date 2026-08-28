# =====================================================================
# Daily blog (investment strategy) sync for Windows Task Scheduler
#  - Every day 17:30 KST: scrape new posts from the Naver blog,
#    rebuild the strategy pages/hub, then commit & push.
#  - The push triggers the daily-insights workflow (paths: blog/**,
#    docs/**), which redeploys the GitHub Pages site.
#  - ASCII-only on purpose: powershell.exe 5.1 misreads BOM-less UTF-8.
#    Paths come from $PSScriptRoot, never hard-coded.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

# --- 브랜치 가드: 자동화는 항상 main 기준 (실습 브랜치에 있으면 전환) ---
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { git rebase --quit 2>$null }
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    git checkout -f main 2>$null | Out-Null
    if ((git rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') { exit 1 }
}

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

    git add blog docs 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("blog: {0:yyyy-MM-dd} auto update" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        # generated files may conflict with bot commits; prefer our fresh build
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                # rebase stopped on generated-file conflict: adopt local build.
                # add -u only (tracked files) - add -A would stage untracked
                # personal files into this public repo (run_flows incident 2026-08-03)
                git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
            }
            else {
                # pull failed before rebase started (e.g. unstaged changes):
                # do not touch the working tree, leave for manual check
                Write-Log 'ERROR: pull --rebase failed before rebase started - manual check needed'
                exit 1
            }
        }
        # 봇 커밋이 fetch~push 사이에 들어오면 push 가 경쟁 실패한다
        # ("cannot lock ref", 2026-08-03 실사고). pull 후 최대 3회 재시도.
        # $pushed 플래그 필수: 루프 마지막 명령이 pull 이라 $LASTEXITCODE 로
        # 판정하면 3회 모두 실패해도 성공으로 오판한다.
        $pushed = $false
        for ($try = 1; $try -le 3; $try++) {
            git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
            if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
            Write-Log ("push rejected (attempt {0}/3) - retrying" -f $try)
            Start-Sleep -Seconds 5
            git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        }
        if (-not $pushed) { Write-Log 'ERROR: git push failed after 3 attempts'; exit 1 }
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
