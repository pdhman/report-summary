# =====================================================================
# 시황 브리핑 원클릭 게시 (클립보드 -> briefs\YYYY-MM-DD.md -> commit/push)
#  사용법: 시황글을 복사(Ctrl+C)한 뒤 이 스크립트(또는 시황올리기.bat)를 실행.
#  NOTE: ASCII-only comments; file saved with BOM so Windows PowerShell 5.1
#        parses the Korean strings correctly.
# =====================================================================
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

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

# 4) 커밋 & 푸시 (푸시하면 GitHub Actions가 페이지 생성 후 배포)
$ymd = $date -replace '-', ''
git add ("briefs/{0}.md" -f $date)
git diff --staged --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "변경 내용이 없습니다 (같은 날짜에 동일한 글)." -ForegroundColor Yellow
} else {
    git commit -m ("시황 브리핑 {0} 게시" -f $date) | Out-Null
    git pull --rebase origin main | Out-Null
    git push origin main
    Write-Host ""
    Write-Host "게시 완료! 1~2분 뒤 아래 주소에서 확인:" -ForegroundColor Green
    Write-Host ("  https://pdhman.github.io/report-summary/brief_{0}.html" -f $ymd) -ForegroundColor Cyan
    Write-Host "  (목록: https://pdhman.github.io/report-summary/ 상단 '시황' 배너)"
}
Read-Host "엔터를 누르면 종료"
