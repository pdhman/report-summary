# 작업 스케줄러 등록: PriceAlert_10min
# 평일(월~금) 09:00부터 10분 간격, 6시간 35분 동안 반복 → 마지막 틱 15:30.
# (파이썬 쪽에도 장시간 가드가 있어 이중 안전장치)
#
# 실행:  .\register_task.ps1        (관리자 권한 불필요 — 현재 사용자 태스크)
# 해제:  Unregister-ScheduledTask -TaskName 'PriceAlert_10min' -Confirm:$false

$taskName = 'PriceAlert_10min'
$wrapper  = Join-Path $PSScriptRoot 'run_price_alert.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""

# 주간 트리거(월~금 09:00)에 반복(Repetition)을 붙인다.
# -Weekly 트리거는 -RepetitionInterval 을 직접 받지 못하므로
# -Once 트리거에서 Repetition 객체만 복사하는 표준 패턴을 쓴다.
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 09:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 09:00 `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 35)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "등록 완료: $taskName (평일 09:00~15:30, 10분 간격)"
Write-Host "즉시 테스트:  Start-ScheduledTask -TaskName '$taskName'"
