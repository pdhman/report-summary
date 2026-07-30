# -*- coding: utf-8 -*-
"""계절성 대시보드용 일봉 데이터 수집.

사용법:
    python fetch_data.py              # tickers.json에 등록된 전체 티커 갱신
    python fetch_data.py QQQ AAPL    # 티커 추가 등록 후 전체 갱신

야후 파이낸스 심볼 기준 (한국 주식은 .KS/.KQ 접미사, 예: 069500.KS).
결과는 data.js 로 저장되며 index.html 이 이를 읽어 렌더링한다.
"""
import json
import sys
from pathlib import Path

import yfinance as yf

BASE = Path(__file__).parent
TICKERS_FILE = BASE / "tickers.json"
DATA_FILE = BASE.parent / "reports" / "data.js"   # 알파노트(분석 탭)가 읽는 기본 티커 데이터

DEFAULT_TICKERS = {
    "SPY": "SPY (S&P 500 ETF)",
    "069500.KS": "KODEX 200",
}


def load_tickers() -> dict:
    if TICKERS_FILE.exists():
        return json.loads(TICKERS_FILE.read_text(encoding="utf-8"))
    return dict(DEFAULT_TICKERS)


def main() -> None:
    tickers = load_tickers()
    for sym in sys.argv[1:]:
        sym = sym.upper()
        tickers.setdefault(sym, sym)

    data = {}
    for sym, name in tickers.items():
        print(f"[{sym}] 다운로드 중...", flush=True)
        hist = yf.Ticker(sym).history(period="max", auto_adjust=True)
        closes = hist["Close"].dropna()
        if closes.empty:
            print(f"[{sym}] 데이터 없음 — 건너뜀")
            continue
        data[sym] = {
            "name": name,
            "dates": [int(d.strftime("%Y%m%d")) for d in closes.index],
            "closes": [round(float(c), 4) for c in closes],
        }
        print(f"[{sym}] {len(closes)}일 ({closes.index[0].date()} ~ {closes.index[-1].date()})")

    TICKERS_FILE.write_text(
        json.dumps(tickers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    DATA_FILE.write_text(
        "const SEASONALITY_DATA = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"저장 완료: {DATA_FILE}")


if __name__ == "__main__":
    main()
