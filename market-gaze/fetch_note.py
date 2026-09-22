# -*- coding: utf-8 -*-
"""
키움 한지영 '장 시작 전 생각' 모닝 노트 읽기 — 시장의 시선 3단계(모닝 노트 대조)용.

공개 텔레그램 채널의 웹 미리보기(t.me/s/hedgecat0301)는 로그인 없이 최근 글 20개쯤을
HTML 로 준다. 그중 제목에 '장 시작 전 생각'이 들어간 글을 날짜(KST)로 골라 본문을 출력한다.
노트는 평일 07:40 전후에 올라온다(2026-09-23 실측 07:41) — 08:30 실행 때는 늘 있다.

  python market-gaze/fetch_note.py              # 오늘(KST) 노트
  python market-gaze/fetch_note.py --date 2026-09-22
  python market-gaze/fetch_note.py --latest     # 날짜 무관 가장 최근 노트

출력은 표준출력만 — 저장하지 않는다. 노트는 저작물이고 이 저장소는 공개라, 원문을
파일로 남기거나 gaze json 에 옮겨 적지 않는다(검증한 주장만 내 말로 요약해 쓴다).
종료 코드: 0 찾음 / 2 해당 날짜 노트 없음 / 1 가져오기 실패.
"""
import argparse
import datetime as dt
import html
import re
import sys
import urllib.request

URL = "https://t.me/s/hedgecat0301"
KEY = "장 시작 전 생각"
KST = dt.timezone(dt.timedelta(hours=9))


def _text(fragment):
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def fetch_posts():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode("utf-8", "replace")
    posts = []
    # 글 하나 = tgme_widget_message_wrap 블록. 본문 div 와 <time datetime> 을 뽑는다.
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        m_txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        m_time = re.search(r'<time datetime="([^"]+)"', block)
        m_link = re.search(r'data-post="([^"]+)"', block)
        if not (m_txt and m_time):
            continue
        when = dt.datetime.fromisoformat(m_time.group(1)).astimezone(KST)
        posts.append({"when": when, "text": _text(m_txt.group(1)),
                      "post": m_link.group(1) if m_link else ""})
    return posts


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (KST, 기본 오늘)")
    ap.add_argument("--latest", action="store_true", help="날짜 무관 가장 최근 노트")
    a = ap.parse_args()
    try:
        posts = fetch_posts()
    except Exception as e:
        print(f"[노트] 가져오기 실패: {e}")
        return 1
    notes = [p for p in posts if KEY in p["text"][:80]]
    if not a.latest:
        day = a.date or dt.datetime.now(KST).strftime("%Y-%m-%d")
        notes = [p for p in notes if p["when"].strftime("%Y-%m-%d") == day]
    if not notes:
        print(f"[노트] 해당 날짜의 '{KEY}' 글 없음 (채널 최근 글 {len(posts)}개 확인)")
        return 2
    n = notes[-1]
    print(f"[노트] {n['when']:%Y-%m-%d %H:%M} KST · t.me/{n['post']}\n")
    print(n["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
