# -*- coding: utf-8 -*-
"""분석 탭(차트·계절성)이 서버 없이도 동작하도록 기본 티커의 일봉을 정적 파일로 굽는다.

산출물: reports/market_data.js
    const MARKET_DATA = {"SPY": {"name":..., "bars":[[yyyymmdd,o,h,l,c,v,adj], ...]}}
    - o/h/l/c/v : 무조정 실거래가 → 차트(캔들·거래량)
    - adj       : 배당·분할 반영 수정종가 → 계절성 통계
    한 줄에 하루씩 기록해 매일 갱신 시 git diff가 몇 줄만 바뀌게 한다.

사용법:
    python seasonality/fetch_data.py              # 등록된 티커 전체 갱신
    python seasonality/fetch_data.py QQQ AAPL     # 티커 추가 등록 후 갱신

야후 파이낸스 심볼 기준(한국 주식은 .KS/.KQ). 조회에 실패한 티커는 기존
파일에 있던 데이터를 그대로 유지하므로, 일시적 차단이 파이프라인을 깨거나
사이트의 데이터를 지우지 않는다.
"""
import json
import math
import re
import sys
from pathlib import Path

import yfinance as yf

BASE = Path(__file__).parent
TICKERS_FILE = BASE / "tickers.json"
OUT_FILE = BASE.parent / "reports" / "market_data.js"
VAR_NAME = "MARKET_DATA"

DEFAULT_TICKERS = {
    "SPY": "SPY (S&P 500)",
    "QQQ": "QQQ (나스닥 100)",
    "069500.KS": "KODEX 200",
    "005930.KS": "삼성전자",
}


def load_tickers() -> dict:
    if TICKERS_FILE.exists():
        saved = json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
        # 기본 티커는 항상 포함 (파일이 옛 버전이어도 4종목은 보장)
        return {**DEFAULT_TICKERS, **saved}
    return dict(DEFAULT_TICKERS)


def load_existing() -> dict:
    """이전 산출물을 읽어둔다 (조회 실패 시 폴백용)."""
    if not OUT_FILE.exists():
        return {}
    text = OUT_FILE.read_text(encoding="utf-8")
    m = re.search(r"=\s*(\{.*\})\s*;\s*$", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def fetch(symbol: str) -> tuple[list, str] | None:
    tk = yf.Ticker(symbol)
    hist = tk.history(period="max", auto_adjust=False)
    if hist.empty:
        return None
    try:
        currency = (tk.history_metadata or {}).get("currency") or ""
    except Exception:
        currency = ""
    adj_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
    cols = hist[["Open", "High", "Low", "Close", "Volume", adj_col]]
    bars = []
    for ts, (o, h, l, c, vol, adj) in zip(cols.index, cols.values):
        if any(math.isnan(v) for v in (o, h, l, c, adj)):
            continue
        bars.append([
            int(ts.strftime("%Y%m%d")),
            round(float(o), 4), round(float(h), 4), round(float(l), 4), round(float(c), 4),
            0 if math.isnan(vol) else int(vol), round(float(adj), 4),
        ])
    return (bars, currency) if bars else None


def main() -> None:
    tickers = load_tickers()
    for sym in sys.argv[1:]:
        sym = sym.strip().upper()
        if sym:
            tickers.setdefault(sym, sym)

    existing = load_existing()
    out, failed = {}, []
    for sym, name in tickers.items():
        try:
            got = fetch(sym)
        except Exception as e:
            print(f"[{sym}] 조회 실패: {e}")
            got = None
        if got:
            bars, currency = got
            out[sym] = {"name": name, "currency": currency, "bars": bars}
            print(f"[{sym}] {len(bars)}일 ({bars[0][0]} ~ {bars[-1][0]}) {currency}")
        elif sym in existing:
            out[sym] = existing[sym]
            failed.append(sym)
            print(f"[{sym}] 조회 실패 → 기존 데이터 유지 ({len(existing[sym]['bars'])}일)")
        else:
            failed.append(sym)
            print(f"[{sym}] 조회 실패 · 기존 데이터도 없음 → 건너뜀")

    if not out:
        print("모든 티커 조회 실패 — 기존 파일을 그대로 둡니다.")
        sys.exit(1)

    lines = [f"const {VAR_NAME} = {{"]
    for i, (sym, d) in enumerate(out.items()):
        head = json.dumps(d["name"], ensure_ascii=False)
        cur = json.dumps(d.get("currency", ""))
        lines.append(f'{json.dumps(sym)}: {{"name": {head}, "currency": {cur}, "bars": [')
        lines.extend(json.dumps(b, separators=(",", ":")) + "," for b in d["bars"][:-1])
        lines.append(json.dumps(d["bars"][-1], separators=(",", ":")))
        lines.append("]}" + ("," if i < len(out) - 1 else ""))
    lines.append("};")
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    TICKERS_FILE.write_text(
        json.dumps(tickers, ensure_ascii=False, indent=2), encoding="utf-8")

    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"저장 완료: {OUT_FILE} ({size_kb:,.0f} KB, {len(out)}종목)")
    if failed:
        print(f"주의: 갱신되지 않은 티커 {failed}")


if __name__ == "__main__":
    main()
