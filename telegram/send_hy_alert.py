# -*- coding: utf-8 -*-
"""하이일드(HY) 스프레드 급변 알림 — 급격히 넓어지거나 좁혀질 때만 DM 발송.

AI 사이클 대시보드 '③ 레버리지 — 하이일드(HY) OAS' 카드와 같은 데이터
(FRED BAMLH0A0HYM2, ICE BofA 미국 하이일드 OAS)를 매일 받아 직전 거래일과 비교한다.
대시보드는 주 1회만 다시 그려지므로, 급변은 이 스크립트가 따로 잡는다.

발송 조건 (넓어짐·좁혀짐 양쪽, 하나라도 충족):
  - 하루 변화 ±20bp 이상       (2024-11~2026-11 상위 약 1%)
  - 5거래일 변화 ±40bp 이상    (2025-03·04 관세 충격, 2025-10, 2026-10 초 수준)
  - 400bp 선을 위·아래로 통과
같은 데이터 날짜로는 한 번만 보낸다. 5거래일 조건만 걸린 날은 같은 방향 알림을
직전 4거래일 안에 이미 보냈으면 건너뛴다(급변이 며칠 이어질 때 매일 같은 알림 방지).
채널에는 보내지 않고 기본 DM(알파노트 봇 개인 대화)으로만 발송.
메시지에는 BBB·CCC 스프레드도 같은 날짜 기준으로 함께 싣는다(판정에는 안 씀).
'평소의 n배' = 5거래일 변화 ÷ 최근 1년 5거래일 변화의 표준편차.

FRED 는 이 PC 에서 직접 접속이 막혀 ai_cycle_monitor 의 프록시 폴백을 그대로 쓴다.
대시보드용 히스토리 CSV(추적 파일)는 건드리지 않는다.

사용:
  python send_hy_alert.py            조건 충족 시 발송
  python send_hy_alert.py --dry-run  발송 없이 판정·메시지만 출력
  python send_hy_alert.py --force    조건·중복 방지 무시하고 발송 (테스트)
  python send_hy_alert.py --backtest 저장된 히스토리로 과거에 몇 번 울렸을지 출력
"""
import argparse
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
AICYCLE = HERE.parent / "aicyclemonitor"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(AICYCLE))
from send_summary import CONFIG_PATH, add_target_arg, resolve_targets, send  # noqa: E402

SERIES = "BAMLH0A0HYM2"
HIST_CSV = AICYCLE / "fred_hy_oas_history.csv"   # 읽기만 (주간 대시보드가 갱신)
# 메시지에 함께 보여주는 등급(판정에는 안 씀) — (라벨, FRED id, 히스토리 CSV)
COMPANIONS = [
    ("BBB(투자등급 맨 아래)", "BAMLC0A4CBBB", AICYCLE / "fred_bbb_oas_history.csv"),
    ("CCC(가장 위험한 등급)", "BAMLH0A3HYC", AICYCLE / "fred_ccc_oas_history.csv"),
]
STATE_PATH = HERE / "hy_alert_state.json"
DASH_URL = "https://pdhman.github.io/report-summary/aicycle.html"

DAY_BP = 20.0      # 하루 변화
WEEK_BP = 40.0     # 5거래일 변화
LEVEL_BP = 400.0   # 통과 여부를 보는 선
REPEAT_DAYS = 4    # 5거래일 조건만 걸린 같은 방향 알림은 이 거래일 수 안에서 한 번


_FRED_DOWN = False


def load_cached(csv_path=HIST_CSV):
    """대시보드 히스토리 CSV(bp) → pandas Series. 없으면 None."""
    import pandas as pd
    if not csv_path.exists():
        return None
    s = pd.read_csv(csv_path, header=None, names=["date", "bp"], parse_dates=["date"])
    return s.set_index("date")["bp"].dropna().sort_index()


def _fetch_fred(series_id):
    """ai_cycle_monitor 와 같은 수집 경로(직접 → r.jina.ai → allorigins), 최근 60일, % 단위."""
    import pandas as pd
    from ai_cycle_monitor import _fred_csv_text
    cosd = (date.today() - timedelta(days=60)).isoformat()
    df = pd.read_csv(io.StringIO(_fred_csv_text(series_id, cosd)))
    df.columns = ["date", "value"]
    return pd.Series(pd.to_numeric(df["value"], errors="coerce").values, index=pd.to_datetime(df["date"]))


def load_series(series_id=SERIES, csv_path=HIST_CSV):
    """최근 구간을 FRED(직접 → jina → allorigins 프록시)에서 받아 캐시 뒤에 붙인다. 실패하면 캐시만."""
    import pandas as pd
    global _FRED_DOWN
    cached = load_cached(csv_path)
    try:
        if _FRED_DOWN:   # 앞 시리즈가 실패했으면 프록시를 또 붙들지 않는다(시리즈당 최대 2.5분)
            raise RuntimeError("앞 시리즈 수집 실패 — 재시도 생략")
        fresh = (_fetch_fred(series_id).dropna() * 100).round(0)
        if cached is None:
            return fresh, "FRED"
        return pd.concat([cached[cached.index < fresh.index[0]], fresh]).sort_index(), "FRED"
    except Exception as e:
        _FRED_DOWN = True
        print(f"FRED {series_id} 수집 실패({type(e).__name__}: {e}) → 저장된 히스토리만 사용")
        if cached is None:
            raise SystemExit(f"{series_id} 히스토리도 없습니다.")
        return cached, "캐시"


def evaluate(s, i):
    """s 의 i 번째 관측(정수 위치)에 대한 판정 → dict."""
    now, prev = float(s.iloc[i]), float(s.iloc[i - 1])
    d1 = now - prev
    d5 = now - float(s.iloc[i - 5]) if i >= 5 else None
    reasons = []
    if abs(d1) >= DAY_BP:
        reasons.append(("day", f"하루 {d1:+.0f}bp (기준 ±{DAY_BP:.0f})"))
    if d5 is not None and abs(d5) >= WEEK_BP:
        reasons.append(("week", f"5거래일 {d5:+.0f}bp (기준 ±{WEEK_BP:.0f})"))
    if prev < LEVEL_BP <= now:
        reasons.append(("level", f"{LEVEL_BP:.0f}bp 위로 올라섬"))
    elif now < LEVEL_BP <= prev:
        reasons.append(("level", f"{LEVEL_BP:.0f}bp 아래로 내려옴"))
    # 방향: 하루 조건이 걸렸으면 하루 변화, 아니면 5거래일 변화, 아니면 선 통과 방향
    ref = d1 if any(k == "day" for k, _ in reasons) else (d5 if d5 is not None and abs(d5) >= WEEK_BP else d1)
    return {"date": s.index[i].date().isoformat(), "bp": now, "d1": d1, "d5": d5,
            "reasons": reasons, "dir": "up" if ref > 0 else "down"}


def suppressed(ev, recent):
    """5거래일 조건만 걸렸고 같은 방향 알림을 최근에 보냈으면 True."""
    kinds = {k for k, _ in ev["reasons"]}
    return kinds == {"week"} and any(r == ev["dir"] for r in recent)


def typical_5d(s, i):
    """최근 1년 5거래일 변화의 표준편차(bp) — '평소 움직임' 크기."""
    w = s.iloc[max(0, i - 256):i + 1].diff(5).dropna()
    return float(w.std()) if len(w) > 20 else None


def times(d5, sd):
    return f" (평소의 {abs(d5) / sd:.1f}배)" if sd and d5 is not None else ""


def companion_lines(ev):
    """BBB·CCC 를 HY 와 같은 날짜 기준으로 한 줄씩 + 셋을 비교한 한 줄 해석."""
    import pandas as pd
    out, z = [], {}
    for label, sid, csv in COMPANIONS:
        s, src = load_series(sid, csv)
        s = s[s.index <= pd.Timestamp(ev["date"])]
        if len(s) < 6:
            continue
        i = len(s) - 1
        now, d1, d5 = float(s.iloc[i]), float(s.iloc[i] - s.iloc[i - 1]), float(s.iloc[i] - s.iloc[i - 5])
        sd = typical_5d(s, i)
        z[sid] = d5 / sd if sd else 0.0
        when = "" if s.index[i].date().isoformat() == ev["date"] else f" ({s.index[i].date()} 값)"
        out.append(f"· {label} <b>{now:,.0f}bp</b> · 하루 {d1:+.0f} · 5거래일 {d5:+.0f}{times(d5, sd)}{when}"
                   + ("" if src == "FRED" else " · 수집 실패, 저장된 값"))
    if not out:
        return []
    hy_z = ev.get("z") or 0.0
    bbb_z, ccc_z = z.get("BAMLC0A4CBBB", 0.0), z.get("BAMLH0A3HYC", 0.0)
    if abs(bbb_z) >= 2 and bbb_z * hy_z > 0:
        note = "→ 투자등급(BBB)까지 크게 같이 움직였습니다. 위험 회사채만이 아니라 신용 시장 전체의 변화입니다."
    elif abs(ccc_z) >= 2 and ccc_z * hy_z > 0:
        note = "→ BBB는 평소 수준인데 CCC가 크게 움직였습니다. 신용이 약한 회사 쪽에 몰린 변화입니다."
    else:
        note = "→ BBB·CCC는 평소 움직임 범위입니다."
    return ["", "<b>등급별로 보면</b>"] + out + [note]


def build_message(s, ev, source, forced):
    i = s.index.get_loc(ev["date"])
    win = s.iloc[max(0, i - 251):i + 1]          # 최근 약 1년(252거래일)
    pct = (win <= ev["bp"]).mean() * 100
    d20 = ev["bp"] - float(s.iloc[i - 20]) if i >= 20 else None
    sd = typical_5d(s, i)
    ev["z"] = ev["d5"] / sd if sd and ev["d5"] is not None else 0.0
    up = ev["dir"] == "up"
    head = ("🔺 넓어짐(신용 위험 ↑)" if up else "🔻 좁혀짐(신용 위험 ↓)") if ev["reasons"] else "변화 작음 — 기준 미달"
    lines = [f"🧯 <b>하이일드 스프레드 급변</b> · {ev['date']}" + (" · 수동 테스트" if forced else ""),
             f"{head}",
             "",
             f"하이일드(HY) 전체 <b>{ev['bp']:.0f}bp</b>",
             f"· 하루 {ev['d1']:+.0f}bp"
             + (f" · 5거래일 {ev['d5']:+.0f}bp{times(ev['d5'], sd)}" if ev["d5"] is not None else "")
             + (f" · 20거래일 {d20:+.0f}bp" if d20 is not None else ""),
             f"· 최근 1년 범위 {win.min():.0f}~{win.max():.0f}bp, 지금은 아래에서 {pct:.0f}% 위치"]
    if ev["reasons"]:
        lines += ["", "<b>걸린 조건</b>"] + [f"· {t}" for _, t in ev["reasons"]]
    lines += companion_lines(ev)
    lines += ["",
              ("하이일드 = 신용등급 낮은 회사채. 스프레드가 넓어지면 돈 빌리기가 비싸지고 "
               "시장이 부도 위험을 더 크게 본다는 뜻입니다. AI 인프라 기업의 자금 조달 여건을 보는 지표입니다.")
              if up else
              ("하이일드 = 신용등급 낮은 회사채. 스프레드가 좁혀지면 시장이 부도 위험을 덜 보고 "
               "자금 조달 여건이 풀린다는 뜻입니다."),
              "",
              f"<a href=\"{DASH_URL}\">AI 사이클 대시보드</a> · 데이터 FRED {SERIES}"
              + ("" if source == "FRED" else " (수집 실패 — 저장된 값 기준)")]
    return "\n".join(lines)


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def backtest():
    s = load_cached()
    recent, n = [], 0
    for i in range(5, len(s)):
        ev = evaluate(s, i)
        fired = bool(ev["reasons"]) and not suppressed(ev, recent[-REPEAT_DAYS:])
        recent.append(ev["dir"] if fired else None)
        if fired:
            n += 1
            print(f"{ev['date']}  {ev['bp']:.0f}bp  하루 {ev['d1']:+.0f}  5일 {ev['d5']:+.0f}  "
                  + " / ".join(t for _, t in ev["reasons"]))
    print(f"→ {s.index[5].date()} ~ {s.index[-1].date()} 동안 {n}번")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backtest", action="store_true")
    add_target_arg(ap)
    args = ap.parse_args()
    if args.backtest:
        return backtest()

    s, source = load_series()
    state = load_state()
    last_checked = state.get("last_checked_date", "")
    dates = [d.date().isoformat() for d in s.index]
    if args.force:
        todo = [len(s) - 1]
    else:
        # 확인 안 한 새 관측을 모두 본다(수집 실패로 며칠 밀렸다 한꺼번에 들어온 날 대비).
        # 처음 실행이면 마지막 관측만.
        todo = [i for i in range(5, len(s)) if dates[i] > last_checked][-10:] if last_checked else [len(s) - 1]
        if not todo:
            print(f"[{source}] 새 데이터 없음 (마지막 {dates[-1]}, 확인 {last_checked}) — 종료")
            return

    cfg = None
    for i in todo:
        ev = evaluate(s, i)
        print(f"[{source}] {ev['date']} HY OAS {ev['bp']:.0f}bp, 하루 {ev['d1']:+.0f}, "
              f"5거래일 {ev['d5']:+.0f} → 조건 {len(ev['reasons'])}개")
        if not args.force:
            # 직전 REPEAT_DAYS 거래일 안에 보낸 알림의 방향
            recent_dates = set(dates[max(0, i - REPEAT_DAYS):i])
            recent = [a["dir"] for a in state.get("alerts", []) if a["date"] in recent_dates]
            if not ev["reasons"] or suppressed(ev, recent):
                if ev["reasons"]:
                    print("  같은 방향 5거래일 알림을 최근에 보냄 — 건너뜀")
                if not args.dry_run:
                    state["last_checked_date"] = ev["date"]
                continue

        msg = build_message(s, ev, source, args.force)
        if args.dry_run:
            print("---- 메시지 미리보기 ----")
            print(msg)
            continue
        cfg = cfg or json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))
        send(cfg, resolve_targets(cfg, args.to), msg)
        if not args.force:
            state["last_checked_date"] = ev["date"]
            state["alerts"] = (state.get("alerts", []) + [{"date": ev["date"], "dir": ev["dir"], "bp": ev["bp"]}])[-30:]

    if not args.force and not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
