# -*- coding: utf-8 -*-
"""알파노트 자가 점검 — '눈' 역할. 실제 사이트와 PC 상태를 보고 문제 목록만 낸다(고치지 않는다).

자가 점검 에이전트(Claude 예약 작업 site-health-check, 평일 10:00·18:00)가 이 스크립트를 먼저 돌리고,
문제가 있을 때만 RUNBOOK.md 대로 조치한다. 정상이면 에이전트는 조용히 끝난다.

  python -X utf8 site-health/site_check.py            # 지금 시각 기준 자동(am: 15시 전, pm: 15시 후)
  python -X utf8 site-health/site_check.py --slot pm  # 저녁 기준으로 강제
  python -X utf8 site-health/site_check.py --json     # 기계용 JSON

종료 코드: 0 정상 · 1 문제 있음 · 3 휴장일(점검 생략)

점검 항목
  ① 홈 카드 날짜: 카드마다 '이 시각이면 최소 이 날짜여야 한다'는 기대값과 비교
  ② 주요 페이지: HTTP 200 · 빈 안내문('아직 게시되지 않았습니다') 아님 · 너무 작지 않음
  ③ 오늘 시황 원본 md / 마감시황 md / 시장의 시선 json 이 GitHub 에 있는지
  ④ 작업 스케줄러: 이 폴더의 예약 작업 중 오늘 실행됐는데 결과 코드가 실패인 것
  ⑤ 로컬 git: 리베이스 멈춤 · main 아님 · 푸시 안 된 커밋 · 추적 파일 미커밋 수정(→ 17:30 자동화 실패 원인)
"""
import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
SITE = "https://pdhman.github.io/report-summary"
STUB = "아직 게시되지 않았습니다"
UA = {"User-Agent": "Mozilla/5.0 (alphanote site-health)"}
KST = dt.timezone(dt.timedelta(hours=9))

# 카드 href → (이름, 담당 예약 작업, 기대 규칙)
#   rule: 'today' 오늘 · 'pm_today' 아침엔 직전 거래일·저녁엔 오늘 · 'prev' 직전 거래일
#         'tdays:N' 최근 N 거래일 안 · 'hours:N' N시간 안 · 'days:N' 달력 N일 안 · None 점검 안 함
CARDS = {
    "briefs.html":     ("오늘의 뉴스(시황)",   None,                      "today"),
    "x.html":          ("X 모니터링",          None,                      None),          # 수동 실행
    "screener.html":   ("오늘의 주도주",       "주도섹터_필터링_매일1530", "pm_today"),
    "strategy.html":   ("마켓 인사이트(블로그)", "투자전략_1730_수집",      "tdays:3"),     # 사용자가 글을 올려야 갱신
    "insights.html":   ("리포트 인사이트",     "(GitHub Actions)",        "today"),
    "market.html":     ("시장 온도계",         "시장건전성_1635_수집",    "pm_today"),
    "flow.html":       ("수급 동향",           "수급동향_1540_수집",      "pm_today"),
    "crypto.html":     ("크립토",              "크립토모니터_1100_수집",  "pm_today"),
    "polymarket.html": ("폴리마켓",            "(GitHub Actions)",        "hours:9"),
    "chart.html":      ("분석 탭",             None,                      None),
    "rs.html":         ("RS 스크리너",         "QuantDataWeekly",         "days:8"),
    "leverage.html":   ("시장 레버리지",       "시장레버리지_1730_수집",  "prev"),
}
PAGES = ["index.html", "briefs.html", "screener.html", "market.html", "flow.html",
         "leverage.html", "crypto.html", "insights.html", "strategy.html", "rs.html"]
MIN_BYTES = 3000

# 작업 결과 코드 중 실패가 아닌 것: 0 성공 · 267009 실행 중 · 267011 아직 안 돔 · 267014 사용자가 중지
OK_CODES = {0, 267009, 267011, 267014}


def now_kst():
    return dt.datetime.now(KST)


def trading_days(today: dt.date):
    """최근 거래일 목록(오름차순). 네이버 코스피 일봉 — 12자리 날짜 필수(10자리는 2026-10-22부터 빈 배열)."""
    s = (today - dt.timedelta(days=20)).strftime("%Y%m%d")
    e = today.strftime("%Y%m%d")
    u = (f"https://api.stock.naver.com/chart/domestic/index/KOSPI/day"
         f"?startDateTime={s}0000&endDateTime={e}2359")
    r = requests.get(u, headers=UA, timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json():
        d = str(it.get("localDate") or it.get("localTradedAt") or "")[:10].replace("-", "")
        if len(d) == 8:
            out.append(dt.date(int(d[:4]), int(d[4:6]), int(d[6:])))
    return sorted(set(out))


def get(url):
    sep = "&" if "?" in url else "?"
    return requests.get(f"{url}{sep}v={int(time.time())}", headers=UA, timeout=20)


def parse_date(s: str):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?", s.strip())
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    return dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=KST)


def card_dates(index_html: str):
    out = {}
    for m in re.finditer(r'<a class="scard" href="([^"]+)"(.*?)</a>', index_html, re.S):
        dm = re.search(r'sc-date">([^<]*)<', m.group(2))
        out[m.group(1)] = dm.group(1).strip() if dm else ""
    return out


def expected_min(rule, slot, today, prev, tdays, now):
    """규칙 → (최소 허용 datetime, 사람이 읽는 기대값)."""
    if rule == "today":
        d = today
    elif rule == "pm_today":
        d = today if slot == "pm" else prev
    elif rule == "prev":
        d = prev
    elif rule.startswith("tdays:"):
        n = int(rule.split(":")[1])
        past = [x for x in tdays if x <= today]
        d = past[-n] if len(past) >= n else past[0]
    elif rule.startswith("hours:"):
        t = now - dt.timedelta(hours=int(rule.split(":")[1]))
        return t, t.strftime("%m-%d %H:%M 이후")
    elif rule.startswith("days:"):
        d = today - dt.timedelta(days=int(rule.split(":")[1]))
    else:
        return None, ""
    return dt.datetime(d.year, d.month, d.day, tzinfo=KST), d.isoformat() + " 이상"


def origin_has(path: str) -> bool:
    r = subprocess.run(["git", "-C", str(BASE), "cat-file", "-e", f"origin/main:{path}"],
                       capture_output=True)
    return r.returncode == 0


def git(*args):
    r = subprocess.run(["git", "-C", str(BASE), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip()


def task_failures(today: dt.date):
    """이 폴더를 실행하는 예약 작업 중 오늘 돌았는데 실패 코드인 것."""
    ps = (
        "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\\' -and $_.State -ne 'Disabled' -and "
        "(($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments + ' ' + $_.WorkingDirectory }) -join ' ') -match '클로드코드' } | "
        "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ n=$_.TaskName; t=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm'); rc=$i.LastTaskResult; s=[string]$_.State } } | "
        "ConvertTo-Json -Compress"
    )
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=90)
    try:
        rows = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return [{"kind": "task", "what": "작업 스케줄러 조회 실패", "detail": (r.stderr or "")[:200]}]
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for x in rows:
        if x["t"][:10] == today.isoformat() and int(x["rc"]) not in OK_CODES:
            out.append({"kind": "task", "what": f"예약 작업 실패: {x['n']}",
                        "detail": f"{x['t']} 실행, 결과 코드 {x['rc']}", "task": x["n"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["am", "pm"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    now = now_kst()
    today = now.date()
    slot = a.slot or ("am" if now.hour < 15 else "pm")
    issues = []

    try:
        tdays = trading_days(today)
    except Exception as e:
        tdays = []
        issues.append({"kind": "env", "what": "거래일 조회 실패(네이버)", "detail": str(e)[:200]})
    if tdays and today not in tdays:
        res = {"slot": slot, "status": "holiday", "issues": []}
        print(json.dumps(res, ensure_ascii=False) if a.json else f"{today} 휴장일 — 점검 생략")
        return 3
    past = [d for d in tdays if d < today]
    prev = past[-1] if past else today - dt.timedelta(days=1)

    # ① 홈 카드
    try:
        r = get(f"{SITE}/index.html")
        cards = card_dates(r.text) if r.ok else {}
        if not r.ok:
            issues.append({"kind": "page", "what": "홈 접속 실패", "detail": f"HTTP {r.status_code}"})
    except Exception as e:
        cards = {}
        issues.append({"kind": "page", "what": "홈 접속 실패", "detail": str(e)[:200]})
    for href, (name, task, rule) in CARDS.items():
        if not rule or not cards:
            continue
        if href not in cards:
            issues.append({"kind": "card", "what": f"홈 카드 없음: {name}", "card": href, "task": task})
            continue
        got = parse_date(cards[href])
        need, label = expected_min(rule, slot, today, prev, tdays, now)
        if got is None or (need and got < need):
            issues.append({"kind": "card", "what": f"홈 카드 날짜 오래됨: {name}",
                           "detail": f"카드 '{cards[href] or '없음'}' · 기대 {label}",
                           "card": href, "task": task})

    # ② 주요 페이지
    for p in PAGES:
        try:
            r = get(f"{SITE}/{p}")
            body = r.text if r.ok else ""
            bad = (not r.ok and f"HTTP {r.status_code}") or (len(body.encode()) < MIN_BYTES and "내용이 거의 없음") \
                or (STUB in body and p != "briefs.html" and "빈 안내문")
            if bad:
                issues.append({"kind": "page", "what": f"페이지 이상: {p}", "detail": bad, "card": p})
        except Exception as e:
            issues.append({"kind": "page", "what": f"페이지 접속 실패: {p}", "detail": str(e)[:150], "card": p})
    ymd = today.strftime("%Y%m%d")
    try:
        r = get(f"{SITE}/brief_{ymd}.html")
        if not r.ok or STUB in r.text:
            issues.append({"kind": "brief", "what": "오늘 시황 페이지가 비어 있음",
                           "detail": f"brief_{ymd}.html " + (f"HTTP {r.status_code}" if not r.ok else "안내문")})
    except Exception as e:
        issues.append({"kind": "brief", "what": "오늘 시황 페이지 접속 실패", "detail": str(e)[:150]})

    # ③ GitHub 원본 파일
    git("fetch", "-q", "origin", "main")
    d = today.isoformat()
    need_files = [(f"briefs/{d}.md", "오늘 시황 원본(md)", "brief"),
                  (f"docs/data/gaze/{d}.json", "시장의 시선", "gaze")]
    if slot == "pm":
        need_files.append((f"docs/data/close/{d}.md", "마감시황 요약", "close"))
    for path, name, kind in need_files:
        if not origin_has(path):
            local = (BASE / path).exists()
            issues.append({"kind": kind, "what": f"GitHub 에 없음: {name}",
                           "detail": f"{path} (PC 에는 {'있음 — 커밋·푸시 누락' if local else '없음'})",
                           "path": path, "local": local})

    # ④ 예약 작업
    try:
        issues += task_failures(today)
    except Exception as e:
        issues.append({"kind": "task", "what": "작업 스케줄러 조회 실패", "detail": str(e)[:200]})

    # ⑤ 로컬 git
    if (BASE / ".git" / "rebase-merge").exists() or (BASE / ".git" / "rebase-apply").exists():
        issues.append({"kind": "git", "what": "git 리베이스가 멈춰 있음", "detail": ".git/rebase-merge 존재"})
    _, br = git("rev-parse", "--abbrev-ref", "HEAD")
    if br != "main":
        issues.append({"kind": "git", "what": f"현재 브랜치가 main 아님: {br}"})
    _, ahead = git("rev-list", "--count", "origin/main..HEAD")
    if ahead.isdigit() and int(ahead) > 0:
        issues.append({"kind": "git", "what": f"푸시 안 된 커밋 {ahead}개"})
    _, dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        files = [ln[3:] for ln in dirty.splitlines()]
        issues.append({"kind": "git", "what": f"추적 파일 미커밋 수정 {len(files)}개",
                       "detail": ", ".join(files[:8]), "files": files})

    res = {"checked_at": now.strftime("%Y-%m-%d %H:%M"), "slot": slot, "today": d,
           "prev_trading_day": prev.isoformat(), "status": "issues" if issues else "ok",
           "cards": cards, "issues": issues}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"[자가 점검] {res['checked_at']} ({'아침' if slot == 'am' else '저녁'} 기준, 직전 거래일 {prev})")
        if not issues:
            print("이상 없음")
        for i, x in enumerate(issues, 1):
            print(f"{i}. [{x['kind']}] {x['what']}" + (f" — {x['detail']}" if x.get("detail") else ""))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
