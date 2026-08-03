# =====================================================================
# 시장 레버리지 데이터 일일 수집 (Windows 작업 스케줄러용)
#  - 매일 17:30: FreeSIS 에서 신용잔고·증시자금 수집 → CSV/DB 갱신
#  - 이어서 요약/차트 페이지를 재생성하고 커밋·푸시·배포까지 수행
#  - 같은 시각의 '투자전략_1730_수집' 과 git 이 겹치지 않도록, 그 작업이
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
$log = Join-Path $logDir ("leverage_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'collector start'
    & $py -u (Join-Path $proj 'market_leverage_collector\collector.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: collector exit $LASTEXITCODE"; exit 1 }

    # 요약 페이지 재생성(레버리지 차트 페이지도 함께 만들어진다)
    & $py -u -c "import make_summary; make_summary.build()" 2>&1 | Add-Content -Path $log -Encoding UTF8

    # 같은 시각 블로그 작업이 돌고 있으면 끝날 때까지 대기(최대 6분)
    for ($i = 0; $i -lt 36; $i++) {
        $t = Get-ScheduledTask -TaskName '투자전략_1730_수집' -ErrorAction SilentlyContinue
        if (-not $t -or $t.State -ne 'Running') { break }
        Write-Log 'waiting for blog task to finish...'
        Start-Sleep -Seconds 10
    }

    git add market_leverage_collector/data reports 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("leverage: {0:yyyy-MM-dd} 수집" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
                # add -A 금지: 추적 외 개인 파일까지 스테이징해 공개 저장소로
                # 유출될 수 있다 (2026-08-03 run_flows 실사고). 충돌은 추적
                # 파일에서만 발생하므로 add -u 로 충분하다.
                git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
            }
            else {
                # 리베이스 시작 전 실패(예: unstaged changes)는 무인 복구 대상이
                # 아니다 - 작업 트리를 건드리지 말고 수동 확인으로 넘긴다.
                Write-Log 'ERROR: pull --rebase failed before rebase started - manual check needed'
                exit 1
            }
        }
        git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { Write-Log 'ERROR: git push failed'; exit 1 }
        Write-Log 'OK: pushed leverage data'
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
