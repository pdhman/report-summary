# -*- coding: utf-8 -*-
"""마감시황 요약(docs/data/close/YYYY-MM-DD.md) → 뉴스 브리핑 페이지의 '📝 마감시황' 탭 조각.

요약 파일은 평일 16:45 Claude 예약 작업 market-close-brief 가 쓴다(형식은 market-close/README.md).
텔레그램(telegram/send_close.py)도 같은 파일을 읽으므로 두 곳 내용이 항상 같다.
make_brief.py 가 날짜별 페이지와 허브(briefs.html)에 이 조각을 넣는다 (2026-10-21).
"""
import datetime
import glob
import html
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "docs", "data", "close")
WEEKDAYS = "월화수목금토일"


def load_all():
    """{날짜: 요약 마크다운} (날짜 오름차순). 빈 파일·읽기 실패는 건너뛴다."""
    out = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "20??-??-??.md"))):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read().strip()
        except Exception as e:
            print(f"[마감] 읽기 실패 — 건너뜀: {os.path.basename(f)} ({e})")
            continue
        if text:
            out[os.path.basename(f)[:10]] = text
    return out


def _inline(s):
    """한 줄 안의 서식: [글](url) 링크, **굵게**. 나머지는 이스케이프."""
    links = []

    def keep(m):
        links.append((m.group(1), m.group(2)))
        return f"\x00{len(links) - 1}\x00"

    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", keep, s)
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    for i, (t, u) in enumerate(links):
        s = s.replace(f"\x00{i}\x00",
                      f'<a href="{html.escape(u, quote=True)}" target="_blank" rel="noopener">{html.escape(t, quote=False)}</a>')
    return s


def body_html(md):
    """요약 md 의 좁은 문법만 다룬다: 첫 줄 제목(📝…)은 헤더가 대신하므로 뺀다,
    '**■ 소제목**' 은 h3, '· ' 불릿은 ul/li, '※'·'출처' 로 시작하는 줄은 회색 주석, 나머지는 문단."""
    lines = md.strip().splitlines()
    if lines and lines[0].lstrip().startswith("📝"):
        lines = lines[1:]
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items.clear()

    for raw in lines:
        s = raw.strip()
        if not s:
            flush()
            continue
        m = re.match(r"^\*\*\s*(?:■\s*)?(.+?)\s*\*\*$", s)
        if m:
            flush()
            out.append(f"<h3>{_inline(m.group(1))}</h3>")
            continue
        m = re.match(r"^[·\-\*]\s+(.*)", s)
        if m:
            items.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        flush()
        cls = ' class="muted"' if s.startswith(("※", "출처")) else ""
        out.append(f"<p{cls}>{_inline(s)}</p>")
    flush()
    return "\n".join(out)


def pane_html(date, md):
    y, m, d = (int(x) for x in date.split("-"))
    wd = WEEKDAYS[datetime.date(y, m, d).weekday()]
    return (f'<div class="pane pane-close">\n'
            f'  <header><div class="eyebrow">데일리 · 마감시황</div><h1>마감시황 요약</h1>'
            f'<div class="date">{date} ({wd})</div></header>\n'
            f'  <article class="prose">{body_html(md)}</article>\n'
            f'  <footer><p class="muted">증권사 리서치 텔레그램 글을 AI 가 요약한 참고 자료이며 투자 권유가 아닙니다.</p></footer>\n'
            f'</div>')


def empty_pane():
    return ('<div class="pane pane-close"><article class="prose">'
            '<p class="muted">이 날짜의 마감시황 요약은 없습니다.</p></article></div>')
