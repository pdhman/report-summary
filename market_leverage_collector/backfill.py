# -*- coding: utf-8 -*-
"""
FreeSIS 과거 데이터 백필 (일회성 + 필요 시 재사용).

화면 스크래핑(collector.py)은 기본 조회창(약 3개월)만 가져오지만, 화면이
내부적으로 호출하는 meta/getMetaDataList.do API 는 조회기간(tmpV45/46)을
받는다. 이 API 로 과거 구간을 받아 CSV·DB 에 병합한다.

컬럼 매핑은 화면 그리드와 동일한 순서(TMPV1=날짜, 이하 화면 열 순서)로,
2026-08-12 자 화면 수집값과 대조해 확인했다.

사용:  python backfill.py [시작일 YYYYMMDD, 기본 20250101]
"""
from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "market_leverage.db"

API = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
HEADERS = {"User-Agent": "Mozilla/5.0",
           "Referer": "https://freesis.kofia.or.kr/stat/FreeSIS.do"}

# TMPV1(날짜) 이후 열 이름 — 기존 CSV 컬럼과 동일한 순서
DATASETS = {
    "credit_balance": {
        "obj": "STATSCU0100000070BO",
        "cols": ["신용거래융자_전체", "신용거래융자_유가증권", "신용거래융자_코스닥",
                 "신용거래대주_전체", "신용거래대주_유가증권", "신용거래대주_코스닥",
                 "청약자금 대출", "예탁증권 담보융자"],
    },
    "market_funds": {
        "obj": "STATSCU0100000060BO",
        "cols": ["투자자예탁금 (장내파생상품 거래예수금제외)", "장내파생상품 거래 예수금",
                 "대고객 환매 조건부 채권(RP) 매도잔고", "위탁매매 미수금",
                 "위탁매매 미수금 대비 실제 반대매매금액", "미수금 대비 반대매매비중(%)"],
    },
}


def fetch_range(obj_nm: str, start: str, end: str) -> list[dict]:
    payload = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                            "tmpV45": start, "tmpV46": end, "OBJ_NM": obj_nm}}
    r = requests.post(API, json=payload, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json().get("ds1") or []


def fetch_all(obj_nm: str, start: str, end: str) -> pd.DataFrame:
    """반년 단위로 나눠 수집(응답 행수 제한을 자극하지 않도록)."""
    rows = []
    a = pd.Timestamp(start)
    fin = pd.Timestamp(end)
    while a <= fin:
        b = min(a + pd.DateOffset(months=6) - pd.Timedelta(days=1), fin)
        chunk = fetch_range(obj_nm, a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))
        rows.extend(chunk)
        print(f"  {a.date()} ~ {b.date()}: {len(chunk)}행")
        a = b + pd.Timedelta(days=1)
        time.sleep(0.5)
    return pd.DataFrame(rows)


def merge_csv(name: str, new_df: pd.DataFrame) -> pd.DataFrame:
    """기존 CSV(화면 수집분)를 우선하고 없는 날짜만 API 값으로 채운다."""
    path = DATA_DIR / f"{name}.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    both = pd.concat([old, new_df], ignore_index=True)
    both = both.drop_duplicates(subset=["구 분"], keep="first")
    both["_d"] = pd.to_datetime(both["구 분"], format="%Y/%m/%d", errors="coerce")
    both = (both.dropna(subset=["_d"]).sort_values("_d", ascending=False)
                .drop(columns="_d").reset_index(drop=True))
    both.to_csv(path, index=False, encoding="utf-8-sig")
    return both


def merge_db(name: str, df: pd.DataFrame) -> None:
    df = df.copy()
    df["수집시각"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        try:
            old = pd.read_sql(f'SELECT * FROM "{name}"', conn)
        except Exception:
            old = pd.DataFrame()
        both = pd.concat([old, df], ignore_index=True)
        both = both.drop_duplicates(subset=["구 분"], keep="first")
        both.to_sql(name, conn, if_exists="replace", index=False)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20250101"
    end = datetime.now().strftime("%Y%m%d")
    for name, spec in DATASETS.items():
        print(f"\n{name} 백필 ({start} ~ {end})...")
        raw = fetch_all(spec["obj"], start, end)
        if raw.empty:
            print("  응답 없음 — 건너뜀")
            continue
        cols = ["구 분"] + spec["cols"]
        raw = raw.rename(columns={f"TMPV{i+1}": c for i, c in enumerate(cols)})
        raw = raw[[c for c in cols if c in raw.columns]]
        raw["구 분"] = pd.to_datetime(raw["구 분"], format="%Y%m%d").dt.strftime("%Y/%m/%d")
        merged = merge_csv(name, raw)
        merge_db(name, raw)
        d = pd.to_datetime(merged["구 분"], format="%Y/%m/%d")
        print(f"  CSV {len(merged)}행 ({d.min().date()} ~ {d.max().date()})")


if __name__ == "__main__":
    main()
