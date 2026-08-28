# -*- coding: utf-8 -*-
"""
팩터랩 외국인 보유율 백필 — 네이버 siseJson API.

종목당 1회 요청으로 일별 '외국인소진율'(외국인 보유/한도, 대부분 지분율과
동일) 전체 이력을 받는다. 외국인 수급 팩터(보유율 N일 변화)의 원천.

- 유니버스: cache/universe.csv (fetch_prices.py 가 저장)
- 종목당 cache/flows/<code>.parquet (date, frgn) — 3일 이내 파일은 건너뜀
- 합본: cache/flows_panel.parquet

실행:  python fetch_flows.py
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, "cache")
FLOW_DIR = os.path.join(CACHE_DIR, "flows")
PANEL_PATH = os.path.join(CACHE_DIR, "flows_panel.parquet")
UNI_PATH = os.path.join(CACHE_DIR, "universe.csv")

WORKERS = 4
SLEEP = 0.1
FRESH_DAYS = 3
START = "20150801"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _log(msg: str):
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def _fetch_one(code: str) -> pd.DataFrame | None:
    end = dt.date.today().strftime("%Y%m%d")
    # 2026-08-28: finance.naver 9/10 종료 대비 — 신형 차트 API 로 전환
    # (foreignRetentionRate = 기존 외국인소진율과 동일 값 확인)
    url = (f"https://api.stock.naver.com/chart/domestic/item/{code}/day"
           f"?startDateTime={START}00&endDateTime={end}23")
    for attempt in range(3):
        try:
            time.sleep(SLEEP)
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code != 200 or not r.text.strip():
                return None
            rows = r.json()                             # 신형 API 는 표준 JSON
            if not rows:
                return None
            df = pd.DataFrame(rows)
            out = pd.DataFrame({
                "date": pd.to_datetime(df["localDate"].astype(str),
                                       format="%Y%m%d", errors="coerce"),
                "frgn": pd.to_numeric(df["foreignRetentionRate"], errors="coerce"),
            }).dropna(subset=["date"])
            return out if not out.empty else None
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5 * (attempt + 1))


def main():
    uni = pd.read_csv(UNI_PATH, dtype={"Code": str})
    os.makedirs(FLOW_DIR, exist_ok=True)
    now = time.time()
    todo = []
    for code in uni["Code"]:
        p = os.path.join(FLOW_DIR, f"{code}.parquet")
        if os.path.exists(p) and (now - os.path.getmtime(p)) / 86400 < FRESH_DAYS:
            continue
        todo.append(code)
    _log(f"외국인 보유율 수신: {len(todo)}종목 (건너뜀 {len(uni) - len(todo)})")

    done, fail = 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_fetch_one, c): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            df = fut.result()
            if df is not None:
                df.to_parquet(os.path.join(FLOW_DIR, f"{code}.parquet"), index=False)
            else:
                fail += 1
            done += 1
            if done % 200 == 0:
                rate = done / (time.time() - t0)
                eta = (len(todo) - done) / rate / 60 if rate > 0 else 0
                _log(f"  진행 {done}/{len(todo)} (실패 {fail}, 남은 예상 {eta:.0f}분)")
    _log(f"수신 완료 (실패 {fail})")

    frames = []
    for code in uni["Code"]:
        p = os.path.join(FLOW_DIR, f"{code}.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        if df.empty:
            continue
        df.insert(1, "code", code)
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["code", "date"])
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    panel.to_parquet(PANEL_PATH, index=False)
    _log(f"패널 저장: {panel['code'].nunique()}종목 / {len(panel):,}행 → {PANEL_PATH}")


if __name__ == "__main__":
    main()
