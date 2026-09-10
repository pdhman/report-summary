"""
Polymarket Macro Watch — 실행 진입점

  python main.py run              # 1회 실행: 수집 → PMSS → docs/polymarket.html + 요약/히스토리 JSON (+알림)
  python main.py run --dry        # 히스토리·알림 상태를 건드리지 않고 화면·HTML 만
  python main.py loop --every 60  # 60분마다 반복 (로컬 상시 실행용)

파이프라인:
  Gamma 태그별 이벤트 → 그룹 분류(config.GROUPS) → CLOB 히스토리(신호 시장 상한)
  → ΔP/Z/유동성/지속성 → Cross-asset 확인(yfinance) → Macro Regime → PMSS → 알림 / 대시보드
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timezone

import config as C
import cross_asset
import pmss as P
import report
import signals
import store
from polymarket_client import STATS, enrich, fetch_price_history, using_proxy
from selector import Event, key_series_markets, select_events, signal_markets

log = logging.getLogger("pmw")

REGIME_WEIGHT = {"A": 1.0, "B": 0.5, "X": 0.0}


def _downsample(hist: list[tuple[int, float]], n: int = 84) -> list[list]:
    if len(hist) <= n:
        return [[t, round(p, 4)] for t, p in hist]
    step = len(hist) / n
    out = [hist[int(i * step)] for i in range(n)]
    out.append(hist[-1])
    return [[t, round(p, 4)] for t, p in out]


def _unit(ev: Event) -> str:
    text = " ".join(m.label for m in ev.markets).lower()
    if ev.slug.startswith("how-many") and "dissent" not in ev.slug and "ships" not in ev.slug:
        return "회"
    if "bps" in text or "bp" in text:
        return "bp"
    if "%" in text:
        return "%"
    if "$" in text:
        return "$"
    if re.search(r"\d+k\b", text):
        return "K"
    if "how-many" in ev.slug:
        return "회"
    return ""


def build_row(mk, ev: Event, snap: cross_asset.AssetSnapshot, now: float) -> tuple[dict, float]:
    sig = signals.compute(mk, now)
    spec = C.GROUPS[ev.group]
    axis = spec["axis"]
    axis_dir = sig.direction * mk.polarity
    conf = cross_asset.confirm(axis, axis_dir, snap) if mk.polarity else cross_asset.confirm("none", 0, snap)
    sc = P.score(sig, conf, spec["importance"])
    w = REGIME_WEIGHT[sig.liq_grade]
    if w == 0 and mk.liquidity >= 5_000 and (mk.spread or 1) <= C.MAX_SPREAD:
        w = 0.25       # 얇지만 살아있는 시장은 레짐 집계에만 소폭 반영
    row = {
        "id": mk.market_id, "ev": ev.slug, "group": ev.group, "kind": ev.kind,
        "q": mk.question, "label": mk.label, "url": mk.url, "end": mk.end_date[:10],
        "prob": round(sig.prob, 4),
        "d1h": None if sig.dp_1h is None else round(sig.dp_1h, 4),
        "d6h": None if sig.dp_6h is None else round(sig.dp_6h, 4),
        "d24": None if sig.dp_24h is None else round(sig.dp_24h, 4),
        "d7": None if sig.dp_7d is None else round(sig.dp_7d, 4),
        "z": round(sig.z, 2), "spread": mk.spread, "vol24": round(mk.volume24h),
        "liq": round(mk.liquidity), "liq_grade": sig.liq_grade,
        "persist": round(sig.persistence, 2), "band": sig.band, "dir": sig.direction,
        "polarity": mk.polarity, "value": mk.value, "lite": sig.lite,
        "confirm": round(conf.score, 2), "confirmed": conf.confirmed, "dtype": conf.divergence_type,
        "agree": conf.agree, "disagree": conf.disagree, "flat": conf.flat,
        "pmss": sc.total, "pmss_band": sc.label,
        "s": {"shock": sc.shock, "confirm": sc.confirm, "liq": sc.liquidity,
              "persist": sc.persistence, "imp": sc.importance},
        "hist7": _downsample(mk.history) if mk.history else [],
        "alert": False,
    }
    # 알림·콘솔 호환 필드
    row.update({"market_id": mk.market_id, "category": ev.group, "question": mk.question,
                "dp_1h": sig.dp_1h, "dp_24h": sig.dp_24h, "dp_7d": sig.dp_7d,
                "direction": sig.direction, "volume24h": mk.volume24h, "persistence": sig.persistence,
                "assets_checked": list(C.EXPECTED_DIRECTION.get(axis, {})),
                "band_pmss": sc.label, "s_shock": sc.shock, "s_confirm": sc.confirm,
                "s_liq": sc.liquidity, "s_persist": sc.persistence, "s_imp": sc.importance})
    return row, w


def build_event_payload(ev: Event, rows: list[dict]) -> dict:
    d = {
        "slug": ev.slug, "title": ev.title, "url": ev.url, "kind": ev.kind, "group": ev.group,
        "vol24": round(ev.volume24h), "liq": round(ev.liquidity), "end": ev.end_date[:10],
        "ev_sign": ev.ev_sign, "unit": _unit(ev), "markets": rows,
    }
    if ev.kind == "dist":
        vals = [(r["value"], r["prob"], r["d24"]) for r in rows if r["value"] is not None]
        if vals:
            tot = sum(p for _, p, _ in vals) or 1.0
            mu = sum(v * p for v, p, _ in vals) / tot
            prev = [(v, max(p - (d or 0), 0.0)) for v, p, d in vals]
            tot0 = sum(p for _, p in prev) or 1.0
            mu0 = sum(v * p for v, p in prev) / tot0
            d["mean"] = round(mu, 3)
            d["mean_d24"] = round(mu - mu0, 3)
        top = max(rows, key=lambda r: r["prob"])
        d["top"] = {"label": top["label"] or top["q"], "prob": top["prob"], "d24": top["d24"]}
    return d


def should_alert(r: dict) -> bool:
    if r["liq_grade"] == "X":
        return False
    trig = (abs(r["dp_1h"] or 0) >= C.ALERT["dp_1h"]
            or abs(r["dp_24h"] or 0) >= C.ALERT["dp_24h"]
            or abs(r["z"]) >= C.ALERT["z"])
    return trig and r["pmss"] >= C.ALERT["pmss_min"]


def run_once(dry: bool = False, base_net: float = 0.50, html: bool = True,
             max_history: int | None = None):
    now = time.time()
    if max_history is not None:
        C.MAX_SIGNAL_HISTORY = max_history

    events = select_events()
    sig_mks = signal_markets(events)
    key = key_series_markets(events)
    key_ids = {m.market_id for _, m in key}
    log.info("signal markets: %d, key series: %d, proxy=%s", len(sig_mks), len(key), using_proxy())
    for i, m in enumerate(sig_mks):
        enrich(m, with_long=m.market_id in key_ids)
        if not using_proxy():
            time.sleep(0.12)
    for _, m in key:
        if not m.long_history:
            m.long_history = fetch_price_history(m.yes_token, "max", 1440)

    snap = cross_asset.fetch_assets()

    axis_moves: dict[str, float] = {}
    axis_w: dict[str, float] = {}
    groups_payload = []
    all_rows: list[dict] = []
    for g in C.GROUP_ORDER:
        spec = C.GROUPS[g]
        evs = [e for e in events if e.group == g]
        if not evs:
            continue
        ev_payloads = []
        for ev in evs:
            rows = []
            for mk in ev.markets:
                row, w = build_row(mk, ev, snap, now)
                rows.append(row)
                all_rows.append(row)
                if spec["axis"] != "none" and mk.polarity and row["d24"] is not None and w > 0:
                    axis_moves[spec["axis"]] = axis_moves.get(spec["axis"], 0.0) + w * mk.polarity * row["d24"]
                    axis_w[spec["axis"]] = axis_w.get(spec["axis"], 0.0) + w
            ev_payloads.append(build_event_payload(ev, rows))
        groups_payload.append({"key": g, "title": spec["title"], "icon": spec["icon"],
                               "desc": spec["desc"], "axis": spec["axis"], "events": ev_payloads})

    moves = {a: (axis_moves[a] / axis_w[a] if axis_w.get(a) else 0.0) for a in axis_moves}
    regime = P.classify_regime(moves)
    top = max((r["pmss"] for r in all_rows), default=0.0)
    new_net = P.exposure_adjustment(regime.name, top, base_net)

    print(report.console_table(all_rows, regime, snap))

    # ── 알림 ──
    sent, alert_rows = 0, []
    for r in sorted(all_rows, key=lambda x: -x["pmss"]):
        if not should_alert(r):
            continue
        r["alert"] = True
        alert_rows.append(r)
        if dry or store.in_cooldown(r["id"], r["dir"], C.ALERT["cooldown_hours"]):
            continue
        msg = report.format_alert(r, regime, snap, base_net, new_net)
        print("\n" + "=" * 70 + "\n" + msg.replace("<b>", "").replace("</b>", ""))
        if report.telegram_send(msg):
            store.log_alert(r["id"], r["pmss"], r["dir"])
            sent += 1

    # ── 히스토리 누적 ──
    hist_row = {
        "ts": int(now), "regime": regime.name,
        "e": round(regime.easing, 4), "r": round(regime.recession, 4),
        "i": round(regime.inflation, 4), "k": round(regime.risk, 4), "top": top,
        "p": {r["id"]: round(r["prob"], 3) for r in all_rows if r["hist7"] or r["id"] in key_ids},
    }
    history = store.append_history(hist_row) if not dry else store.load_history() + [hist_row]

    # ── 장기 시리즈 ──
    series = []
    for name, m in key:
        if not m.long_history:
            continue
        dates = [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d") for t, _ in m.long_history]
        vals = [round(p, 4) for _, p in m.long_history]
        # 같은 날 여러 점이면 마지막 값
        dd, vv = [], []
        for d_, v_ in zip(dates, vals):
            if dd and dd[-1] == d_:
                vv[-1] = v_
            else:
                dd.append(d_)
                vv.append(v_)
        series.append({"name": name, "id": m.market_id, "ev": m.event_slug, "url": m.url,
                       "dates": dd, "values": vv, "last": vals[-1]})

    data = {
        "generated_at": report.now_kst_str(), "generated_ts": int(now),
        "source": "proxy(r.jina.ai)" if using_proxy() else "direct",
        "stats": dict(STATS),
        "regime": {"icon": regime.icon, "name": regime.name, "easing": round(regime.easing, 4),
                   "recession": round(regime.recession, 4), "inflation": round(regime.inflation, 4),
                   "risk": round(regime.risk, 4), "signals": regime.signals, "arrows": regime.arrows},
        "exposure": {"base": base_net, "suggested": new_net, "top_pmss": top},
        "assets": [{"ticker": t, "name": C.ASSETS[t], "chg": round(snap.chg_24h[t], 5),
                    "arrow": snap.arrow(t), "last": snap.last.get(t)}
                   for t in C.ASSETS if t in snap.chg_24h],
        "assets_ok": snap.ok,
        "expected": C.EXPECTED_DIRECTION,
        "groups": groups_payload,
        "series": series,
        "history": history[-1500:],
        "alerts": [r["id"] for r in alert_rows],
        "recent_alerts": store.recent_alerts(),
        "thresholds": {"alert": C.ALERT, "liquidity": C.LIQUIDITY, "pmss_bands": C.PMSS_BANDS},
    }

    if html:
        path = report.render_html(data)
        log.info("HTML → %s", path)
        report.write_summary(_summary(data, all_rows))

    log.info("run done: %d events, %d markets, %d alerts (%d sent), regime=%s, stats=%s",
             len(events), len(all_rows), len(alert_rows), sent, regime.name, STATS)
    return data


def _summary(data: dict, rows: list[dict]) -> dict:
    fomc = None
    rec = None
    for g in data["groups"]:
        for e in g["events"]:
            if fomc is None and e["slug"].startswith("fed-decision-in-") and e["kind"] == "dist":
                fomc = {"title": e["title"], "top": e.get("top"), "mean": e.get("mean"),
                        "mean_d24": e.get("mean_d24"), "url": e["url"]}
            if rec is None and e["slug"].startswith("us-recession-by-end-of-2026"):
                m = e["markets"][0]
                rec = {"prob": m["prob"], "d24": m["d24"], "url": e["url"]}
    top = max(rows, key=lambda r: r["pmss"]) if rows else None
    return {
        "date": data["generated_at"][:16],
        "regime_icon": data["regime"]["icon"], "regime_name": data["regime"]["name"],
        "arrows": data["regime"]["arrows"],
        "fomc": fomc, "recession": rec,
        "top_signal": None if not top else {
            "q": (top["label"] + " · " if top["label"] else "") + top["q"],
            "pmss": top["pmss"], "band": top["pmss_band"], "d24": top["d24"], "prob": top["prob"]},
        "exposure": data["exposure"], "alerts": len(data["alerts"]),
        "n_markets": len(rows), "source": data["source"],
    }


def cli():
    ap = argparse.ArgumentParser(prog="polymarket_macro_watch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry", action="store_true", help="히스토리·알림 상태 미저장")
    r.add_argument("--net", type=float, default=0.50)
    r.add_argument("--no-html", action="store_true")
    r.add_argument("--max-history", type=int, default=None, help="CLOB 히스토리 호출 상한(테스트용)")
    l = sub.add_parser("loop")
    l.add_argument("--every", type=int, default=60, help="분")
    l.add_argument("--net", type=float, default=0.50)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if a.cmd == "run":
        run_once(dry=a.dry, base_net=a.net, html=not a.no_html, max_history=a.max_history)
    elif a.cmd == "loop":
        while True:
            try:
                run_once(base_net=a.net)
            except Exception as e:      # noqa: BLE001
                log.exception("run failed: %s", e)
            time.sleep(a.every * 60)


if __name__ == "__main__":
    cli()
