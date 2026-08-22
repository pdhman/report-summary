# =====================================================================
# 수급동향 일일 수집 (Windows 작업 스케줄러용)
#  - 평일 15:40 (작업명 수급동향_1540_수집): 네이버에서 코스피/코스닥/선물
#    투자자별 수급 수집 → CSV/엑셀/dashboard_data.js 갱신
#  - 이어서 알파노트 요약(index.html)·수급 페이지(flow.html)를 재생성하고
#    커밋·푸시·배포까지 수행 (run_leverage.ps1 과 같은 패턴)
#  - 15:40 은 잠정치 시간대: 당일 수급이 아직 잠정이거나(특히 선물) 안 떴을
#    수 있다. 매 실행이 최근 14일을 재수집하므로 다음 실행에서 확정치로
#    보정된다.
#  - 같은 시간대의 다른 git 자동화(15:30 주도섹터, 수동 재실행 시 17:30
#    작업들)와 겹치지 않도록, 그 작업이 끝난 뒤에 git 단계를 시작한다
#    (수집 자체는 바로 진행).
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

    # 같은 시간대 git 자동화가 돌고 있으면 끝날 때까지 대기(최대 6분)
    for ($i = 0; $i -lt 36; $i++) {
        $busy = $false
        foreach ($tn in @('주도섹터_필터링_매일1530', '투자전략_1730_수집', '시장레버리지_1730_수집')) {
            $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
            if ($t -and $t.State -eq 'Running') { $busy = $true }
        }
        if (-not $busy) { break }
        Write-Log 'waiting for 17:30 tasks to finish...'
        Start-Sleep -Seconds 10
    }

    git add 수급모니터링 docs 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("flows: {0:yyyy-MM-dd} 수급 수집" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
                # add -A 금지: 추적 외 개인 파일까지 스테이징해 공개 저장소로
                # 유출될 뻔한 실사고 있음(2026-08-03). 충돌은 추적 파일에서만
                # 발생하므로 add -u 로 충분하다.
                git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
            }
            else {
                # 리베이스가 시작도 못 한 실패(예: unstaged changes)는 무인 복구
                # 대상이 아니다 - 작업 트리를 건드리지 말고 수동 확인으로 넘긴다.
                Write-Log 'ERROR: pull --rebase failed before rebase started - manual check needed'
                exit 1
            }
        }
        # pull 과 push 사이에 다른 자동화가 먼저 푸시하면 리젝된다(2026-08-21
        # 실사고). 리젝 시 다시 pull --rebase 후 재시도.
        $pushed = $false
        for ($p = 0; $p -lt 3; $p++) {
            git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
            if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
            Write-Log ("push rejected, retry {0}/3" -f ($p + 1))
            Start-Sleep -Seconds 5
            git pull --rebase -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        }
        if (-not $pushed) { Write-Log 'ERROR: git push failed after retries'; exit 1 }
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
