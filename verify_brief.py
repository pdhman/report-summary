# -*- coding: utf-8 -*-
"""시황 게시 확인 — 시황올리기(add_brief.ps1) 마지막 단계에서 백그라운드로 실행된다.

실제 사이트(GitHub Pages)에 그날 시황이 반영됐는지 최대 10분 동안 확인하고 텔레그램 개인 DM 으로 알린다.
  ① brief_YYYYMMDD.html 에 시황 본문 첫 문장이 있고 '아직 게시되지 않았습니다' 안내문이 아닐 것
  ② 홈(index.html) '오늘의 뉴스' 카드 날짜가 그 날짜일 것
2026-10-22 도입: 게시 직후 다른 자동 작업이 페이지를 빈 안내문으로 덮어써도 조용히 지나간 사고가 있었다.

  python verify_brief.py 2026-10-22            # 확인 + DM
  python verify_brief.py 2026-10-22 --no-send  # 확인만(출력)
"""
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SITE = "https://pdhman.github.io/report-summary"
STUB = "아직 게시되지 않았습니다"
WAIT_MIN, STEP_SEC = 10, 30


def text_of(h: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def brief_snippet(ymd: str) -> str:
    """로컬에서 만든 페이지의 브리핑 본문 첫 문장 앞 20자 — 사이트 페이지와 대조하는 표식."""
    p = BASE / "docs" / f"brief_{ymd}.html"
    if not p.exists():
        return ""
    m = re.search(r'</header>\s*<article class="prose">(.*?)</article>', p.read_text(encoding="utf-8"), re.S)
    if not m or STUB in m.group(1):
        return ""
    t = text_of(m.group(1))
    return t[:20]


def check(date: str, snippet: str) -> tuple[bool, bool, str]:
    ymd = date.replace("-", "")
    v = int(time.time())
    page_ok = card_ok = False
    note = ""
    try:
        r = requests.get(f"{SITE}/brief_{ymd}.html?v={v}", timeout=20)
        body = text_of(r.text) if r.ok else ""
        page_ok = r.ok and STUB not in body and (snippet in body if snippet else True)
        if not r.ok:
            note = f"페이지 HTTP {r.status_code}"
        elif STUB in body:
            note = "페이지가 '아직 게시되지 않았습니다' 안내문"
        elif snippet and snippet not in body:
            note = "페이지에 오늘 시황 본문이 없음"
    except Exception as e:
        note = f"페이지 조회 실패: {e}"
    try:
        r = requests.get(f"{SITE}/index.html?v={v}", timeout=20)
        m = re.search(r'sc-title">오늘의 뉴스[^<]*</span>\s*<span class="sc-date">([0-9-]+)', r.text)
        card = m.group(1) if m else "?"
        card_ok = card == date
        if not card_ok:
            note += (" · " if note else "") + f"홈 카드 날짜 {card}"
    except Exception as e:
        note += (" · " if note else "") + f"홈 조회 실패: {e}"
    return page_ok, card_ok, note


def dm(msg: str):
    sys.path.insert(0, str(BASE / "telegram"))
    from send_summary import resolve_targets, send
    cfg = json.loads((BASE / "telegram" / "config.json").read_text(encoding="utf-8"))
    send(cfg, resolve_targets(cfg, "dm"), msg)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sys.argv[1]):
        raise SystemExit("사용법: python verify_brief.py YYYY-MM-DD [--no-send]")
    date, no_send = sys.argv[1], "--no-send" in sys.argv
    ymd = date.replace("-", "")
    snippet = brief_snippet(ymd)
    # 지난 날짜를 뒤늦게 올리면 홈 카드는 원래 최신 날짜를 보여 준다 — 최신 시황일 때만 카드를 본다
    dates = sorted(f.stem for f in (BASE / "briefs").glob("????-??-??.md"))
    need_card = not dates or date >= dates[-1]
    t0, note = time.time(), ""
    ok = False
    while time.time() - t0 < WAIT_MIN * 60:
        page_ok, card_ok, note = check(date, snippet)
        if not need_card:
            card_ok, note = True, re.sub(r"( · )?홈 카드 날짜 [0-9?-]+", "", note).strip(" ·")
        print(f"[{time.strftime('%H:%M:%S')}] 페이지 {'OK' if page_ok else '대기'} · 홈 카드 {'OK' if card_ok else '대기'} {note}", flush=True)
        if page_ok and card_ok:
            ok = True
            break
        time.sleep(STEP_SEC)
    mins = (time.time() - t0) / 60
    url = f"{SITE}/brief_{ymd}.html"
    if ok:
        msg = f"✅ <b>{date} 시황 게시 완료</b>\n뉴스 페이지·홈 카드 반영 확인 ({mins:.0f}분)\n{url}"
    else:
        msg = (f"⚠️ <b>{date} 시황이 사이트에 반영되지 않았습니다</b>\n{note or '원인 미상'}\n"
               f"{WAIT_MIN}분 기다렸지만 확인되지 않음 — 클로드에게 '시황 반영 안 됐다'고 알려주세요.\n{url}")
    print(msg)
    if not no_send:
        dm(msg)


if __name__ == "__main__":
    main()
