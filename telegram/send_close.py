# -*- coding: utf-8 -*-
"""증권사 텔레그램 마감시황 요약 발송.

docs/data/close/YYYY-MM-DD.md (평일 16:45 Claude 예약 작업 market-close-brief 가 작성, 알파노트
뉴스 탭 '📝 마감시황'과 같은 파일)를 읽어 텔레그램 HTML 로 바꿔 보낸다. 봇·설정은 send_summary.py 와
공유(@alphanote_daily_bot). 맨 아래에 그 날짜의 알파노트 페이지 링크를 단다.

기본 발송처는 개인 대화방(dm)뿐이다. 요약 재료가 증권사 채널 글이라 공개 채널로는 보내지 않는다.

사용:  python telegram/send_close.py --dry-run
       python telegram/send_close.py [--date 2026-10-21] [--force]

마크다운 → 텔레그램 HTML 규칙(요약 파일을 쓸 때 이 범위만 쓴다):
  **굵게**, 줄 맨 앞 '# '/'## ' 는 굵은 제목, '- '/'* '/'· ' 는 불릿 '· ', [글](url) 링크.
  4,000자를 넘으면 빈 줄 경계에서 나눠 여러 통으로 보낸다(기본 분량은 한 통).
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

from send_summary import CONFIG_PATH, add_target_arg, resolve_targets, send

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "docs" / "data" / "close"
PAGE_URL = "https://pdhman.github.io/report-summary/briefs.html"
SENT_LOG = Path(__file__).resolve().parent / "sent_close.json"
LIMIT = 4000


def load(date=None):
    files = sorted(OUT_DIR.glob("20??-??-??.md"))
    if date:
        files = [f for f in files if f.stem == date]
    if not files:
        raise SystemExit(f"마감시황 요약 파일이 없습니다: {date or OUT_DIR}")
    f = files[-1]
    return f.stem, f.read_text(encoding="utf-8").strip()


def md_to_html(md):
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            s = f"**{m.group(2)}**"
        s = re.sub(r"^\s*[-*·]\s+", "· ", s)
        # 링크는 먼저 자리표시자로 빼고, 본문을 이스케이프한 뒤 되돌린다
        links = []
        def _link(mm):
            links.append((mm.group(1), mm.group(2)))
            return f"\x00{len(links) - 1}\x00"
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _link, s)
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        for i, (t, u) in enumerate(links):
            s = s.replace(f"\x00{i}\x00", f'<a href="{html.escape(u, quote=True)}">{html.escape(t, quote=False)}</a>')
        out.append(s)
    return "\n".join(out).strip()


def split_chunks(text, limit=LIMIT):
    """빈 줄 경계로 나눈다. 한 문단이 limit 를 넘으면 줄 단위로 쪼갠다."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        cand = (cur + "\n\n" + para) if cur else para
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            chunks.append(cur)
        cur = ""
        if len(para) <= limit:
            cur = para
            continue
        for ln in para.split("\n"):
            cand = (cur + "\n" + ln) if cur else ln
            if len(cand) > limit:
                chunks.append(cur)
                cur = ln
            else:
                cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (생략 시 가장 최근 파일)")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    ap.add_argument("--force", action="store_true", help="같은 날짜를 이미 보냈어도 다시 보낸다")
    add_target_arg(ap)
    args = ap.parse_args()

    date, md = load(args.date)
    if "**" in md and md.count("**") % 2:
        raise SystemExit("요약 파일의 ** 가 짝이 맞지 않습니다 — 굵게 표시를 확인하세요.")
    link = f'<a href="{PAGE_URL}?tab=close#{date.replace("-", "")}">알파노트에서 지난 날짜와 함께 보기</a>'
    msg = md_to_html(md) + "\n\n" + link
    chunks = split_chunks(msg)
    if args.dry_run:
        for i, c in enumerate(chunks, 1):
            if len(chunks) > 1:
                print(f"----- {i}/{len(chunks)} ({len(c)}자) -----")
            print(c)
        print(f"\n[{date}] {len(msg)}자 · {len(chunks)}통" + (" ⚠️ 한 통을 넘습니다 — 줄이는 게 좋습니다" if len(chunks) > 1 else ""))
        return

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = resolve_targets(cfg, args.to)
    sent = json.loads(SENT_LOG.read_text(encoding="utf-8")) if SENT_LOG.exists() else {}
    done = sent.get(date, [])
    todo = targets if args.force else [t for t in targets if t not in done]
    if not todo:
        print(f"{date} 마감시황 요약은 이미 발송됨 — 건너뜀")
        return
    for c in chunks:
        send(cfg, todo, c)
    sent[date] = sorted(set(done) | set(todo))
    for old in sorted(sent)[:-90]:
        del sent[old]
    SENT_LOG.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
