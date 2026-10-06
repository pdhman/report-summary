# -*- coding: utf-8 -*-
"""CoinMetrics 커뮤니티 API 의 일별 기준가(PriceUSD)를 받아 backfill/*.csv 로 저장한다.

야후의 BTC-USD(2014-09-17~)·ETH-USD(2017-11-09~) 이전 구간을 fetch_data.py 의
BACKFILL 설정이 이 CSV 로 이어 붙인다(비트코인 2010-07-18~, 이더리움 2015-08-08~).
과거 기준가는 바뀌지 않으므로 한 번만 실행하면 되고, 매일 갱신하는 GitHub Actions
러너는 이 파일을 읽기만 한다(외부 API 의존 없음).

사용법:  python seasonality/build_crypto_backfill.py
"""
import csv
from pathlib import Path

import requests

OUT_DIR = Path(__file__).parent / "backfill"
API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
ASSETS = {"btc": "btc_coinmetrics.csv", "eth": "eth_coinmetrics.csv"}
CUTOFF = "2018-12-31"       # 이후는 야후가 담당 — 접합 검증용 여유만 남긴다


def fetch(asset: str) -> list[tuple[str, float]]:
    rows, url, params = [], API, {
        "assets": asset, "metrics": "PriceUSD", "frequency": "1d",
        "start_time": "2009-01-01", "end_time": CUTOFF, "page_size": 10000,
    }
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        for x in j.get("data", []):
            if x.get("PriceUSD"):
                rows.append((x["time"][:10], float(x["PriceUSD"])))
        url, params = j.get("next_page_url"), None
    return rows


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for asset, name in ASSETS.items():
        rows = fetch(asset)
        with open(OUT_DIR / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            w.writerows((d, f"{c:.6g}") for d, c in rows)
        print(f"{asset}: {len(rows)}일 ({rows[0][0]} ~ {rows[-1][0]}) → {name}")


if __name__ == "__main__":
    main()
