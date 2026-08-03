# =====================================================================
# 주도섹터 필터링 자동 실행 래퍼 (Windows 작업 스케줄러용)
#  - UTF-8 로그 저장 (한글 안 깨짐)
#  - matplotlib Agg 백엔드 → 차트 창으로 멈추지 않음
#  - 결과는 logs\screener_날짜시간.log 에 저장
# =====================================================================

$proj = 'C:\Users\SAMSUNG\Desktop\클로드코드'
$py   = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'
$script = '주도섹터 필터링.py'

Set-Location $proj

# 실행 환경
$env:PYTHONUTF8      = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:MPLBACKEND      = 'Agg'      # 차트 창 없이 진행 (스케줄 실행 시 멈춤 방지)

# (선택) 슬랙 알림을 켜려면 아래 줄의 주석을 풀고 봇 토큰을 넣으세요.
# $env:SLACK_TOKEN = 'xoxb-여기에-토큰'

# 로그 폴더 / 파일명
$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = Join-Path $logDir "screener_$stamp.log"

# --- 브랜치 가드: 자동화는 항상 main 기준 (실습 브랜치에 있으면 전환) ---
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { & git -C $proj rebase --quit 2>$null }
$branch = (& git -C $proj rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    "[guard] branch '$branch' -> main" | Out-File $out -Append -Encoding utf8
    & git -C $proj checkout -f main 2>&1 | Out-File $out -Append -Encoding utf8
    if ((& git -C $proj rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') {
        "[guard] FAILED to switch to main - abort" | Out-File $out -Append -Encoding utf8
        exit 1
    }
}
$err = Join-Path $logDir "screener_$stamp.err.log"

# 실행 (자식 프로세스가 UTF-8 바이트를 그대로 파일에 기록)
$p = Start-Process -FilePath $py -ArgumentList "-u `"$script`"" `
    -WorkingDirectory $proj -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err

# 에러 로그가 비어있으면 삭제
if ((Test-Path $err) -and ((Get-Item $err).Length -eq 0)) { Remove-Item $err }

# --- 보고서(HTML) 생성 및 브라우저로 열기 ---
& cmd /c "`"$py`" enrich_fundamentals.py >> `"$out`" 2>&1"
& cmd /c "`"$py`" make_report.py >> `"$out`" 2>&1"
$report = Get-ChildItem (Join-Path $proj 'reports\report_*.html') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 1
if ($report) { Start-Process $report.FullName }

# --- Git 아카이브에 이력 커밋 ---
& cmd /c "`"$py`" git_archive.py >> `"$out`" 2>&1"

# --- GitHub(report_summary origin/main)에 리포트 게시 ---
# report_*.html + 허브/요약 + 스크리너 xlsx push. push 되면 GitHub Actions(daily-insights)가
# reports/** 트리거로 사이트를 재배포한다. (봇 커밋은 [skip ci]라 루프 없음)
# xlsx 는 러너의 요약 대시보드(주도주 카드) 생성에 필요하다.
& git -C $proj add "reports/report_*.html" "reports/index.html" "reports/screener.html" "종목탐색_TOP30.xlsx" 2>&1 | Out-File $out -Append -Encoding utf8
& git -C $proj diff --staged --quiet
if ($LASTEXITCODE -ne 0) {
    & git -C $proj checkout -- reports/ 2>&1 | Out-File $out -Append -Encoding utf8
    & git -C $proj commit -m "screener: report $(Get-Date -Format 'yyyy-MM-dd')" 2>&1 | Out-File $out -Append -Encoding utf8
    & git -C $proj pull --rebase -X theirs origin main 2>&1 | Out-File $out -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
            # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
            # add -A 금지: 추적 외 개인 파일까지 스테이징해 공개 저장소로
            # 유출될 수 있다 (2026-08-03 run_flows 실사고). add -u 로 충분하다.
            & git -C $proj checkout --theirs -- . 2>&1 | Out-File $out -Append -Encoding utf8
            & git -C $proj add -u 2>&1 | Out-File $out -Append -Encoding utf8
            & git -C $proj -c core.editor=true rebase --continue 2>&1 | Out-File $out -Append -Encoding utf8
        }
        else {
            "[git] ERROR: pull --rebase failed before rebase started - manual check needed" | Out-File $out -Append -Encoding utf8
        }
    }
    # 봇 커밋이 fetch~push 사이에 들어오면 push 가 경쟁 실패한다
    # ("cannot lock ref", 2026-08-03 실사고). pull 후 최대 3회 재시도.
    # $pushed 플래그 필수: 루프 마지막 명령이 pull 이라 $LASTEXITCODE 로
    # 판정하면 3회 모두 실패해도 성공으로 오판한다.
    $pushed = $false
    for ($try = 1; $try -le 3; $try++) {
        & git -C $proj push origin main 2>&1 | Out-File $out -Append -Encoding utf8
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        "[git] push rejected (attempt $try/3) - retrying" | Out-File $out -Append -Encoding utf8
        Start-Sleep -Seconds 5
        & git -C $proj pull --rebase -X theirs origin main 2>&1 | Out-File $out -Append -Encoding utf8
    }
    if ($pushed) {
        "[git] GitHub push OK" | Out-File $out -Append -Encoding utf8
        # push 이벤트가 배포를 못 거는 경우가 있어 명시적으로 배포 트리거
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj 'trigger_deploy.ps1')
        "[git] deploy trigger exit: $LASTEXITCODE" | Out-File $out -Append -Encoding utf8
    } else {
        "[git] PUSH FAILED - check log" | Out-File $out -Append -Encoding utf8
    }
} else {
    "[git] no report change - skip push" | Out-File $out -Append -Encoding utf8
}

exit $p.ExitCode
