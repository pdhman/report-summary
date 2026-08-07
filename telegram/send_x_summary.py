# -*- coding: utf-8 -*-
"""X 모니터링 일일 리포트를 텔레그램으로 요약 발송한다.

/x-monitor 스킬이 리포트를 만들고 사이트에 게시한 뒤 마지막 단계로 호출한다.
읽는 파일: x-monitor/reports/YYYY-MM-DD.md (스킬이 이미 만들어 둔 것).
보내는 내용은 '주제별 정리'의 소제목과 '코멘트' 전문이다.

사용:
  python send_x_summary.py                     가장 최근 리포트 발송
  python send_x_summary.py --date 2026-08-07   특정 날짜
  python send_x_summary.py --dry-run           발송 없이 출력
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from send_summary import BASE, CONFIG_PATH, add_target_arg, resolve_targets, send

X_DIR = BASE / "x-monitor"
SITE = "https://pdhman.github.io/report-summary"

# 텔레그램 메시지 상한은 4096자. 총평 외 머리말·계정 목록·링크 몫을 빼고 잡는다.
SUMMARY_MAX = 2600


def latest_date():
    files = sorted(X_DIR.glob("reports/????-??-??.md"))
    if not files:
        raise SystemExit(f"리포트가 없습니다: {X_DIR / 'reports'}")
    return files[-1].stem


def md_to_html(text):
    """리포트 마크다운을 텔레그램 HTML 로 바꾼다."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [본문](링크) → 본문
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", text, flags=re.S)
    return text


def shorten(md, limit=SUMMARY_MAX):
    """마크다운 상태에서 문장 경계로 자른다. HTML 변환 전에 잘라야 태그가 깨지지 않는다."""
    if len(md) <= limit:
        return md
    cut = md[:limit]
    end = max(cut.rfind("다. "), cut.rfind("다.\n"), cut.rfind(". "))
    if end > limit * 0.5:
        cut = cut[:end + 2]
    cut = cut.rstrip() + " …"
    if cut.count("**") % 2:      # 굵게 표시가 열린 채 잘리면 닫아준다
        cut += "**"
    return cut


def section(text, title):
    """'## <title>' 부터 다음 '## ' 직전까지의 본문."""
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def parse_report(date):
    path = X_DIR / "reports" / f"{date}.md"
    if not path.exists():
        raise SystemExit(f"리포트가 없습니다: {path}")
    text = path.read_text(encoding="utf-8")

    count_line = next(
        (m.group(1).strip() for m in re.finditer(r"^-\s+\*\*수집 건수\*\*:\s*(.+)$", text, re.M)), None)

    body = section(text, "주제별 정리") or ""
    topics = re.findall(r"^###\s+(.+?)\s*$", body, re.M)

    return count_line, topics, section(text, "코멘트")


def paragraphs(md):
    """코멘트를 문단 단위로 쪼갠다. '1.' '2.' 로 시작하는 항목은 각각 별도 문단으로 뗀다
    (원문은 빈 줄 없이 이어져 있어 그대로 보내면 한 덩어리 줄글로 읽힌다)."""
    out = []
    for block in re.split(r"\n\s*\n", md.strip()):
        cur = []
        for line in block.split("\n"):
            if re.match(r"^\s*\d+\.\s", line) and cur:
                out.append(" ".join(cur))
                cur = []
            cur.append(line.strip())
        if cur:
            out.append(" ".join(cur))
    return [p for p in out if p]


def build_message(date):
    count_line, topics, comment = parse_report(date)
    try:
        title = datetime.strptime(date, "%Y-%m-%d").strftime("%m/%d")
    except ValueError:
        title = date

    lines = [f"📡 <b>X 모니터링</b> · {title}"]
    if count_line:
        # "147건 (42개 모니터링 계정 리스트 기준)" 의 괄호 설명은 매일 같으므로 뺀다
        brief = re.sub(r"\s*\([^)]*\)", "", count_line).strip()
        lines.append(f"   {html.escape(brief)}")

    if topics:
        lines.append("")
        lines.append("📑 <b>주제별 정리</b>")
        for t in topics:
            lines.append(f"   {md_to_html(t)}")

    if comment:
        lines.append("")
        lines.append("💬 <b>코멘트</b>")
        for p in paragraphs(shorten(comment)):
            lines.append("")
            lines.append(md_to_html(p))

    lines.append("")
    lines.append(f'🔗 <a href="{SITE}/x_{date.replace("-", "")}.html">전체 리포트</a>')
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 가장 최근 리포트)")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    add_target_arg(ap)
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    date = args.date or latest_date()
    msg = build_message(date)

    if args.dry_run:
        print(msg)
        print(f"\n--- {len(msg)}자 (텔레그램 상한 4096) ---")
        return

    if not CONFIG_PATH.exists():
        raise SystemExit(f"설정 파일이 없습니다: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    send(cfg, resolve_targets(cfg, args.to), msg)
    print(f"X 모니터링 {date} ({len(msg)}자)")


if __name__ == "__main__":
    main()
