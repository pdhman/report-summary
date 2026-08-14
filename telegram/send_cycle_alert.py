# -*- coding: utf-8 -*-
"""한국 시장 사이클 모델 국면 알림 — 스코어 하락 시에만 DM 발송.

korea_cycle_monitor.py(평일 17:45)가 aicyclemonitor/kc_cache/score_log.csv 에
일별 점수를 기록한 뒤, run_korea_cycle.bat 이 마지막 단계로 이 스크립트를 호출한다.

발송 조건 (하나라도 충족, 직전 기록 대비):
  - 종합 스코어가 DROP_PT(2.0)점 이상 하락
  - 국면 밴드 하향 전환 (예: Bull → Neutral)
  - 국면 밴드 상향 전환 (예: Neutral → Bull)
채널(@daily_alphanote)에는 보내지 않고 기본 DM(알파노트 봇 개인 대화)으로만 발송.
같은 날짜로는 한 번만 보낸다 (cycle_alert_state.json).

사용:
  python send_cycle_alert.py            조건 충족 시 발송
  python send_cycle_alert.py --dry-run  발송 없이 판정·메시지만 출력
  python send_cycle_alert.py --force    조건·중복 방지 무시하고 발송 (테스트)
"""
import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from send_summary import CONFIG_PATH, add_target_arg, resolve_targets, send  # noqa: E402

SCORE_LOG = HERE.parent / "aicyclemonitor" / "kc_cache" / "score_log.csv"
STATE_PATH = HERE / "cycle_alert_state.json"
DASH_URL = "https://pdhman.github.io/report-summary/korea_cycle.html"

DROP_PT = 2.0                       # 전 기록 대비 이 폭 이상 하락하면 알림
FACTOR_KO = {"global": "Global", "semi": "반도체", "breadth": "Breadth",
             "liq": "유동성", "euphoria": "Euphoria"}
REGIMES = [  # (하한, 라벨, 권장 주식비중) — korea_cycle_monitor.py 와 동일
    (80, "Strong Bull", "90~100%"),
    (65, "Bull", "75~90%"),
    (50, "Neutral", "50~75%"),
    (35, "Risk-off", "30~50%"),
    (0, "Bear", "현금·헤지 확대"),
]


def regime(score):
    """(순위, 라벨, 권장비중) — 순위가 작을수록 강세."""
    for i, (lo, label, band) in enumerate(REGIMES):
        if score >= lo:
            return i, label, band
    return len(REGIMES) - 1, "Bear", "현금·헤지 확대"


def load_scores():
    if not SCORE_LOG.exists():
        raise SystemExit(f"score_log.csv 가 없습니다: {SCORE_LOG}")
    with open(SCORE_LOG, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("composite")]
    rows.sort(key=lambda r: r["date"])
    return rows


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_message(last, prev, forced):
    score = fnum(last["composite"])
    rank, label, band = regime(score)
    lines = [f"🇰🇷 <b>한국 사이클 국면 알림</b> · {last['date']}"
             + (" · 수동 테스트" if forced else "")]

    if prev:
        pscore = fnum(prev["composite"])
        diff = score - pscore
        lines.append(f"종합 <b>{score:.1f}</b> (직전 {pscore:.1f}, {diff:+.1f})")
        prank, plabel, _ = regime(pscore)
        if rank > prank:
            lines.append(f"⚠️ 국면 하향: <b>{plabel} → {label}</b> · 권장 주식비중 {band}")
        elif rank < prank:
            lines.append(f"🟢 국면 상향: <b>{plabel} → {label}</b> · 권장 주식비중 {band}")
        else:
            lines.append(f"국면: <b>{label}</b> · 권장 주식비중 {band}")
        parts = []
        for k, ko in FACTOR_KO.items():
            v, p = fnum(last.get(k)), fnum(prev.get(k))
            if v is None:
                continue
            parts.append(f"{ko} {v:.0f}({v - p:+.0f})" if p is not None else f"{ko} {v:.0f}")
        if parts:
            lines.append("팩터(직전比): " + " · ".join(parts))
    else:
        lines.append(f"종합 <b>{score:.1f}</b> (직전 기록 없음)")
        lines.append(f"국면: <b>{label}</b> · 권장 주식비중 {band}")

    lines.append(f'📊 <a href="{DASH_URL}">사이클 대시보드</a>')
    return "\n".join(lines)


def should_alert(last, prev):
    """(발송 여부, 사유)"""
    if not prev:
        return False, "직전 기록 없음 — 비교 불가"
    score, pscore = fnum(last["composite"]), fnum(prev["composite"])
    if score is None or pscore is None:
        return False, "점수 파싱 실패"
    drop = pscore - score
    rank, label, _ = regime(score)
    prank, plabel, _ = regime(pscore)
    if rank > prank:
        return True, f"국면 하향 {plabel}→{label}"
    if rank < prank:
        return True, f"국면 상향 {plabel}→{label}"
    if drop >= DROP_PT:
        return True, f"스코어 {drop:.1f}점 하락"
    return False, f"알림 조건 미충족 (변화 {score - pscore:+.1f})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 판정·메시지 출력")
    ap.add_argument("--force", action="store_true", help="조건·중복 방지 무시하고 발송")
    add_target_arg(ap)
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    rows = load_scores()
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None

    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    if (state.get("last_sent_date") == last["date"]
            and not args.force and not args.dry_run):
        print(f"오늘({last['date']})은 이미 발송함 — 스킵")
        return

    fire, reason = should_alert(last, prev)
    print(f"판정: {reason}")
    if not fire and not args.force:
        return

    msg = build_message(last, prev, forced=args.force and not fire)
    if args.dry_run:
        print("--- 메시지 (dry-run) ---")
        print(msg)
        return

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    send(cfg, resolve_targets(cfg, args.to), msg)
    state["last_sent_date"] = last["date"]
    state["last_sent_score"] = fnum(last["composite"])
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
