# =====================================================================
# 수급동향 일일 수집 (Windows 작업 스케줄러용)
#  - 평일 17:40: 네이버에서 코스피/코스닥/선물 투자자별 수급 수집
#    → CSV/엑셀/dashboard_data.js 갱신
#  - 이어서 알파노트 요약(index.html)·수급 페이지(flow.html)를 재생성하고
#    커밋·푸시·배포까지 수행 (run_leverage.ps1 과 같은 패턴)
#  - 17:30 작업들(투자전략·시장레버리지)과 git 이 겹치지 않도록, 그 작업이
#    끝난 뒤에 git 단계를 시작한다(수집 자체는 바로 진행).
#  - 수집 CSV 는 git 추적 대상이라 반드시 커밋해야 한다. 방치하면 다른
#    자동화의 pull --rebase 가 "unstaged changes" 로 실패한다.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

# --- 브랜치 가드: 자동화는 항상 main 기준 ---
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
$log = Join-Path $logDir ("flows_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'collector start'
    & $py -X utf8 -u (Join-Path $proj '수급모니터링\collect.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: collector exit $LASTEXITCODE"; exit 1 }

    # 요약 페이지 재생성(수급 카드 + flow.html 도 함께 만들어진다)
    & $py -u -c "import make_summary; make_summary.build()" 2>&1 | Add-Content -Path $log -Encoding UTF8

    # 17:30 작업들이 돌고 있으면 끝날 때까지 대기(최대 6분)
    for ($i = 0; $i -lt 36; $i++) {
        $busy = $false
        foreach ($tn in @('투자전략_1730_수집', '시장레버리지_1730_수집')) {
            $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
            if ($t -and $t.State -eq 'Running') { $busy = $true }
        }
        if (-not $busy) { break }
        Write-Log 'waiting for 17:30 tasks to finish...'
        Start-Sleep -Seconds 10
    }

    git add 수급모니터링 reports 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("flows: {0:yyyy-MM-dd} 수급 수집" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            # 생성물 충돌로 리베이스가 멈추면 로컬 본을 채택해 무인 복구
            git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
            git add -A 2>&1 | Add-Content -Path $log -Encoding UTF8
            git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
        }
        git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { Write-Log 'ERROR: git push failed'; exit 1 }
        Write-Log 'OK: pushed flow data'
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
