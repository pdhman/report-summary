# =====================================================================
# 크립토 모니터 일일 갱신 (Windows 작업 스케줄러용)
#  - 매일 11:00·20:00 두 번 (작업명 크립토모니터_1100_수집 / _2000_수집, 같은 스크립트):
#    crypto_monitor.py 실행 → crypto.html + crypto_summary.json 갱신 →
#    알파노트 홈 재생성 → 커밋·푸시·배포
#  - 11시: UTC 일봉이 09:00 KST 에 마감되므로 그 뒤라야 전일 UTC 데이터(MVRV·OI·
#    가격)가 완성된다.
#  - 20시: ETF 순유입 때문. Farside 는 발행사 보고가 들어오는 대로 칸을 채우고 영국
#    오전에 마무리하므로 11시 KST(미국 전날 22시)엔 전일 세션이 대개 부분집계다
#    (9번 중 5번, 부호가 뒤집힌 날도 있음). 파서가 부분집계 행을 거르므로 11시엔
#    ETF 기준일이 T-2 로 보이고, 20시(영국 12시) 실행이 T-1 을 채운다.
#  - OI 는 바이낸스가 최근 30일만 주기 때문에 매일 실행해야 히스토리가
#    누적된다(oi_history.csv). 하루 걸러도 그날 구간은 영구 유실이므로
#    놓친 실행 따라잡기(StartWhenAvailable)를 반드시 켜 둘 것.
#  - git 은 저장소 루트(상위 폴더)에서 돈다. 이 스크립트만 crypto-monitor/
#    안에 있고 배포 대상은 docs/ 다.
#  - crypto-monitor/ 자체는 git 비추적이라 스테이징하지 않는다(docs 만 커밋).
# =====================================================================

$here = $PSScriptRoot
$repo = Split-Path $here -Parent
Set-Location $repo

# --- 브랜치 가드: 자동화는 항상 main 기준 ---
# -f 금지: `checkout -f main` 은 이미 main 이어도 미커밋 수정을 전부 버린다
# (2026-09-15 docs/market.html 유실 실사고). 전환이 막히면 건드리지 않고 중단한다.
if (Test-Path (Join-Path $repo '.git/rebase-merge')) { git rebase --quit 2>$null }
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    git checkout main 2>$null | Out-Null
    if ((git rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') {
        "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) [crypto] branch '$branch' -> main FAILED (uncommitted edits in the way?) - abort, nothing discarded" |
            Add-Content -Path (Join-Path $repo 'logs\branch_guard.log') -Encoding UTF8 -ErrorAction SilentlyContinue
        exit 1
    }
}

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$logDir = Join-Path $here 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("crypto_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }

try {
    Write-Log 'crypto_monitor start'
    & $py -X utf8 -u (Join-Path $here 'crypto_monitor.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: crypto_monitor exit $LASTEXITCODE"; exit 1 }

    # 생성물이 실제로 갱신됐는지 확인 (네트워크 실패 시 옛 파일이 남는 것 방지)
    $html = Join-Path $here 'crypto.html'
    if (-not (Test-Path $html) -or (Get-Item $html).LastWriteTime.Date -ne (Get-Date).Date) {
        Write-Log 'ERROR: crypto.html not refreshed today'
        exit 1
    }

    # 알파노트 홈 재생성 — card_crypto() 가 요약 JSON 을 읽어 카드를 만들고
    # crypto.html 을 docs/ 로 복사하면서 하단 내비를 주입한다.
    & $py -u -c "import make_summary; make_summary.build()" 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: make_summary exit $LASTEXITCODE"; exit 1 }

    # 오전 시간대 git 자동화가 돌고 있으면 끝날 때까지 대기(최대 6분)
    for ($i = 0; $i -lt 36; $i++) {
        $busy = $false
        foreach ($tn in @('리포트서머리_0923_트리거', '시장레버리지_0852_수집', '팩터랩_월간갱신',
                          '투자전략_1730_수집', '시장레버리지_1730_수집', '한국사이클_1745_생성')) {
            $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
            if ($t -and $t.State -eq 'Running') { $busy = $true }
        }
        if (-not $busy) { break }
        if ($i -eq 0) { Write-Log 'waiting for other git tasks to finish...' }
        Start-Sleep -Seconds 10
    }

    git add docs 2>&1 | Add-Content -Path $log -Encoding UTF8
    git diff --staged --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ("crypto: {0:yyyy-MM-dd} 대시보드 갱신" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
        # --autostash: 다른 작업이 남긴 미커밋 변경이 있어도 잠시 치웠다가 복원
        git pull --rebase --autostash -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path (Join-Path $repo '.git/rebase-merge')) {
                # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
                # add -A 금지: 추적 외 개인 파일까지 스테이징해 공개 저장소로
                # 유출될 수 있다 (2026-08-03 run_flows 실사고). 충돌은 추적
                # 파일에서만 발생하므로 add -u 로 충분하다.
                # `--theirs -- .` 의 실제 영향은 충돌 난 추적 파일뿐이다: 리베이스 중엔
                # 사람의 미커밋 변경이 작업 트리에 없고(--autostash 로 치워져 있음),
                # 미추적 파일은 checkout 이 건드리지 않는다.
                git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
            }
            else {
                # 리베이스 시작 전 실패는 무인 복구 대상이 아니다 - 수동 확인으로 넘긴다.
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
            git pull --rebase --autostash -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
        }
        if (-not $pushed) { Write-Log 'ERROR: git push failed after 3 attempts'; exit 1 }
        Write-Log 'OK: pushed crypto dashboard'
    }
    else {
        Write-Log 'OK: no data change, nothing to push'
    }

    # 로그 보관 30일
    Get-ChildItem $logDir -Filter 'crypto_*.log' |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
