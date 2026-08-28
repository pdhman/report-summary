# -*- coding: utf-8 -*-
"""계절성 분석용 한국 주요 종목 목록 생성 → seasonality/universe_kr.py

  - 코스피 200 : 네이버 KPI200 페이지에서 실제 구성종목 200개
  - 코스닥 150 : 정확한 구성종목 공개 소스가 없어 코스닥 시총 상위 150개로 대체
  - 관심 종목  : 코스맥스·한국콜마·에이피알

실행: python seasonality/build_kr_universe.py
"""
import re
import time
from pathlib import Path

import requests
import pandas as pd
import FinanceDataReader as fdr

OUT = Path(__file__).parent / "universe_kr.py"
HEADERS = {"User-Agent": "Mozilla/5.0"}
WATCH = {"192820": "코스맥스", "161890": "한국콜마", "278470": "에이피알"}


def kospi200() -> list:
    """KPI200 구성종목 — 신형 front-api(finance.naver 9/10 종료 대비, 2026-08-28 전환)."""
    rows = []
    for page in range(1, 15):
        url = ("https://m.stock.naver.com/front-api/stock/domestic/index/"
               f"enrollStock/list?code=KPI200&page={page}&pageSize=20")
        r = requests.get(url, headers={**HEADERS,
                                       "Referer": "https://m.stock.naver.com/"},
                         timeout=20)
        stocks = ((r.json().get("result") or {}).get("stocks")) or []
        if not stocks:
            break
        rows.extend((str(s.get("itemCode", "")).zfill(6),
                     str(s.get("name", "")).strip()) for s in stocks)
        time.sleep(0.2)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for c, n in rows:
        if c not in seen:
            seen.add(c)
            out.append((c, n))
    return out


def kosdaq_top(n: int = 150) -> list:
    """코스닥 시총 상위 n개 (우선주·스팩 제외)."""
    df = fdr.StockListing("KOSDAQ")[["Code", "Name", "Marcap"]].dropna(subset=["Marcap"])
    df = df[df["Code"].str.endswith("0")]                    # 보통주만
    df = df[~df["Name"].str.contains("스팩", na=False)]
    df = df.sort_values("Marcap", ascending=False).head(n)
    return [(r.Code, str(r.Name).strip()) for r in df.itertuples()]


def main() -> None:
    ks = kospi200()
    kq = kosdaq_top(150)
    print(f"코스피200: {len(ks)}종목 · 코스닥 시총상위: {len(kq)}종목")

    # 관심 종목 중 위 두 목록에 이미 있으면 중복 제외
    have = {c for c, _ in ks} | {c for c, _ in kq}
    krx = pd.concat([fdr.StockListing(m)[["Code", "Name"]] for m in ("KOSPI", "KOSDAQ")])
    mkt = dict(zip(fdr.StockListing("KRX")["Code"], fdr.StockListing("KRX")["Market"]))
    watch = [(c, n) for c, n in WATCH.items() if c not in have]

    def sfx(code):
        m = str(mkt.get(code, "KOSPI"))
        return "KS" if m == "KOSPI" else "KQ"

    blocks = [("코스피 200", ks, "KS"), ("코스닥 150", kq, "KQ"), ("관심 종목", watch, None)]
    lines = ['# -*- coding: utf-8 -*-',
             '"""계절성용 한국 주요 종목 (build_kr_universe.py 가 생성 — 직접 수정하지 말 것).',
             '',
             '코스피 200 은 네이버 KPI200 실제 구성종목, 코스닥 150 은 공개 구성종목',
             '소스가 없어 코스닥 시가총액 상위 150개로 대체한 목록이다.',
             '"""',
             '', 'KR_GROUPS = [']
    for label, rows, fixed in blocks:
        if not rows:
            continue
        lines.append(f'    ("{label}", {{')
        for code, name in rows:
            s = fixed or sfx(code)
            lines.append(f'        "{code}.{s}": "{name}",')
        lines.append('    }),')
    lines.append(']')
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = len(ks) + len(kq) + len(watch)
    print(f"저장: {OUT} (총 {total}종목, {OUT.stat().st_size/1024:,.0f} KB)")


if __name__ == "__main__":
    main()
