# -*- coding: utf-8 -*-
"""자가 점검 결과를 텔레그램 개인 대화방(dm)으로 보낸다 — 같은 내용은 하루 한 번만.

  python -X utf8 site-health/notify.py --file 메시지.txt --key "am:홈 카드 날짜 오래됨: 수급 동향"
  python -X utf8 site-health/notify.py --file 메시지.txt --key ... --dry-run

--key: 중복 판정용 문자열(보통 '슬롯:문제 제목들'). 오늘 같은 key 로 이미 보냈으면 보내지 않고 종료 코드 4.
기록은 site-health/state.json(git 제외).
"""
import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = Path(__file__).resolve().parent / "state.json"
KST = dt.timezone(dt.timedelta(hours=9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    msg = Path(a.file).read_text(encoding="utf-8").strip()
    if not msg:
        print("빈 메시지 — 보내지 않음")
        return 1
    today = dt.datetime.now(KST).date().isoformat()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    sent = state.get(today, [])
    if a.key in sent:
        print("오늘 같은 내용으로 이미 발송됨 — 건너뜀")
        return 4
    if a.dry_run:
        print(msg)
        print(f"--- {len(msg)}자 (dry-run, 발송 안 함)")
        return 0

    sys.path.insert(0, str(BASE / "telegram"))
    from send_summary import resolve_targets, send
    cfg = json.loads((BASE / "telegram" / "config.json").read_text(encoding="utf-8"))
    send(cfg, resolve_targets(cfg, "dm"), html.escape(msg, quote=False))   # 개인 대화방만 · HTML 모드라 <,>,& 이스케이프

    state = {k: v for k, v in state.items() if k >= (dt.date.fromisoformat(today) - dt.timedelta(days=14)).isoformat()}
    state.setdefault(today, []).append(a.key)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.key.startswith("weekly:"):        # 주간 보고 재료: 에이전트가 보낸 '발견→조치→결과' 원문
        with (Path(__file__).resolve().parent / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "phase": "sent",
                                "key": a.key, "msg": msg}, ensure_ascii=False) + "\n")
    print("발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
