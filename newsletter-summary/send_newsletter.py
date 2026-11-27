# -*- coding: utf-8 -*-
"""뉴스레터 요약(out/<id>.md)을 텔레그램 개인 대화방으로 보내고, 보낸 메일을 sent.json 에 기록한다.

봇·설정은 telegram/send_summary.py 와 공유(@alphanote_daily_bot, telegram/config.json).
마크다운 → 텔레그램 변환도 telegram/send_close.py 것을 그대로 쓴다
(**굵게**, 줄 맨 앞 '· ' 불릿, [글](url) 링크만 지원).

발송처는 개인 대화방(dm) 고정이다. 재료가 남의 뉴스레터라 공개 채널로는 보내지 않는다.

사용:  python -X utf8 newsletter-summary/send_newsletter.py <id> --dry-run   # 보내지 않고 확인
       python -X utf8 newsletter-summary/send_newsletter.py <id>             # 발송 + 기록
       python -X utf8 newsletter-summary/send_newsletter.py <id> --force     # 이미 보낸 것도 다시
       python -X utf8 newsletter-summary/send_newsletter.py <id> --mark-only # 보내지 않고 '처리됨'으로만 기록
       python -X utf8 newsletter-summary/send_newsletter.py --alert "로그인 실패 …"  # 문제 알림 한 줄
"""
import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "telegram"))

from send_close import md_to_html, split_chunks          # noqa: E402
from send_summary import CONFIG_PATH, resolve_targets, send  # noqa: E402

SENT_FILE = HERE / "sent.json"
BOT_FILE = HERE / "telegram_bot.json"   # 뉴스레터 전용 봇 토큰(git 제외). 비어 있으면 기존 봇 사용
RAW_DIR = HERE / "raw"
OUT_DIR = HERE / "out"
KEEP = 500


def load_cfg():
    """발송 설정. telegram_bot.json 에 bot_token 이 있으면 그 봇으로, 없으면 기존 봇(telegram/config.json)으로.

    개인 대화방 chat_id 는 봇이 달라도 같은 값(사용자 고유 번호)이라 기존 설정 것을 그대로 쓴다.
    단, 새 봇은 사용자가 그 봇 대화방에서 '시작'을 한 번 눌러야 메시지를 보낼 수 있다.
    """
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if BOT_FILE.exists():
        own = json.loads(BOT_FILE.read_text(encoding="utf-8-sig"))
        token = (own.get("bot_token") or "").strip()
        if token:
            return {"bot_token": token, "chat_id": str(own.get("chat_id") or cfg["chat_id"])}
    return cfg


def load_sent():
    if SENT_FILE.exists():
        try:
            return json.loads(SENT_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def subject_of(mid):
    raw = RAW_DIR / f"{mid}.txt"
    if raw.exists():
        for line in raw.read_text(encoding="utf-8").splitlines()[:6]:
            if line.startswith("제목: "):
                return line[4:].strip()
    return ""


def mark(mid, how):
    sent = load_sent()
    sent[mid] = {"subject": subject_of(mid), "at": f"{datetime.now():%Y-%m-%d %H:%M}", "how": how}
    for old in sorted(sent)[:-KEEP]:
        del sent[old]
    SENT_FILE.write_text(json.dumps(sent, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?", help="fetch_newsletter.py 가 알려준 id (예: 2026-11-27_3fa9c1d2)")
    ap.add_argument("--alert", metavar="글", help="요약 대신 짧은 알림 한 줄을 dm 으로 보낸다(기록 안 함)")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    ap.add_argument("--force", action="store_true", help="이미 보낸 메일이어도 다시 보낸다")
    ap.add_argument("--mark-only", action="store_true", help="보내지 않고 처리됨으로만 기록")
    args = ap.parse_args()
    mid = args.id

    if args.alert:
        msg = "⚠️ <b>뉴스레터 요약</b>\n" + html.escape(args.alert, quote=False)
        if args.dry_run:
            print(msg)
            return
        cfg = load_cfg()
        send(cfg, resolve_targets(cfg, "dm"), msg)
        return
    if not mid:
        ap.error("id 가 필요합니다(또는 --alert).")

    if args.mark_only:
        mark(mid, "skipped")
        print(f"{mid} 를 보내지 않고 처리됨으로 기록했습니다.")
        return

    path = OUT_DIR / f"{mid}.md"
    if not path.exists():
        raise SystemExit(f"요약 파일이 없습니다: {path}")
    md = path.read_text(encoding="utf-8").strip()
    if not md:
        raise SystemExit(f"요약 파일이 비어 있습니다: {path}")
    if md.count("**") % 2:
        raise SystemExit("요약 파일의 ** 가 짝이 맞지 않습니다 — 굵게 표시를 확인하세요.")

    chunks = split_chunks(md_to_html(md))
    if args.dry_run:
        for i, c in enumerate(chunks, 1):
            if len(chunks) > 1:
                print(f"----- {i}/{len(chunks)} ({len(c)}자) -----")
            print(c)
        total = sum(len(c) for c in chunks)
        print(f"\n[{mid}] {total}자 · {len(chunks)}통"
              + (" ⚠️ 한 통을 넘습니다 — 줄이는 게 좋습니다" if len(chunks) > 1 else ""))
        return

    if mid in load_sent() and not args.force:
        print(f"{mid} 요약은 이미 발송됨 — 건너뜀")
        return
    cfg = load_cfg()
    targets = resolve_targets(cfg, "dm")
    for c in chunks:
        send(cfg, targets, c)
    mark(mid, "sent")


if __name__ == "__main__":
    main()
