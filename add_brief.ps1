# =====================================================================
# 시황 브리핑 원클릭 게시 (클립보드 -> briefs\YYYY-MM-DD.md -> commit/push)
#  사용법: 시황글을 복사(Ctrl+C)한 뒤 이 스크립트(또는 시황올리기.bat)를 실행.
#  NOTE: ASCII-only comments; file saved with BOM so Windows PowerShell 5.1
#        parses the Korean strings correctly.
# =====================================================================
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

# --- 브랜치 가드: 게시는 항상 main 기준 (실습 브랜치에 있으면 전환) ---
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { git rebase --quit 2>$null }
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    Write-Host ("현재 브랜치 '{0}' -> main 으로 전환합니다." -f $branch) -ForegroundColor Yellow
    git checkout -f main 2>$null | Out-Null
    if ((git rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') {
        Write-Host "main 전환 실패 - 게시를 중단합니다." -ForegroundColor Red
        Read-Host "엔터를 누르면 종료"; exit 1
    }
}

# 1) 클립보드 읽기
$text = Get-Clipboard -Raw
if ([string]::IsNullOrWhiteSpace($text)) {
    Write-Host "클립보드가 비어 있습니다. 시황글을 먼저 복사(Ctrl+C)한 뒤 다시 실행하세요." -ForegroundColor Red
    Read-Host "엔터를 누르면 종료"; exit 1
}

# 2) 날짜 입력 (엔터=오늘). 2026-07-16 또는 20260716 허용
$today = (Get-Date).ToString('yyyy-MM-dd')
$date = Read-Host "발행 날짜 (그냥 엔터 = 오늘 $today)"
if ([string]::IsNullOrWhiteSpace($date)) { $date = $today }
if ($date -match '^\d{8}$') { $date = $date.Substring(0,4) + '-' + $date.Substring(4,2) + '-' + $date.Substring(6,2) }
if ($date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    Write-Host "날짜 형식이 올바르지 않습니다. 예) 2026-07-16" -ForegroundColor Red
    Read-Host "엔터를 누르면 종료"; exit 1
}

# 3) 파일 저장 (UTF-8, BOM 없음)
$md = Join-Path $proj ("briefs\{0}.md" -f $date)
[System.IO.File]::WriteAllText($md, $text, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("저장 완료: briefs\{0}.md  ({1:N0}자)" -f $date, $text.Length) -ForegroundColor Green

# 3.5) 서식 자동 복구: 줄바꿈 소실 등 깨진 붙여넣기를 표준 형식으로 재조립
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
& $py -u (Join-Path $proj 'format_brief.py') $md
if ($LASTEXITCODE -ne 0) {
    Write-Host "서식 자동 복구를 건너뜁니다(구조 인식 실패) - 원본 그대로 게시. 페이지가 이상하면 알려주세요." -ForegroundColor Yellow
}

# 3.7) 페이지 생성 (md -> docs/brief_*.html + 허브 + 홈 카드)
# 2026-08-22 브랜치 서빙 전환으로 Actions 의 push 트리거가 없어져, HTML 은
# 여기(로컬)서 만든다. push = 배포라 러너를 기다릴 필요도 없다.
# (2026-08-24 실사고: md 만 push 되고 HTML 미생성 → '오늘의 뉴스' 미갱신)
$env:PYTHONUTF8 = '1'
& $py -u (Join-Path $proj 'make_brief.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host "페이지 생성 실패 - 클로드에게 알려주세요. (md 는 게시 계속)" -ForegroundColor Red
}
& $py -u -c "import make_summary; make_summary.build()"

# 4) 커밋 & 푸시 (브랜치 서빙: push 가 곧 배포)
$ymd = $date -replace '-', ''
git add ("briefs/{0}.md" -f $date) docs/
git diff --staged --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "변경 내용이 없습니다 (같은 날짜에 동일한 글)." -ForegroundColor Yellow
    # 이전 실행이 커밋만 남기고 push 에 실패했을 수 있다 - 밀린 커밋이 있으면 마저 민다
    git fetch origin main 2>&1 | Out-Null
    $ahead = (git rev-list --count "origin/main..HEAD" 2>$null)
    if ($ahead -and [int]$ahead -gt 0) {
        Write-Host ("푸시되지 않은 커밋 {0}개 발견 - 지금 게시합니다." -f $ahead) -ForegroundColor Yellow
        git pull --rebase --autostash origin main | Out-Null
        git push origin main 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "게시 완료!" -ForegroundColor Green }
        else { Write-Host "푸시 실패 - 클로드에게 알려주세요." -ForegroundColor Red }
    }
} else {
    git commit -m ("시황 브리핑 {0} 게시" -f $date) | Out-Null
    # --autostash: 다른 자동화가 남긴 미커밋 파일이 있어도 pull 이 막히지 않게
    # (2026-08-18 실사고: unstaged 파일로 pull 실패 -> push 거절인데 '게시 완료' 출력)
    # 봇 커밋과의 push 경쟁은 pull 후 최대 3회 재시도로 해소한다.
    $pushed = $false
    for ($try = 1; $try -le 3; $try++) {
        git pull --rebase --autostash origin main | Out-Null
        git push origin main 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Write-Host ("push 재시도 {0}/3..." -f $try) -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
    Write-Host ""
    if ($pushed) {
        Write-Host "게시 완료! 1~2분 뒤 아래 주소에서 확인:" -ForegroundColor Green
        Write-Host ("  https://pdhman.github.io/report-summary/brief_{0}.html" -f $ymd) -ForegroundColor Cyan
        Write-Host "  (목록: https://pdhman.github.io/report-summary/ 상단 '시황' 배너)"
    } else {
        Write-Host "푸시 실패 - 게시되지 않았습니다. 커밋은 로컬에 남아 있으니" -ForegroundColor Red
        Write-Host "잠시 후 이 스크립트를 다시 실행하거나 클로드에게 알려주세요." -ForegroundColor Red
    }
}
Read-Host "엔터를 누르면 종료"
