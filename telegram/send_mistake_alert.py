# -*- coding: utf-8 -*-
"""반복실수 한 줄을 '개인 DM 으로만' 보낸다 (공개 알파노트 요약과는 완전히 별개 메시지).

보낼 곳은 config.json 의 chat_id(개인 대화방) 하나로 고정이다. --to 옵션이 없고, 채널 주소를
받는 코드 경로가 없다. 데이터(매매일지/·엑셀)는 이 PC 에만 있어 읽지 못하면 아무것도 보내지 않는다.

사용:
  python send_mistake_alert.py            발송(하루 한 번)
  python send_mistake_alert.py --dry-run  발송 없이 메시지만 출력
  python send_mistake_alert.py --force    오늘 이미 보냈어도 재발송
"""
import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BASE))

from send_summary import CONFIG_PATH, api_call  # noqa: E402

SENT_LOG = HERE / "sent_mistake.json"


WEEKDAY = "월화수목금토일"
CIRCLED = "①②③④⑤"


def build_message():
    """반복실수 알림 본문(HTML). 코드(X1 등)보다 이름·뜻·다음 행동을 먼저 풀어쓴다(2026-10-19 요청)."""
    import pre_trade
    rep = pre_trade.repeat_report()
    if not rep:
        return None
    esc = html.escape
    d = rep["today"]
    out = [f"🔁 <b>반복실수 점검</b> · {d:%m/%d}({WEEKDAY[d.weekday()]})",
           f"최근 {rep['days']}일 동안 기록된 실수 {rep['n_recent']}건을 기준으로 정리했습니다.", ""]

    out.append(f"<b>■ 자주 반복한 실수</b> (최근 {rep['days']}일 동안 3번 이상)")
    if not rep["red"]:
        out.append("   3번 이상 반복한 실수는 없습니다.")
    for i, r in enumerate(rep["red"]):
        out.append("")
        out.append(f"<b>{CIRCLED[i]} {esc(r['name'])}</b> ({r['tag']})")
        out.append(f"   {r['count']}번 반복 · 마지막 {r['last']:%m/%d}")
        if r["desc"]:
            out.append(f"   • 어떤 실수: {esc(r['desc'])}")
        if r["rule"]:
            out.append(f"   • 다음엔: {esc(r['rule'])}")
        out.append("   • 막는 장치: " + (f"체크리스트 「{esc(r['guard'])}」" if r["guard"] else "아직 없음"))

    out.append("")
    out.append(f"<b>■ 최근 {rep['recur_days']}일 안에 다시 나온 실수</b>")
    if not rep["recur"]:
        out.append(f"   같은 실수가 {rep['recur_days']}일 안에 다시 나온 경우는 없습니다.")
    for r in rep["recur"][:5]:
        name = pre_trade.ml.TAG_NAME.get(r["tag"], r["tag"])
        out.append(f"   • {r['date']:%m/%d} {esc(name)}({r['tag']}) — "
                   f"직전 {r['prev']:%m/%d} 이후 {r['gap']}일 만에 다시 발생")
        if r["what"]:
            out.append(f"     “{esc(pre_trade.ml.clip(r['what'], 60))}”")

    out.append("")
    out.append("💡 진입 전에 <code>python pre_trade.py 종목명 --side long</code> 으로 "
               "그 종목의 과거 실수와 손절가·손실금액·청산조건을 확인하세요.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        msg = build_message()
    except Exception as e:                      # 데이터를 못 읽으면 조용히 건너뜀(요약 발송과 무관)
        print(f"건너뜀: 반복실수 데이터를 읽지 못했습니다 ({e})")
        return
    if not msg:
        print("건너뜀: 기록이 없습니다.")
        return
    if args.dry_run:
        print(msg)
        return

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    chat_id = str(cfg.get("chat_id") or "")
    if not chat_id.lstrip("-").isdigit():       # 숫자 chat_id(개인 대화방)가 아니면 보내지 않는다
        raise SystemExit("config.json 의 개인 chat_id 를 확인할 수 없어 보내지 않습니다.")

    today = f"{datetime.now():%Y-%m-%d}"
    log = json.loads(SENT_LOG.read_text(encoding="utf-8")) if SENT_LOG.exists() else {}
    if log.get(today) and not args.force:
        print(f"건너뜀: {today} 이미 발송됨")
        return
    res = api_call(cfg["bot_token"], "sendMessage", {
        "chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": "true"})
    if not res.get("ok"):
        raise SystemExit(f"발송 실패: {res}")
    log[today] = chat_id
    for old in sorted(log)[:-30]:
        del log[old]
    SENT_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"발송 완료 → 개인 DM ({datetime.now():%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    main()
