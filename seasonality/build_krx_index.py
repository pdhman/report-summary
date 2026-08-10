# -*- coding: utf-8 -*-
"""KRX 전 종목 이름↔코드 목록 생성 (reports/data/krx.js).

분석 탭 검색창에서 '코미코' 처럼 한글 종목명으로 찾을 수 있게 한다.
가격 데이터는 담지 않으므로 파일이 작다(약 70KB).

    const KRX_INDEX = [["183300","코미코","KQ"], ...];

실행: python seasonality/build_krx_index.py
"""
import json
from pathlib import Path

import pandas as pd
import FinanceDataReader as fdr

OUT = Path(__file__).parent.parent / "reports" / "data" / "krx.js"


def main() -> None:
    frames = []
    for market, suffix in [("KOSPI", "KS"), ("KOSDAQ", "KQ")]:
        df = fdr.StockListing(market)[["Code", "Name"]].copy()
        df["sfx"] = suffix
        frames.append(df)
    df = pd.concat(frames).dropna(subset=["Code", "Name"])
    df = df[df["Code"].str.match(r"^\d{6}$")].drop_duplicates(subset=["Code"])
    df = df.sort_values("Name")

    rows = [[r.Code, str(r.Name).strip(), r.sfx] for r in df.itertuples()]
    body = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    OUT.write_text(f"const KRX_INDEX = [\n{body}\n];\n", encoding="utf-8")
    print(f"저장: {OUT} ({len(rows)}종목, {OUT.stat().st_size/1024:,.0f} KB)")


if __name__ == "__main__":
    main()
