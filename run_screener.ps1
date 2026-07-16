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
$err = Join-Path $logDir "screener_$stamp.err.log"

# 실행 (자식 프로세스가 UTF-8 바이트를 그대로 파일에 기록)
$p = Start-Process -FilePath $py -ArgumentList "-u `"$script`"" `
    -WorkingDirectory $proj -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err

# 에러 로그가 비어있으면 삭제
if ((Test-Path $err) -and ((Get-Item $err).Length -eq 0)) { Remove-Item $err }

# --- 보고서(HTML) 생성 및 브라우저로 열기 ---
& cmd /c "`"$py`" make_report.py >> `"$out`" 2>&1"
$report = Get-ChildItem (Join-Path $proj 'reports\report_*.html') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 1
if ($report) { Start-Process $report.FullName }

# --- Git 아카이브에 이력 커밋 ---
& cmd /c "`"$py`" git_archive.py >> `"$out`" 2>&1"

# --- GitHub(report_summary origin/main)에 리포트 게시 ---
# report_*.html + index.html 만 push. push 되면 GitHub Actions(daily-insights)가
# reports/** 트리거로 사이트를 재배포한다. (봇 커밋은 [skip ci]라 루프 없음)
& git -C $proj add "reports/report_*.html" "reports/index.html" 2>&1 | Out-File $out -Append -Encoding utf8
& git -C $proj diff --staged --quiet
if ($LASTEXITCODE -ne 0) {
    & git -C $proj checkout -- reports/ 2>&1 | Out-File $out -Append -Encoding utf8
    & git -C $proj commit -m "screener: report $(Get-Date -Format 'yyyy-MM-dd')" 2>&1 | Out-File $out -Append -Encoding utf8
    & git -C $proj pull --rebase origin main 2>&1 | Out-File $out -Append -Encoding utf8
    & git -C $proj push origin main 2>&1 | Out-File $out -Append -Encoding utf8
    "[git] GitHub push OK" | Out-File $out -Append -Encoding utf8
} else {
    "[git] no report change - skip push" | Out-File $out -Append -Encoding utf8
}

exit $p.ExitCode
