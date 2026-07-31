# -*- coding: utf-8 -*-
"""분석 탭(차트·계절성)이 서버 없이도 동작하도록 유니버스 일봉을 정적 파일로 굽는다.

산출물 (reports/data/):
    <티커>.json   {"symbol","name","group","currency","bars":[[yyyymmdd,o,h,l,c,v,adj], ...]}
                  o/h/l/c/v = 무조정 실거래가(차트), adj = 수정종가(계절성)
                  한 줄에 하루씩 기록해 매일 갱신 시 git diff 가 몇 줄만 바뀐다.
    index.js      const TICKER_INDEX = [...]  — 종목 목록(이름·그룹·기간). 화면 목록용.

종목이 100개가 넘으므로 한 파일에 몰지 않고 종목별로 나눈다. 페이지는 선택한
종목의 파일만 내려받으므로 첫 로딩이 가볍다.

사용법:
    python seasonality/fetch_data.py             # 유니버스 전체 갱신
    python seasonality/fetch_data.py SPY QQQ     # 지정 종목만 갱신
    python seasonality/fetch_data.py --missing   # 파일이 없는 종목만 (중단 후 이어받기)

조회에 실패한 종목은 기존 파일을 그대로 두므로, 야후의 일시적 차단이
사이트의 데이터를 지우지 않는다.
"""
import json
import math
import sys
from pathlib import Path

import yfinance as yf

from universe import UNIVERSE, flat

BASE = Path(__file__).parent
OUT_DIR = BASE.parent / "reports" / "data"
INDEX_FILE = OUT_DIR / "index.js"


def fetch(symbol: str):
    """(bars, currency) 또는 None."""
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


def write_ticker(sym: str, name: str, group: str, bars: list, currency: str) -> None:
    head = json.dumps({"symbol": sym, "name": name, "group": group,
                       "currency": currency}, ensure_ascii=False)[:-1]
    lines = [head + ', "bars": [']
    lines.extend(json.dumps(b, separators=(",", ":")) + "," for b in bars[:-1])
    lines.append(json.dumps(bars[-1], separators=(",", ":")))
    lines.append("]}")
    (OUT_DIR / f"{sym}.json").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index() -> None:
    """디스크에 실제로 존재하는 종목만 모아 목록 파일을 만든다."""
    entries = []
    for group, items in UNIVERSE:
        for sym, name in items.items():
            path = OUT_DIR / f"{sym}.json"
            if not path.exists():
                continue
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            bars = d.get("bars") or []
            if not bars:
                continue
            entries.append({"s": sym, "n": name, "g": group,
                            "c": d.get("currency", ""),
                            "f": bars[0][0], "l": bars[-1][0], "k": len(bars)})
    lines = ["const TICKER_INDEX = ["]
    lines.extend(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + ","
                 for e in entries[:-1])
    if entries:
        lines.append(json.dumps(entries[-1], ensure_ascii=False, separators=(",", ":")))
    lines.append("];")
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"목록 갱신: {INDEX_FILE.name} ({len(entries)}종목)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    known = flat()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_missing = "--missing" in sys.argv

    if args:
        targets = [(s.upper(), *known.get(s.upper(), (s.upper(), "기타"))) for s in args]
    else:
        targets = [(sym, name, group) for group, items in UNIVERSE
                   for sym, name in items.items()]
    if only_missing:
        targets = [t for t in targets if not (OUT_DIR / f"{t[0]}.json").exists()]

    ok, failed = 0, []
    for i, (sym, name, group) in enumerate(targets, 1):
        try:
            got = fetch(sym)
        except Exception as e:
            print(f"[{i}/{len(targets)}] {sym} 실패: {str(e)[:60]}")
            failed.append(sym)
            continue
        if not got:
            print(f"[{i}/{len(targets)}] {sym} 데이터 없음")
            failed.append(sym)
            continue
        bars, currency = got
        write_ticker(sym, name, group, bars, currency)
        ok += 1
        print(f"[{i}/{len(targets)}] {sym} {len(bars)}일 ({bars[0][0]}~{bars[-1][0]})")

    write_index()
    total_mb = sum(f.stat().st_size for f in OUT_DIR.glob("*.json")) / 1024 / 1024
    print(f"완료: 갱신 {ok}종목 · 실패 {len(failed)} · 전체 {total_mb:,.1f} MB")
    if failed:
        print(f"실패 목록(기존 파일 유지): {failed}")


if __name__ == "__main__":
    main()
