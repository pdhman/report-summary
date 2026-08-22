# -*- coding: utf-8 -*-
"""
RS 스크리너 데이터 빌더 — 주간 퀀트데이터 CSV 기반.

입력: output/퀀트데이터_latest.csv (없으면 최신 날짜본, --csv 로 지정 가능)
  - 섹터·업종·테마 열 필수 (collect.py 가 네이버 분류를 CSV 에 포함)
  - 테마는 '; ' 구분 다중값

계산:
  - 종합 RS = 기간별 RS 백분위의 가중평균 (1M 20% · 3M 40% · 6M 20% · 12M 20%,
    결측 기간은 가중치 재배분, 3M 필수). 0~100 척도.
  - 그룹(섹터/업종/테마) 집계 = 종합 RS 평균, 기간별 RS 평균, 종목수,
    RS90+ 비중. 테마는 3종목 이상만.
  - 테마에서 SPAC 제외: '기업인수목적' 테마 + 종목명에 '스팩' 포함 종목

출력: ../docs/rs_data.js  (window.RS_DATA = {...})

실행:  python rs_build.py
       python rs_build.py --csv output/퀀트데이터_20260805_new.csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import sys

import pandas as pd

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")
CACHE_DIR = os.path.join(BASE, "cache")
ETF_OHLCV = os.path.join(CACHE_DIR, "etf_ohlcv.parquet")
ETF_LIST_JSON = os.path.join(CACHE_DIR, "etf_list.json")   # chart_build 가 읽는다
REPORTS_DIR = os.path.normpath(os.path.join(BASE, "..", "docs"))
OUT_JS = os.path.join(REPORTS_DIR, "rs_data.js")

CLASS_COLS = ("섹터", "업종", "테마")
THEME_MIN_MEMBERS = 3

# 네이버 ETF 목록 API 의 etfTabCode → 유형명
ETF_TABS = {1: "국내 시장지수", 2: "국내 업종·테마", 3: "국내 파생",
            4: "해외 주식", 5: "원자재", 6: "채권", 7: "기타/혼합"}
ETF_LOOKBACK_DAYS = 420
ETF_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 종합 RS 가중치 (IBD 방식 변형: 최근 3개월 2배)
#
# 가중 대상은 '기간별 RS 백분위'이지 원시 수익률이 아니다. 원시 수익률을
# 가중평균하면 구간별 스케일 차이 때문에 장기 구간이 결과를 지배한다
# (예: 1개월 수익률은 ±30% 범위인데 1년 수익률은 +900%까지 나온다 →
#  1년 항이 1개월 항보다 30배 커서 가중치를 어떻게 줘도 뒤집히지 않는다).
# 그 결과 "1년 급등 후 고점에서 40% 빠진" 종목이 RS 99로 남는 문제가 있었다.
# 백분위를 먼저 매기면 모든 구간이 0~100 같은 척도가 된다(IBD 방식).
RS_WEIGHTS = {"RS_1개월": 0.2, "RS_3개월": 0.4,
              "RS_6개월": 0.2, "RS_1년": 0.2}
RS_REQUIRED = "RS_3개월"      # 이 값이 없으면 종합 RS 를 내지 않는다


def _log(msg: str):
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


# ------------------------------------------------------------------ 데이터 로드
def load_quant_csv(path: str | None = None) -> tuple[pd.DataFrame, str]:
    if not path:
        latest = os.path.join(OUT_DIR, "퀀트데이터_latest.csv")
        dated = sorted(glob.glob(os.path.join(OUT_DIR, "퀀트데이터_2*.csv")))
        path = latest if os.path.exists(latest) else (dated[-1] if dated else None)
        if not path:
            raise FileNotFoundError("output/퀀트데이터_*.csv 가 없습니다. collect.py 먼저 실행하세요.")
    df = pd.read_csv(path, dtype={"코드": str})
    df["코드"] = df["코드"].str.zfill(6)

    missing = [c for c in (*CLASS_COLS, *RS_WEIGHTS) if c not in df.columns]
    if missing:
        raise ValueError(f"{os.path.basename(path)} 에 필요한 열이 없습니다: {missing} "
                         "(collect.py 를 최신 버전으로 실행하세요)")

    m = re.search(r"_(\d{8})", os.path.basename(path))
    asof = (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m
            else dt.date.fromtimestamp(os.path.getmtime(path)).isoformat())
    _log(f"퀀트데이터 로드: {len(df)}종목 (기준 {asof}, {os.path.basename(path)})")
    return df, asof


def composite_rs(df: pd.DataFrame) -> pd.Series:
    """기간별 RS 백분위의 가중평균 (0~100). 3개월 RS 없는 종목은 제외.

    이미 0~100 척도라 결과에 다시 rank 를 매기지 않는다 — 그래야 "RS 80"이
    '상위 20%'가 아니라 '기간별 상대강도의 가중평균'이라는 뜻으로 안정적이다.
    """
    cols = list(RS_WEIGHTS)
    pct = df[cols].apply(pd.to_numeric, errors="coerce")
    w = pd.Series(RS_WEIGHTS)
    wsum = pct.notna().mul(w, axis=1).sum(axis=1)
    score = pct.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, math.nan)
    score[pct[RS_REQUIRED].isna()] = math.nan
    return score.round(1)


# ------------------------------------------------------------------ 집계·출력
def _r(v):
    """NaN → None, 그 외 float 1자리."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 1)


def group_stats(sub: pd.DataFrame) -> dict | None:
    sub = sub[sub["RS"].notna()]
    if len(sub) == 0:
        return None
    return {
        "rs": _r(sub["RS"].mean()),
        "r1": _r(sub["RS_1개월"].mean()), "r3": _r(sub["RS_3개월"].mean()),
        "r6": _r(sub["RS_6개월"].mean()), "r12": _r(sub["RS_1년"].mean()),
        "n": int(len(sub)),
        "p90": _r((sub["RS"] >= 90).mean() * 100),
    }


# ------------------------------------------------------------------ ETF
def fetch_etf_list() -> pd.DataFrame:
    """네이버 ETF 목록 (전 종목 1회 호출). 코드·이름·유형·시총(억)·거래대금(억)."""
    import requests
    r = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn?etfType=0",
                     headers=ETF_UA, timeout=15)
    r.raise_for_status()
    items = (r.json().get("result") or {}).get("etfItemList") or []
    if not items:
        raise RuntimeError("네이버 ETF 목록이 비어 있음")
    df = pd.DataFrame([{
        "코드": str(x["itemcode"]).zfill(6),
        "이름": str(x["itemname"]).strip(),
        "유형": ETF_TABS.get(x.get("etfTabCode"), "기타/혼합"),
        "시가총액(억)": pd.to_numeric(x.get("marketSum"), errors="coerce"),
        # amonut(sic)는 백만원 단위 거래대금
        "거래대금(억)": pd.to_numeric(x.get("amonut"), errors="coerce") / 100.0,
    } for x in items]).drop_duplicates(subset="코드")
    _log(f"ETF 목록: {len(df)}종목")
    return df


def update_etf_cache(codes: list[str]) -> pd.DataFrame:
    """ETF 일봉 종가 캐시 — 전 구간 재수신(소급조정 대응), 실패 종목만 캐시 유지."""
    import collect                      # 같은 폴더: 일봉 수신 유틸 재사용
    from concurrent.futures import ThreadPoolExecutor, as_completed

    start = (dt.date.today() - dt.timedelta(days=ETF_LOOKBACK_DAYS)).isoformat()
    cached = None
    if os.path.exists(ETF_OHLCV):
        cached = pd.read_parquet(ETF_OHLCV)
        cached["date"] = pd.to_datetime(cached["date"])
        cached = cached[cached["code"].isin(set(codes))]

    _log(f"ETF 일봉 수신 {len(codes)}종목 ...")
    frames, fail, done = [], 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(collect._fetch_close, c, start): c for c in codes}
        for fut in as_completed(futs):
            df = fut.result()
            if df is not None:
                frames.append(df)
            else:
                fail += 1
            done += 1
            if done % 300 == 0:
                _log(f"  ETF 일봉 진행 {done}/{len(codes)}")

    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not new.empty:
        new["date"] = pd.to_datetime(new["date"])
        if cached is not None:
            cached = cached[~cached["code"].isin(set(new["code"]))]
    merged = pd.concat([cached, new], ignore_index=True) if cached is not None else new
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged[merged["date"] >= pd.Timestamp(start)]
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    merged.to_parquet(ETF_OHLCV, index=False)
    _log(f"ETF 일봉 캐시: {merged['code'].nunique()}종목 (수신실패 {fail})")
    return merged


def build_etf_section() -> tuple[list, list]:
    """ETF 유형 랭킹·종목 배열 생성. RS 백분위는 ETF 풀 안에서만 매긴다.

    종목 배열은 주식 배열과 같은 15개 필드 뒤에 레버리지/인버스 플래그(0/1)를
    붙인 16필드 — 프런트가 주식/ETF 를 같은 코드로 렌더링할 수 있게 한다.
    (섹터 인덱스 자리에 유형 인덱스, 업종 -1, 테마 [])
    """
    import collect

    uni = fetch_etf_list()
    ohlcv = update_etf_cache(uni["코드"].tolist())
    if ohlcv is None or ohlcv.empty:
        raise RuntimeError("ETF 일봉 데이터 없음")

    mom = collect.momentum_factors(ohlcv)     # 코퍼레이트액션 보정 + ETF 풀 내 RS 백분위
    mom["코드"] = mom["코드"].astype(str).str.zfill(6)
    df = uni.merge(mom, on="코드", how="left")
    df["RS"] = composite_rs(df)
    df["레버"] = (df["이름"].str.contains("레버리지") |
                  df["이름"].str.contains("인버스")).astype(int)

    cats = []
    for name, sub in df.groupby("유형"):
        st = group_stats(sub)
        if st:
            cats.append({"name": name, **st})
    cats.sort(key=lambda x: -(x["rs"] or 0))
    cat_idx = {c["name"]: i for i, c in enumerate(cats)}

    out = []
    view = df[df["RS"].notna()].sort_values("RS", ascending=False)
    for _, row in view.iterrows():
        out.append([
            row["코드"], row["이름"], 0,
            None if pd.isna(row["시가총액(억)"]) else round(float(row["시가총액(억)"])),
            None if pd.isna(row["거래대금(억)"]) else round(float(row["거래대금(억)"]), 1),
            _r(row["RS"]), _r(row["RS_1개월"]), _r(row["RS_3개월"]),
            _r(row["RS_6개월"]), _r(row["RS_1년"]),
            _r(row["52주고점대비(%)"]), _r(row["MA200대비(%)"]),
            cat_idx.get(row["유형"], -1), -1, [],
            int(row["레버"]),
        ])

    with open(ETF_LIST_JSON, "w", encoding="utf-8") as f:
        json.dump([{"code": c, "name": n} for c, n in zip(uni["코드"], uni["이름"])],
                  f, ensure_ascii=False)
    _log(f"ETF 집계: {len(out)}종목 · 유형 {len(cats)}개 (레버리지·인버스 {int(df['레버'].sum())})")
    return cats, out


def build(csv_path: str | None = None) -> str:
    df, asof = load_quant_csv(csv_path)
    df["RS"] = composite_rs(df)

    # ---- 섹터/업종 집계 (CSV 열 기준)
    sectors, industries, themes = [], [], []
    for name, sub in df[df["섹터"].notna()].groupby("섹터"):
        st = group_stats(sub)
        if st:
            sectors.append({"name": str(name), **st})
    for name, sub in df[df["업종"].notna()].groupby("업종"):
        st = group_stats(sub)
        if st:
            industries.append({"name": str(name), **st})

    # ---- 테마 멤버십 (테마 열 '; ' 분리, SPAC 제외)
    spac_codes = set(df.loc[df["회사명"].str.contains("스팩", na=False), "코드"])
    theme_codes: dict[str, list[str]] = {}
    for code, name, raw in zip(df["코드"], df["회사명"], df["테마"]):
        if code in spac_codes or not isinstance(raw, str) or not raw.strip():
            continue
        for t in raw.split(";"):
            t = t.strip()
            if not t or "기업인수목적" in t or "SPAC" in t.upper():
                continue
            theme_codes.setdefault(t, []).append(code)

    idx = df.set_index("코드")
    theme_members = [(t, cs) for t, cs in theme_codes.items()
                     if len(cs) >= THEME_MIN_MEMBERS]
    for name, members in theme_members:
        st = group_stats(idx.loc[members])
        if st:
            themes.append({"name": name, **st})

    sectors.sort(key=lambda x: -(x["rs"] or 0))
    industries.sort(key=lambda x: -(x["rs"] or 0))
    themes.sort(key=lambda x: -(x["rs"] or 0))

    sec_idx = {s["name"]: i for i, s in enumerate(sectors)}
    ind_idx = {s["name"]: i for i, s in enumerate(industries)}
    thm_idx = {s["name"]: i for i, s in enumerate(themes)}

    themes_of: dict[str, list[int]] = {}
    for name, members in theme_members:
        ti = thm_idx.get(name)
        if ti is None:
            continue
        for c in members:
            themes_of.setdefault(c, []).append(ti)

    # ---- 종목 배열 (RS 내림차순, RS 없는 종목 제외)
    out_stocks = []
    view = df[df["RS"].notna()].sort_values("RS", ascending=False)
    for _, row in view.iterrows():
        code = row["코드"]
        out_stocks.append([
            code, row["회사명"], 0 if row["시장"] == "KOSPI" else 1,
            None if pd.isna(row["시가총액(억)"]) else round(float(row["시가총액(억)"])),
            None if pd.isna(row["거래대금(억)"]) else round(float(row["거래대금(억)"]), 1),
            _r(row["RS"]), _r(row["RS_1개월"]), _r(row["RS_3개월"]),
            _r(row["RS_6개월"]), _r(row["RS_1년"]),
            _r(row["52주고점대비(%)"]), _r(row["MA200대비(%)"]),
            sec_idx.get(row["섹터"], -1),
            ind_idx.get(row["업종"], -1),
            themes_of.get(code, []),
        ])

    # ETF 섹션 — 실패해도 주식 데이터 생성은 계속한다 (주간 자동화 보호).
    # 실패 시 기존 rs_data.js 의 ETF 데이터를 재사용해 화면 공백을 막는다.
    etf_cats, etfs = [], []
    try:
        etf_cats, etfs = build_etf_section()
    except Exception as ex:
        _log(f"경고: ETF 수집 실패 — 기존 데이터 유지 시도: {ex}")
        try:
            with open(OUT_JS, encoding="utf-8") as f:
                prev = json.loads(f.read().split("=", 1)[1].rstrip().rstrip(";"))
            etf_cats, etfs = prev.get("etfCats", []), prev.get("etfs", [])
        except Exception:
            pass

    payload = {
        "asof": asof,
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sectors": sectors, "industries": industries, "themes": themes,
        "stocks": out_stocks,
        "etfCats": etf_cats, "etfs": etfs,
    }
    js = "window.RS_DATA = " + json.dumps(payload, ensure_ascii=False,
                                          separators=(",", ":")) + ";\n"
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    _log(f"완료: 종목 {len(out_stocks)} · 섹터 {len(sectors)} · 업종 {len(industries)}"
         f" · 테마 {len(themes)} · ETF {len(etfs)}(유형 {len(etf_cats)})"
         f" → {OUT_JS} ({os.path.getsize(OUT_JS) / 1024:.0f}KB)")
    return OUT_JS


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RS 스크리너 데이터 빌드")
    ap.add_argument("--csv", help="퀀트데이터 CSV 경로 (기본: latest → 최신 날짜본)")
    args = ap.parse_args()
    build(csv_path=args.csv)
