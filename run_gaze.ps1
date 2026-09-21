# =====================================================================
# '시장의 시선' 게시 (Claude 예약 작업 market-center-of-gravity 가 호출)
#  - 예약 작업이 docs\data\gaze\YYYY-MM-DD.json 을 쓴 뒤 이 스크립트를 부른다:
#    뉴스 브리핑 페이지(시선 블록 포함)·알파노트 홈 재생성 → 커밋·푸시(=배포)
#    → 텔레그램 개인 대화방 발송.
#  - git 패턴은 run_market.ps1 과 동일 (브랜치 가드, docs 만 add, autostash 리베이스).
#  - 발송은 푸시 뒤에: 메시지의 페이지 링크가 살아 있어야 한다. 푸시가 실패해도
#    발송은 한다(아침 메시지가 핵심) — 종료 코드로 구분: 0 정상 / 2 발송됨·게시 실패 / 1 실패.
# =====================================================================

$proj = $PSScriptRoot
Set-Location $proj

# --- 브랜치 가드: 자동화는 항상 main 기준 (-f 금지 — run_market.ps1 주석 참조) ---
if (Test-Path (Join-Path $proj '.git/rebase-merge')) { git rebase --quit 2>$null }
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') {
    git checkout main 2>$null | Out-Null
    if ((git rev-parse --abbrev-ref HEAD 2>$null) -ne 'main') {
        "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) [gaze] branch '$branch' -> main FAILED (uncommitted edits in the way?) - abort, nothing discarded" |
            Add-Content -Path (Join-Path $proj 'logs\branch_guard.log') -Encoding UTF8 -ErrorAction SilentlyContinue
        exit 1
    }
}

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe'

$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("gaze_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Write-Log($m) { "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8; Write-Host $m }

$published = $true
try {
    # 뉴스 브리핑 페이지(시선 블록) + 알파노트 홈 재생성
    & $py -X utf8 -u (Join-Path $proj 'make_brief.py') 2>&1 | Add-Content -Path $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: make_brief exit $LASTEXITCODE"; $published = $false }

    if ($published) {
        git add docs 2>&1 | Add-Content -Path $log -Encoding UTF8
        git diff --staged --quiet
        if ($LASTEXITCODE -ne 0) {
            git commit -m ("gaze: {0:yyyy-MM-dd} 시장의 시선 갱신" -f (Get-Date)) 2>&1 | Add-Content -Path $log -Encoding UTF8
            git pull --rebase --autostash -X theirs origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
            if ($LASTEXITCODE -ne 0) {
                if (Test-Path (Join-Path $proj '.git/rebase-merge')) {
                    # 생성물 충돌만 로컬 본 채택(add -A 금지 — run_market.ps1 주석 참조)
                    git checkout --theirs -- . 2>&1 | Add-Content -Path $log -Encoding UTF8
                    git add -u 2>&1 | Add-Content -Path $log -Encoding UTF8
                    git -c core.editor=true rebase --continue 2>&1 | Add-Content -Path $log -Encoding UTF8
                }
                else {
                    Write-Log 'ERROR: pull --rebase failed before rebase started - manual check needed'
                    $published = $false
                }
            }
            if ($published) {
                git push origin main 2>&1 | Add-Content -Path $log -Encoding UTF8
                if ($LASTEXITCODE -ne 0) { Write-Log 'ERROR: git push failed'; $published = $false }
                else { Write-Log 'OK: pushed gaze' }
            }
        }
        else {
            Write-Log 'OK: no change, nothing to push'
        }
    }

    # 텔레그램 발송 (개인 대화방만. 같은 날 중복은 send_gaze.py 가 막는다)
    & $py -X utf8 -u (Join-Path $proj 'telegram\send_gaze.py') --to dm 2>&1 | Tee-Object -Variable sendOut | Add-Content -Path $log -Encoding UTF8
    $sendOut | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: send_gaze exit $LASTEXITCODE"; exit 1 }

    if ($published) { exit 0 } else { exit 2 }
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
