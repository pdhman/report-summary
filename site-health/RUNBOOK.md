# 알파노트 자가 점검 — 조치 설명서(RUNBOOK)

자가 점검 에이전트(Claude 예약 작업 `site-health-check` 평일 10:00 · `site-health-check-pm` 평일 17:30)가 따르는 절차다.
주간 보고(`site-health-weekly`, 토 10:00)는 `history.jsonl`(점검 기록·발송 원문)과 그 주 '자가 점검:' 커밋으로 한 주를 정리한다.
`site_check.py` 가 '눈'(문제 목록만 냄), 에이전트가 '손'(원인 확인 → 안전한 조치 → 재확인 → 보고).
2026-11-20 사용자 결정: 하루 2번 · **안전한 것만 자동 조치**(재생성·재게시·예약 작업 재실행) · 코드 수정이 필요하면 원인만 보고 · **문제 있을 때만** 텔레그램 dm · 홈 카드+주요 페이지+예약 작업+git 점검.

## 0. 시작

```
cd "C:/Users/SAMSUNG/Desktop/클로드코드"
python -X utf8 site-health/site_check.py --json --log first
```
- `--log` 는 주간 보고 재료(history.jsonl)를 남긴다. 처음 점검은 `--log first`, 조치 후 재확인은 `--log final`.
- 17:30 실행은 레버리지·블로그(17:30)·한국 사이클(17:45)이 도는 중일 수 있다 → 1절대로 기다린 뒤 판단.
- 종료 3(휴장일) 또는 0(이상 없음) → **아무것도 하지 않고 끝낸다**(텔레그램 없음). 보고는 한 줄.
- 종료 1 → 아래 표대로 문제마다 원인 확인·조치. 같은 원인을 공유하는 문제는 한 번에 처리한다.

## 1. 손대기 전 공통 확인

- **진행 중인 작업이 있으면 기다린다**: 이 폴더의 예약 작업이 `Running` 이거나 `.git/index.lock` 이 있으면 최대 10분 기다린 뒤 다시 `site_check.py` 를 돌린다(작업이 끝나면 저절로 풀리는 문제가 많다). 예: 18:00 점검 때 17:45 작업이 아직 도는 중.
- **실행 시각 전이면 문제 아님**: 카드의 담당 작업이 오늘 아직 정규 시각이 안 됐으면(예: 수능일처럼 장 마감이 늦어진 날) 보고만 하고 재실행하지 않는다.
- 작업 상태 보기(PowerShell): `Get-ScheduledTask -TaskName <이름> | Get-ScheduledTaskInfo | Select LastRunTime,LastTaskResult`

## 2. 문제별 조치

| 문제(kind) | 원인 확인 | 허용된 조치 |
|---|---|---|
| `card` 날짜 오래됨, 담당이 윈도우 예약 작업 | 담당 작업의 오늘 LastRunTime·결과 코드, `logs/` 의 그 작업 최신 로그 | ① 오늘 안 돌았거나 실패 → `Start-ScheduledTask -TaskName <이름>` **1회**, 끝날 때까지 기다림(차트데이터 35분 등 길 수 있음) ② 작업은 성공했는데 카드만 옛날 → push 경쟁으로 홈이 덮인 것. 아래 '홈 재생성' |
| `card` 담당이 `(GitHub Actions)` (리포트 인사이트·폴리마켓) | `gh run list --workflow "<이름>" -L 3` | 실패·누락이면 `gh workflow run "<이름>"` **1회**. 인사이트 = `Daily Report Insights`, 폴리마켓 = `Polymarket Macro Watch` |
| `card` 오늘의 뉴스 / `brief` / GitHub 에 시황 md 없음 | `briefs/오늘.md` 가 PC 에 있는지, `git status briefs/` | PC 에 있으면(커밋 누락) → '시황 재게시'. **PC 에도 없으면 사용자가 아직 안 올린 것** → 조치 없이 보고("오늘 시황 미게시") |
| `gaze` 시장의 시선 없음 | — | 조치 없음, 보고만(별도 Claude 예약 작업 `market-center-of-gravity` 담당) |
| `close` 마감시황 없음(저녁) | — | 조치 없음, 보고만(Claude 예약 작업 `market-close-brief` 담당) |
| `page` 페이지 이상 | 해당 페이지 담당 작업 확인 | 담당 예약 작업 재실행 1회. 담당이 없으면 보고만 |
| `task` 예약 작업 실패 | 그 작업의 `logs/` 최신 로그 끝 40줄 | 네트워크·push 거절·타임아웃 같은 **일시적 원인이면 재실행 1회**. 코드 오류(Traceback 등)면 재실행 말고 원인만 보고 |
| `git` 푸시 안 된 커밋 | `git log origin/main..HEAD --oneline` | 전부 자동화 커밋이면 `git pull --rebase --autostash origin main` 후 `git push origin main` |
| `git` 리베이스 멈춤 · main 아님 | — | **조치 없음, 보고만**(사람 작업 중일 수 있음) |
| `git` 추적 파일 미커밋 수정 | 파일 목록 | **조치 없음, 보고만** — 사용자가 편집 중일 수 있다. "17:30 레버리지 자동화가 실패할 수 있으니 커밋 필요"라고 알림 |

### 홈 재생성(카드만 옛날일 때)
```
python -X utf8 -c "import make_summary; make_summary.build()"
git add docs/index.html
git commit -m "자가 점검: 홈 카드 재생성"
git pull --rebase --autostash -X theirs origin main
git push origin main
```
(리베이스 중 `-X theirs` = 방금 만든 우리 커밋 쪽을 채택. 홈은 재생성물이라 안전.)

### 시황 재게시(PC 에 md 는 있는데 GitHub 에 없을 때)
```
python -X utf8 make_brief.py
python -X utf8 -c "import make_summary; make_summary.build()"
git add briefs/오늘.md docs/
git commit -m "자가 점검: 시황 브리핑 오늘 재게시"
git pull --rebase --autostash -X theirs origin main
git push origin main
```

## 3. 절대 하지 않는 것
- 코드(.py/.ps1/.html 원본 템플릿) 수정. 코드가 원인이면 **원인과 고칠 곳만 보고**.
- `git add -A`, `git checkout -f`, `git checkout -- <파일>`, `git reset --hard`, `git push --force`, `git rebase --abort/--quit`, 파일 삭제.
- `config.yaml`, `secrets.json`, `telegram/config.json` 커밋. 위 표에 적힌 파일 외 스테이징.
- 같은 작업을 2번 넘게 재실행. 재실행 후에도 안 되면 보고로 넘긴다.
- 텔레그램 공개 채널 발송(`notify.py` 는 개인 dm 만 보낸다. send_summary 등을 직접 부르지 않는다).
- 사용자가 직접 해야 하는 일(시황 올리기·블로그 글 쓰기)을 대신 만들어 넣기.

## 4. 마무리 — 재확인과 보고
1. `python -X utf8 site-health/site_check.py --log final` 을 다시 돌려 남은 문제를 확인한다(조치를 하나도 안 했으면 생략).
2. 메시지를 `site-health/last_msg.txt` 에 Write 로 쓴다(텔레그램 한 통, 15줄 이내, 쉬운 말):
   ```
   🩺 알파노트 자가 점검 (11/20 저녁)
   발견 3건 → 자동 해결 2건 · 확인 필요 1건

   ✅ 수급 동향 카드 날짜 → 수급 작업 재실행, 11-20 반영
   ✅ 홈 카드 덮어쓰기 → 홈 재생성·재게시
   ⚠️ 마감시황 요약 없음 → 16:45 예약 작업 확인 필요
   ```
   - 해결된 것은 ✅, 사용자가 봐야 하는 것은 ⚠️ + 무엇을 하면 되는지 한 줄.
3. 보낸다: `python -X utf8 site-health/notify.py --file site-health/last_msg.txt --key "<am|pm>:<남은 ⚠️ 문제 제목들, 없으면 해결한 문제 제목들>"`
   - 종료 4 = 오늘 같은 내용 이미 발송 → 그대로 끝(재발송 금지).
4. 끝에 한국어 2~3줄 보고(발견·조치·남은 문제).

## 5. 주간 보고(토 10:00, `site-health-weekly`)
1. `site-health/history.jsonl` 에서 최근 7일 기록을 읽는다(`phase` first=처음 점검, final=조치 후, sent=보낸 메시지 원문, status ok/issues/holiday).
2. `git log --since="7 days ago" --oneline --grep="자가 점검"` 으로 에이전트가 직접 한 커밋을 본다.
3. 텔레그램 한 통(20줄 이내, 쉬운 말)으로 쓴다:
   ```
   🗓 알파노트 주간 점검 보고 (11/16~11/20)
   점검 10회 · 이상 없음 7회 · 문제 발견 3회

   🔧 고친 것
   · 11/18 저녁 수급 동향 카드 날짜 → 수급 작업 재실행으로 해결

   ⚠️ 남은 것 / 확인 필요
   · 없음

   🔁 반복된 문제
   · '오늘의 뉴스' 날짜 2회 — 근본 원인 점검 권장(시황올리기 게시 단계)
   ```
   - 점검 기록이 하나도 없으면(앱이 꺼져 있어 안 돈 경우 포함) "이번 주 점검 기록 없음 — 앱이 꺼져 있었는지 확인" 이라고 쓴다.
   - 같은 문제가 2일 이상 나오면 '반복된 문제'에 넣고, 근본 원인을 고칠 곳(파일·작업)을 한 줄로 제안한다. 직접 고치지는 않는다.
4. `site-health/last_msg.txt` 에 쓰고 `python -X utf8 site-health/notify.py --file site-health/last_msg.txt --key "weekly:<그 주 월요일 날짜>"` 로 보낸다. 주간 보고는 문제가 0건이어도 보낸다.
