# =====================================================================
# Quant data weekly collector wrapper (for Windows Task Scheduler)
#  - Every Friday 17:00: collect all-market quant data (price, momentum,
#    fundamentals + consensus estimates) into quant-data\output\.
#  - Then build RS screener data (quant-data\rs_build.py) into
#    reports\rs_data.js and commit/push/deploy (same pattern as
#    run_flows.ps1). Only rs files are staged - quant-data CSVs stay
#    local (untracked).
#  - Python writes its own UTF-8 log to quant-data\logs\; this wrapper
#    logs the rs/git steps to logs\quantdata_*.log.
# =====================================================================

$proj = 'C:\Users\SAMSUNG\Desktop\클로드코드'
$py   = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

Set-Location $proj

# --- git 용량 관리 ---
# 종목별 데이터 파일이 매일 바뀌어 loose object 가 빠르게 쌓인다.
# git 의 자동 gc 는 '객체 6,700개' 기준이라 파일이 큰 이 저장소에서는
# 1.5GB 가 쌓이도록 안 돈다. 임계치를 낮춰 자주 압축되게 한다(재클론 대비).
if ((git config gc.auto) -ne '400') {
    git config gc.auto 400
    git config gc.autoPackLimit 20
    git config gc.bigPackThreshold 256m
}

# --- branch guard: automation always runs on main ---
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { git rebase --quit 2>$null }
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    git checkout -f main 2>$null | Out-Null
    if ((git rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') { exit 1 }
}

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("quantdata_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'collector start'
    Set-Location (Join-Path $proj 'quant-data')
    & $py -X utf8 -u 'collect.py' 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: collector exit $LASTEXITCODE"; exit 1 }

    Write-Log 'rs_build start'
    & $py -X utf8 -u 'rs_build.py' 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: rs_build exit $LASTEXITCODE"; exit 1 }
    Set-Location $proj

    # wait while other scheduled git automations are running (max 6 min)
    for ($i = 0; $i -lt 36; $i++) {
        $busy = $false
        foreach ($tn in @('주도섹터_필터링_매일1530', '투자전략_1730_수집', '시장레버리지_1730_수집', '수급동향_1540_수집')) {
            $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
            if ($t -and $t.State -eq 'Running') { $busy = $true }
        }
        if (-not $busy) { break }
        Write-Log 'waiting for other git tasks to finish...'
        Start-Sleep -Seconds 10
    }

    git add reports/rs_data.js reports/rs.html 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("rs: {0:yyyy-MM-dd} RS screener data update" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                # generated-file conflict stopped the rebase: take local side.
                # never add -A here (risk of leaking untracked private files).
                git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
            }
            else {
                Write-Log 'ERROR: pull --rebase failed before rebase started - manual check needed'
                exit 1
            }
        }
        git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { Write-Log 'ERROR: git push failed'; exit 1 }
        Write-Log 'OK: pushed rs data'
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj 'trigger_deploy.ps1')
        Write-Log ("deploy trigger exit: {0}" -f $LASTEXITCODE)
    }
    else {
        Write-Log 'OK: no data change, nothing to push'
    }
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
