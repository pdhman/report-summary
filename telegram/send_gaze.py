"""'시장의 시선' 일일 텔레그램 발송.

docs/data/gaze/YYYY-MM-DD.json (매일 아침 Claude 예약 작업이 작성)을 읽어
순위·웨이트·전일 대비 변화를 메시지로 만들어 보낸다. 봇·설정은 send_summary.py 와 공유.

기본 발송처는 개인 대화방(dm)뿐이다 — 공개 채널로 보내려면 --to 를 명시한다.

사용:  python telegram/send_gaze.py --dry-run
       python telegram/send_gaze.py [--date 2026-09-21] [--to dm] [--force]
"""
import argparse
import html
import json
import sys
from pathlib import Path

from send_summary import CONFIG_PATH, add_target_arg, resolve_targets, send

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "docs" / "data" / "gaze"
SENT_LOG = Path(__file__).resolve().parent / "sent_gaze.json"
DIR_ICON = {"우호": "🟢", "중립": "⚪", "비우호": "🔴"}
WEEKDAYS = "월화수목금토일"
PAGE_URL = "https://pdhman.github.io/report-summary/briefs.html"


def load(date=None):
    files = sorted(DATA_DIR.glob("20??-??-??.json"))
    if date:
        files = [f for f in files if f.stem <= date]
    if not files or (date and files[-1].stem != date):
        raise SystemExit(f"시장의 시선 데이터가 없습니다: {date or DATA_DIR}")
    cur = json.loads(files[-1].read_text(encoding="utf-8"))
    prev = json.loads(files[-2].read_text(encoding="utf-8")) if len(files) > 1 else None
    return cur, prev


def delta(item, prev):
    """전일 대비 웨이트·순위 변화 표시."""
    if not prev:
        return ""
    old = {i["key"]: i for i in prev.get("items", [])}.get(item["key"])
    if not old:
        return " 🆕"
    dw = item["weight"] - old["weight"]
    dr = old["rank"] - item["rank"]
    parts = []
    if dw:
        parts.append(f"{dw:+d}")
    if dr:
        parts.append(f"{'▲' if dr > 0 else '▼'}{abs(dr)}계단")
    return f" ({', '.join(parts)})" if parts else " (=)"


def build_message(cur, prev):
    e = lambda s: html.escape(s, quote=False)   # 텔레그램 HTML 은 < > & 만 이스케이프하면 된다
    y, m, d = (int(x) for x in cur["date"].split("-"))
    import datetime
    wd = WEEKDAYS[datetime.date(y, m, d).weekday()]
    lines = [f"👁 <b>시장의 시선</b> — {cur['date']} ({wd})", ""]
    if cur.get("headline"):
        lines += [e(cur["headline"]), ""]
    for it in sorted(cur["items"], key=lambda i: i["rank"]):
        icon = DIR_ICON.get(it.get("direction", ""), "▫️")
        lines.append(f"<b>{it['rank']}. {e(it['name'])} — {it['weight']}</b>{delta(it, prev)} {icon}{e(it.get('direction', ''))}")
        if it.get("why"):
            lines.append(f"· {e(it['why'])}")
        if it.get("trigger"):
            lines.append(f"· 다음 트리거: {e(it['trigger'])}")
        lines.append("")
    if prev:
        gone = {i["key"]: i["name"] for i in prev.get("items", [])}.keys() - {i["key"] for i in cur["items"]}
        if gone:
            names = [i["name"] for i in prev["items"] if i["key"] in gone]
            lines += [f"➖ 목록에서 빠짐: {e(', '.join(names))}", ""]
    if cur.get("indicators"):
        lines.append("📍 <b>체크 지표</b>")
        lines += [f"· {e(i['name'])}: {e(str(i['value']))}" + (f" (기준 {e(str(i['line']))})" if i.get("line") else "")
                  for i in cur["indicators"]]
        lines.append("")
    if cur.get("watch"):
        lines.append("👀 <b>관찰 목록</b>")
        lines += [f"· {e(w)}" for w in cur["watch"]]
        lines.append("")
    mix = cur.get("source_mix") or {"base": 80, "x": 20}
    lines += [f'<a href="{PAGE_URL}">뉴스 브리핑과 함께 보기</a>', ""]
    lines.append(f"<i>웨이트 합 100 · 뉴스·가격반응·폴리마켓 {mix['base']}% + X모니터링 {mix['x']}% · 판단 추정치</i>")
    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (생략 시 가장 최근 파일)")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    ap.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 보낸다")
    add_target_arg(ap)
    args = ap.parse_args()

    cur, prev = load(args.date)
    total = sum(i["weight"] for i in cur["items"])
    if total != 100:
        raise SystemExit(f"웨이트 합이 100이 아닙니다: {total}")
    msg = build_message(cur, prev)
    if len(msg) > 4000:
        raise SystemExit(f"메시지가 너무 깁니다({len(msg)}자). why/trigger 를 줄이세요.")
    if args.dry_run:
        print(msg)
        return

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = resolve_targets(cfg, args.to)
    sent = json.loads(SENT_LOG.read_text(encoding="utf-8")) if SENT_LOG.exists() else {}
    done = sent.get(cur["date"], [])
    todo = targets if args.force else [t for t in targets if t not in done]
    if not todo:
        print(f"{cur['date']} 시장의 시선은 이미 발송됨 — 건너뜀")
        return
    send(cfg, todo, msg)
    sent[cur["date"]] = sorted(set(done) | set(todo))
    SENT_LOG.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
