# -*- coding: utf-8 -*-
"""X 모니터링 일일 리포트를 텔레그램으로 요약 발송한다.

/x-monitor 스킬이 리포트를 만들고 사이트에 게시한 뒤 마지막 단계로 호출한다.
읽는 파일: x-monitor/reports/YYYY-MM-DD.md (스킬이 이미 만들어 둔 것).
보내는 내용은 '주제별 정리'의 소제목과 '코멘트' 전문이다.

사용:
  python send_x_summary.py                     가장 최근 리포트 발송
  python send_x_summary.py --date 2026-08-07   특정 날짜
  python send_x_summary.py --dry-run           발송 없이 출력
  python send_x_summary.py --if-missing --to channel
                                               오늘자가 아직 안 나갔으면 발송 (누락 감지용)
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from send_summary import BASE, CONFIG_PATH, add_target_arg, resolve_targets, send

X_DIR = BASE / "x-monitor"
SITE = "https://pdhman.github.io/report-summary"

# 발송 이력. 스킬이 보냈든 감지기가 보냈든 여기 남으므로 중복 발송이 막힌다.
# *.json 이라 .gitignore 에 걸려 저장소에는 올라가지 않는다.
SENT_LOG = BASE / "telegram" / "sent.json"

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


# ---------------------------------------------------------------- 발송 이력·누락 감지

def load_sent():
    if SENT_LOG.exists():
        try:
            return json.loads(SENT_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass          # 손상됐으면 빈 이력으로 시작한다 (최악이라도 재발송일 뿐)
    return {}


def mark_sent(date, targets):
    log = load_sent()
    log[date] = sorted(set(log.get(date, [])) | set(targets))
    for old in sorted(log)[:-90]:      # 90일치만 남긴다
        del log[old]
    SENT_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def page_is_live(date):
    """게시 페이지가 배포됐는지. 아직이면 메시지 링크가 404 가 되므로 발송을 미룬다."""
    url = f"{SITE}/x_{date.replace('-', '')}.html"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def skip_reason(date, targets, min_age):
    """--if-missing 에서 발송을 건너뛸 이유. 보내도 되면 None."""
    path = X_DIR / "reports" / f"{date}.md"
    if not path.exists():
        return f"{date} 리포트가 아직 없음"

    done = set(load_sent().get(date, []))
    if all(t in done for t in targets):
        return f"{date} 이미 발송됨 → {', '.join(sorted(done))}"

    age = (time.time() - path.stat().st_mtime) / 60
    if age < min_age:
        # 스킬이 리포트를 쓰는 중이거나 곧 스스로 보낼 수 있다. 다음 회차에 다시 본다.
        return f"{date} 리포트 수정 {age:.0f}분 전 — {min_age}분은 지나야 발송"

    if not page_is_live(date):
        return f"{date} 게시 페이지가 아직 배포 전 (링크가 404 가 됨)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 가장 최근 리포트)")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    ap.add_argument("--if-missing", action="store_true",
                    help="오늘자 리포트가 아직 발송되지 않았을 때만 보낸다 (스케줄러용)")
    ap.add_argument("--min-age", type=int, default=30,
                    help="--if-missing 에서 리포트 수정 후 대기할 분 (기본 30)")
    add_target_arg(ap)
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    # --if-missing 은 '오늘' 이 기준이다. latest_date() 를 쓰면 며칠 전 리포트를
    # 뒤늦게 채널에 올리는 사고가 난다.
    date = args.date or (datetime.now().strftime("%Y-%m-%d") if args.if_missing else latest_date())

    if not CONFIG_PATH.exists():
        raise SystemExit(f"설정 파일이 없습니다: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = resolve_targets(cfg, args.to)

    if args.if_missing:
        reason = skip_reason(date, targets, args.min_age)
        if reason:
            print(f"건너뜀: {reason}")
            return
        print(f"미발송 감지 → {date} 발송 시작")

    msg = build_message(date)

    if args.dry_run:
        print(msg)
        print(f"\n--- {len(msg)}자 (텔레그램 상한 4096) ---")
        return

    send(cfg, targets, msg)
    mark_sent(date, targets)
    print(f"X 모니터링 {date} ({len(msg)}자)")


if __name__ == "__main__":
    main()
