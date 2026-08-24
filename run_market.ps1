# =====================================================================
# 시장 건전성 일일 갱신 (Windows 작업 스케줄러용)
#  - 평일 16:35 (작업명 시장건전성_1635_수집): quant-data\breadth_build.py 로
#    폭 지표·VKOSPI 계산 → docs\market_data.js + data\market_history.csv
#    갱신 → 알파노트 요약(index.html) 재생성 → 커밋·푸시·배포
#  - 16:20 차트데이터 작업이 만든 ohlcv_full.parquet 를 그대로 재사용하므로
#    차트데이터 작업이 끝날 때까지 먼저 기다린다 (최대 12분).
#  - git 단계는 같은 시간대의 다른 git 자동화와 겹치지 않게 대기 후 시작
#    (run_flows.ps1 과 같은 패턴).
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
$log = Join-Path $logDir ("market_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    # 차트데이터(16:20, parquet 생산자)가 오늘자 캐시를 만들 때까지 대기 (최대 12분).
    # "실행 중"만 보면 차트 작업이 늦게 시작한 날(예: 2026-08-06, 16:41 시작)
    # 어제 데이터로 돌아버리므로, parquet 파일이 오늘 갱신됐는지를 함께 본다.
    # 휴장일 등으로 오늘자 갱신이 없으면 12분 후 그대로 진행한다(데이터 변화
    # 없음 → 푸시 생략으로 끝나므로 무해).
    $pq = Join-Path $proj 'quant-data\cache\ohlcv_full.parquet'
    for ($i = 0; $i -lt 72; $i++) {
        $t = Get-ScheduledTask -TaskName '차트데이터_1620_수집' -ErrorAction SilentlyContinue
        $running = ($t -and $t.State -eq 'Running')
        $fresh = (Test-Path $pq) -and ((Get-Item $pq).LastWriteTime.Date -eq (Get-Date).Date)
        if (-not $running -and $fresh) { break }
        if (-not $t) { break }   # 차트 작업이 아예 없으면 그냥 진행
        if ($i -eq 0) { Write-Log 'waiting for chart-data (fresh parquet)...' }
        Start-Sleep -Seconds 10
    }

    Write-Log 'breadth_build start'
    & $py -X utf8 -u (Join-Path $proj 'quant-data\breadth_build.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: breadth_build exit $LASTEXITCODE"; exit 1 }

    # 침체·과열 구간 카톡 알림 (실패해도 파이프라인은 계속)
    & $py -X utf8 -u (Join-Path $proj 'quant-data\market_alert.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -eq 3) {
        Write-Log 'NEEDS_REAUTH: 카카오 토큰 만료 - 복구: python price-alert\kakao_auth.py'
    }
    elseif ($LASTEXITCODE -ne 0) {
        Write-Log "WARN: market_alert exit $LASTEXITCODE (계속 진행)"
    }

    # 알파노트 홈 카드(index.html) 재생성
    & $py -u -c "import make_summary; make_summary.build()" 2>&1 | Add-Content -Path $log -Encoding UTF8

    # 같은 시간대 git 자동화가 돌고 있으면 끝날 때까지 대기(최대 6분)
    for ($i = 0; $i -lt 36; $i++) {
        $busy = $false
        foreach ($tn in @('수급동향_1540_수집', '주도섹터_필터링_매일1530', '투자전략_1730_수집', '시장레버리지_1730_수집')) {
            $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
            if ($t -and $t.State -eq 'Running') { $busy = $true }
        }
        if (-not $busy) { break }
        Write-Log 'waiting for other git tasks to finish...'
        Start-Sleep -Seconds 10
    }

    git add docs 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("market: {0:yyyy-MM-dd} 시장 건전성 갱신" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        # --autostash: 다른 작업(예: aicyclemonitor)이 남긴 미커밋 변경이 있어도
        # 잠시 치웠다가 리베이스 후 복원 — unstaged changes 로 매일 실패하는 것 방지
        git pull --rebase --autostash -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
                # add -A 금지: 추적 외 개인 파일 유출 실사고(2026-08-03) 재발 방지 —
                # 충돌은 추적 파일에서만 발생하므로 add -u 로 충분하다.
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
        Write-Log 'OK: pushed market data'
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
