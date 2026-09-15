# =====================================================================
# 주도섹터 필터링 자동 실행 래퍼 (Windows 작업 스케줄러용)
#  - UTF-8 로그 저장 (한글 안 깨짐)
#  - matplotlib Agg 백엔드 → 차트 창으로 멈추지 않음
#  - 결과는 logs\screener_날짜시간.log 에 저장
# =====================================================================

param([switch]$Force)   # -Force : 시간 가드 무시하고 즉시 실행(수동 실행용)

$proj = 'C:\Users\SAMSUNG\Desktop\클로드코드'
$py   = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'
$script = '주도섹터 필터링.py'

Set-Location $proj

# --- 시간 가드: 장 마감(15:30) 전이거나 주말이면 실행하지 않는다 ---
# 주말에 PC 가 꺼져 있으면 15:35 트리거가 밀리고, StartWhenAvailable=True 때문에
# 월요일 부팅 직후 놓친 실행이 한꺼번에 돈다(2026-08-10 08:50/08:56 실사고).
# 그 시각엔 장이 열리기 전이라 직전 거래일 종가로 '오늘자' 리포트를 만들어
# 발행해버린다. 정규 15:35 실행은 그대로 두고 이런 따라잡기 실행만 막는다.
$now = Get-Date
if (-not $Force) {
    $skip = $null
    if ($now.DayOfWeek -eq 'Saturday' -or $now.DayOfWeek -eq 'Sunday') { $skip = '주말 휴장' }
    elseif ($now.TimeOfDay -lt [TimeSpan]'15:30:00') { $skip = '장 마감 전' }
    if ($skip) {
        $g = Join-Path $proj 'logs'
        if (-not (Test-Path $g)) { New-Item -ItemType Directory -Path $g | Out-Null }
        "$($now.ToString('yyyy-MM-dd HH:mm:ss')) [guard] $skip - 실행 건너뜀 (놓친 실행 따라잡기 추정)" |
            Add-Content -Path (Join-Path $g 'screener_skip.log') -Encoding UTF8
        exit 0
    }
}

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
# -f 금지: `checkout -f main` 은 이미 main 에 있어도 추적 파일의 미커밋 수정을 전부
# 버린다 (2026-09-15 15:35 실사고: 스케줄러 작업 액션의 무조건 `checkout -f main` 이
# 편집 중이던 docs/market.html 변경 13건을 지움). 전환이 막히면(사람의 수정과 충돌)
# 작업 트리를 건드리지 않고 중단해 로그로 넘긴다.
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { & git -C $proj rebase --quit 2>$null }
$branch = (& git -C $proj rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    "[guard] branch '$branch' -> main" | Out-File $out -Append -Encoding utf8
    & git -C $proj checkout main 2>&1 | Out-File $out -Append -Encoding utf8
    if ((& git -C $proj rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') {
        "[guard] FAILED to switch to main (uncommitted edits in the way?) - abort, nothing discarded" | Out-File $out -Append -Encoding utf8
        exit 1
    }
}
$err = Join-Path $logDir "screener_$stamp.err.log"

# --- 실행 전 이미 수정돼 있던 docs/ 추적 파일(= 사람이 편집 중) 기록 ---
# 아래 파이프라인(make_report → make_summary.build)은 자기 산출물 외에도 docs/ 의
# 다른 페이지(leverage.html, flow.html, flow_data.js, x.html, x_*.html, crypto.html)를
# 부수적으로 재생성한다. 예전엔 이를 치우려고 `git checkout -- docs/` 로 docs/ 전체를
# 되돌렸고, 편집 중이던 docs/market.html 변경까지 함께 날렸다(2026-09-15 실사고).
# 지금은 이 목록에 없는, 즉 이 실행이 새로 건드린 파일만 되돌린다(아래 [git] 단계).
$preEdited = @(& git -C $proj -c core.quotepath=false diff --name-only HEAD -- docs 2>$null | Where-Object { $_ })

# 실행 (자식 프로세스가 UTF-8 바이트를 그대로 파일에 기록)
$p = Start-Process -FilePath $py -ArgumentList "-u `"$script`"" `
    -WorkingDirectory $proj -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err

# 에러 로그가 비어있으면 삭제
if ((Test-Path $err) -and ((Get-Item $err).Length -eq 0)) { Remove-Item $err }

# --- 보고서(HTML) 생성 및 브라우저로 열기 ---
& cmd /c "`"$py`" enrich_fundamentals.py >> `"$out`" 2>&1"
& cmd /c "`"$py`" make_report.py >> `"$out`" 2>&1"
$report = Get-ChildItem (Join-Path $proj 'docs\report_*.html') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 1
if ($report) { Start-Process $report.FullName }

# --- Git 아카이브에 이력 커밋 ---
& cmd /c "`"$py`" git_archive.py >> `"$out`" 2>&1"

# --- GitHub(report_summary origin/main)에 리포트 게시 ---
# report_*.html + 허브/요약 + 스크리너 xlsx push. push 되면 GitHub Actions(daily-insights)가
# docs/** 트리거로 사이트를 재배포한다. (봇 커밋은 [skip ci]라 루프 없음)
# xlsx 는 러너의 요약 대시보드(주도주 카드) 생성에 필요하다.
& git -C $proj add "docs/report_*.html" "docs/index.html" "docs/screener.html" "종목탐색_TOP30.xlsx" 2>&1 | Out-File $out -Append -Encoding utf8
# 대기본(pending)은 생성·삭제가 오가므로 그 한 경로만 -A 로 스테이징한다.
# 러너도 이 파일을 우선 읽어야 잠긴 회차의 결과가 사이트에 반영된다.
& git -C $proj add -A -- "종목탐색_TOP30.pending.xlsx" 2>&1 | Out-File $out -Append -Encoding utf8
& git -C $proj diff --staged --quiet
if ($LASTEXITCODE -ne 0) {
    # 부수 재생성물 정리: 이 실행이 새로 바꿨지만 커밋 대상이 아닌 docs/ 추적 파일만
    # 되돌린다. 실행 전부터 수정돼 있던 파일($preEdited)은 사람의 편집이므로 보존하고,
    # 미추적 파일은 checkout 이 원래 건드리지 않는다. (docs/ 전체 되돌리기 금지)
    $nowEdited = @(& git -C $proj -c core.quotepath=false diff --name-only -- docs 2>$null | Where-Object { $_ })
    $revert = @($nowEdited | Where-Object { $preEdited -notcontains $_ })
    if ($revert.Count -gt 0) {
        "[git] revert side-generated: $($revert -join ', ')" | Out-File $out -Append -Encoding utf8
        & git -C $proj checkout -- $revert 2>&1 | Out-File $out -Append -Encoding utf8
    }
    if ($preEdited.Count -gt 0) {
        "[git] keep pre-existing edits: $($preEdited -join ', ')" | Out-File $out -Append -Encoding utf8
    }
    & git -C $proj commit -m "screener: report $(Get-Date -Format 'yyyy-MM-dd')" 2>&1 | Out-File $out -Append -Encoding utf8
    # --autostash: 보존한 사람의 미커밋 변경이 있어도 잠시 치웠다가 리베이스 후 복원
    # (없으면 "unstaged changes" 로 pull 이 시작조차 못 한다)
    & git -C $proj pull --rebase --autostash -X theirs origin main 2>&1 | Out-File $out -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
            # 생성물 충돌로 리베이스가 멈춘 경우에만 로컬 본을 채택해 무인 복구.
            # add -A 금지: 추적 외 개인 파일까지 스테이징해 공개 저장소로
            # 유출될 수 있다 (2026-08-03 run_flows 실사고). add -u 로 충분하다.
            # `--theirs -- .` 의 실제 영향은 충돌 난 추적 파일뿐이다: 리베이스 중엔
            # 사람의 미커밋 변경이 작업 트리에 없고(--autostash 로 치워져 있음),
            # 미추적 파일은 checkout 이 건드리지 않는다.
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
        & git -C $proj pull --rebase --autostash -X theirs origin main 2>&1 | Out-File $out -Append -Encoding utf8
    }
    if ($pushed) {
        "[git] GitHub push OK" | Out-File $out -Append -Encoding utf8
    } else {
        "[git] PUSH FAILED - check log" | Out-File $out -Append -Encoding utf8
    }
} else {
    "[git] no report change - skip push" | Out-File $out -Append -Encoding utf8
}

exit $p.ExitCode
