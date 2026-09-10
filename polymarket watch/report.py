"""
출력 계층
- console_table : 실행 로그용 표
- format_alert / telegram_send : 이벤트 쇼크 알림
- render_html   : template.html 의 __DATA_JSON__ 치환 → docs/polymarket.html (알파노트 내비 주입)
- write_summary : 홈 카드용 요약 JSON
"""
from __future__ import annotations

import html
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import config as C
from cross_asset import DIVERGENCE_LABEL, AssetSnapshot

log = logging.getLogger("pmw.report")
KST = timezone(timedelta(hours=9))


def _pct(v, signed=True):
    if v is None:
        return "  n/a"
    s = f"{v*100:+.0f}" if signed else f"{v*100:.0f}"
    return f"{s:>5}"


def console_table(rows: list[dict], regime, snap: AssetSnapshot) -> str:
    lines = [f"{regime.icon} Current Macro Regime: {regime.name}", f"   {regime.arrows}",
             "   " + " | ".join(f"{k}: {v}" for k, v in regime.signals.items()), ""]
    hdr = f"{'Cat':<11}{'Event':<58}{'Prob':>5}{'1H':>6}{'24H':>6}{'7D':>6}{'Z':>6}{'Liq':>4}{'Conf':>5}{'PMSS':>6}  Band"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in sorted(rows, key=lambda x: -x["pmss"])[:40]:
        q = (r["label"] + " · " if r["label"] else "") + r["question"]
        conf = "OK" if r["confirmed"] else ("--" if r["dtype"] == 1 else "NO")
        lines.append(
            f"{r['category']:<11}{q[:56]:<58}{_pct(r['prob'], False)}{_pct(r['dp_1h'])}{_pct(r['dp_24h'])}"
            f"{_pct(r['dp_7d'])}{r['z']:>6.1f}{r['liq_grade']:>4}{conf:>5}{r['pmss']:>6.0f}  {r['band_pmss']}"
        )
    if snap.ok:
        lines.append("")
        lines.append("Cross-asset 24H: " + "  ".join(
            f"{C.ASSETS[t]} {snap.arrow(t)}{snap.chg_24h.get(t, 0)*100:+.1f}%"
            for t in C.ASSETS if t in snap.chg_24h))
    return "\n".join(lines)


def format_alert(r: dict, regime, snap: AssetSnapshot, base_net: float, new_net: float) -> str:
    dir_txt = "↑" if r["direction"] > 0 else "↓"
    p_prev = r["prob"] - (r["dp_24h"] or 0)
    xs = "\n".join(
        f"  {C.ASSETS[t]}: {snap.arrow(t)} {snap.chg_24h[t]*100:+.1f}%"
        for t in r["assets_checked"] if t in snap.chg_24h
    ) or "  (자산 데이터 없음)"
    q = (r["label"] + " · " if r["label"] else "") + r["question"]
    return (
        f"🚨 <b>PMSS {r['band_pmss']}</b>  [{r['category']}]\n"
        f"<b>{html.escape(q)}</b>\n"
        f"{p_prev*100:.0f}% → {r['prob']*100:.0f}%  ({(r['dp_24h'] or 0)*100:+.0f}%p / 24H, "
        f"{(r['dp_1h'] or 0)*100:+.0f}%p / 1H) {dir_txt}\n\n"
        f"Z-score: {r['z']:+.1f}   Volume24h: ${r['volume24h']/1e6:.2f}m   "
        f"Spread: {(r['spread'] or 0)*100:.1f}%p   Liq: {r['liq_grade']}\n"
        f"Persistence: {r['persistence']*100:.0f}%\n\n"
        f"<b>Cross-asset</b>  ({DIVERGENCE_LABEL[r['dtype']]})\n{xs}\n\n"
        f"<b>PMSS {r['pmss']:.0f}/100</b>  "
        f"(Shock {r['s_shock']:.0f} · Confirm {r['s_confirm']:.0f} · Liq {r['s_liq']:.0f} · "
        f"Persist {r['s_persist']:.0f} · Imp {r['s_imp']:.0f})\n\n"
        f"{regime.icon} <b>Regime: {regime.name}</b>\n{regime.arrows}\n"
        + " · ".join(f"{k} {v}" for k, v in regime.signals.items()) + "\n"
        f"Net Exposure 제안: {base_net*100:.0f}% → <b>{new_net*100:.0f}%</b>\n\n"
        f"{r['url']}"
    )


def telegram_send(text: str) -> bool:
    if not C.TELEGRAM_BOT_TOKEN or not C.TELEGRAM_CHAT_ID:
        log.warning("Telegram 미설정 — PMW_TG_TOKEN / PMW_TG_CHAT 환경변수 또는 telegram/config.json 필요")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=C.HTTP_TIMEOUT,
        )
        if not r.ok:
            log.error("telegram %s: %s", r.status_code, r.text[:200])
        return r.ok
    except requests.RequestException as e:
        log.error("telegram send failed: %s", e)
        return False


def _nav_injection() -> str:
    """알파노트 공용 하단 내비 + 사이트 변수 보충(크립토·수급 페이지와 같은 방식)."""
    try:
        sys.path.insert(0, C.REPO_DIR)
        import site_nav  # noqa: WPS433
    except Exception as e:      # noqa: BLE001
        log.warning("site_nav 사용 불가(%s) — 내비 없이 출력", e)
        return ""
    shim = (
        "<style>\n"
        "  :root { --panel:#ffffff; --line:#e6e8eb; --accent:#3b5bdb; --muted:#6b7280; }\n"
        '  :root[data-theme="dark"] { --panel:#171b21; --line:#252b33; '
        "--accent:#748ffc; --muted:#9aa2ad; }\n"
        "</style>"
    )
    return site_nav.nav_html("polymarket") + shim + site_nav.NAV_CSS


def render_html(data: dict, path: str = C.HTML_OUT) -> str:
    with open(C.TEMPLATE_HTML, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</script", "<\\/script")
    doc = tpl.replace("__DATA_JSON__", payload)
    nav = _nav_injection()
    if nav:
        doc = doc.replace("</body>", nav + "</body>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def write_summary(summary: dict, path: str = C.SUMMARY_JSON) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return path


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
