# -*- coding: utf-8 -*-
"""
시장 건전성(Market Breadth) 일일 빌더 — 알파노트 '시장 건전성' 페이지 데이터 생성.

소스:
  - cache/ohlcv_full.parquet (chart_build.py 가 평일 16:20 갱신하는 전종목 일봉)
      → 등락종목수(A/D)·52주 신고/신저·이평선 위 비율·상승/하락 거래대금·
        저가주 비중·TOP10 집중도·상하한가 수·회전율
  - output/퀀트데이터_latest.csv                : 유니버스(시장 구분)·상장주식수 근사
  - 네이버 siseJson (KOSPI/KOSDAQ 지수)         : 지수 종가·실현변동성·상대강도
  - 인베스팅닷컴 VKOSPI (Playwright 스크레이핑) : 변동성지수 — 실패해도 치명적 아님
  - market_leverage_collector/data/*.csv        : 신용융자·예탁금·반대매매비중 요약
  - 수급모니터링/data/*.csv                     : 투자자별 수급 요약 타일

출력:
  - reports/data/market_history.csv : 일일 지표 마스터(누적 병합, git 추적 → 백업 겸용)
      * 누적선(A/D라인·McClellan 등)은 저장하지 않고 매번 병합 히스토리에서 재계산
        — parquet 롤링 윈도우(550일)가 지나가도 연속성이 유지된다.
  - reports/market_data.js          : window.MARKET_DATA = {...} (market.html 이 읽음)
  - reports/data/market_summary.json: make_summary.py 홈 카드용 최신 요약

주의:
  - 거래대금은 종가×거래량 근사(네이버 일봉에 거래대금 없음). 비중·집중도 지표라
    근사로도 왜곡이 작다.
  - 52주 신고/신저·MA200 비율은 종목별 250/200봉 이상 쌓인 날부터 계산된다
    (초기 백필 구간은 공란 → 매일 실행하며 앞으로 채워진다).

실행:  python breadth_build.py               # 전체 (VKOSPI 스크레이핑 포함)
       python breadth_build.py --no-vkospi   # 스크레이핑 생략
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys

import numpy as np
import pandas as pd
import requests

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(BASE)
OHLCV_PATH = os.path.join(BASE, "cache", "ohlcv_full.parquet")
UNI_PATH = os.path.join(BASE, "output", "퀀트데이터_latest.csv")
LOG_DIR = os.path.join(BASE, "logs")

REPORTS = os.path.join(PROJ, "reports")
HIST_PATH = os.path.join(REPORTS, "data", "market_history.csv")
JS_PATH = os.path.join(REPORTS, "market_data.js")
SUMMARY_PATH = os.path.join(REPORTS, "data", "market_summary.json")

LEV_DIR = os.path.join(PROJ, "market_leverage_collector", "data")
FLOW_DIR = os.path.join(PROJ, "수급모니터링", "data")

PENNY_KRW = 2000            # 저가주 기준(종가, 원)
CAP_PCT = 29.5              # 상·하한가 판정 등락률(%)
NH_WIN, MA_LONG = 250, 200  # 52주 신고/신저 윈도우, 장기 이평
CHART_ROWS = 280            # market_data.js 에 싣는 최근 거래일 수
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

log = logging.getLogger("breadth")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"breadth_{dt.datetime.now():%Y%m%d_%H%M%S}.log")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    return path


# ------------------------------------------------------------------ 유니버스/일봉
def load_universe() -> pd.DataFrame:
    df = pd.read_csv(UNI_PATH, dtype={"코드": str},
                     usecols=["코드", "회사명", "시장", "주가", "시가총액(억)"])
    df["코드"] = df["코드"].str.zfill(6)
    df = df[~df["회사명"].str.contains("스팩", na=False)]
    df = df[df["주가"] > 0]
    # 상장주식수 근사(주간 스냅샷 기준) — 일일 시총 = 주식수 × 당일 종가
    df["주식수"] = df["시가총액(억)"] * 1e8 / df["주가"]
    return df.set_index("코드")


def load_ohlcv(uni: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(OHLCV_PATH, columns=["date", "code", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["code"].isin(set(uni.index))]
    return df


# ------------------------------------------------------------------ 폭 지표 계산
def compute_breadth(ohlcv: pd.DataFrame, uni: pd.DataFrame) -> pd.DataFrame:
    """parquet 윈도우 안에서 계산 가능한 '일일 원시값'만 반환(누적선 없음)."""
    close = ohlcv.pivot_table(index="date", columns="code", values="close")
    volume = ohlcv.pivot_table(index="date", columns="code", values="volume")
    close = close.sort_index()
    volume = volume.reindex(close.index)
    amt = close * volume                      # 거래대금 근사(원)
    chg = close.pct_change() * 100

    shares = uni["주식수"].reindex(close.columns)
    mcap = close.mul(shares, axis=1)          # 일일 시총 근사(원)

    groups = {"all": close.columns,
              "kospi": uni.index[uni["시장"] == "KOSPI"].intersection(close.columns),
              "kosdaq": uni.index[uni["시장"] == "KOSDAQ"].intersection(close.columns)}

    out = pd.DataFrame(index=close.index)
    for key, cols in groups.items():
        c, v, a, ch = close[cols], volume[cols], amt[cols], chg[cols]
        traded = ch.notna()                   # 전일 종가가 있는 종목만 등락 판정

        out[f"{key}_adv"] = ((ch > 0) & traded).sum(axis=1)
        out[f"{key}_dec"] = ((ch < 0) & traded).sum(axis=1)
        out[f"{key}_unch"] = ((ch == 0) & traded).sum(axis=1)

        # 52주 신고/신저 (250봉 이상 쌓인 종목만)
        hi = c.rolling(NH_WIN, min_periods=NH_WIN).max()
        lo = c.rolling(NH_WIN, min_periods=NH_WIN).min()
        out[f"{key}_nh"] = (c >= hi).sum(axis=1)
        out[f"{key}_nl"] = (c <= lo).sum(axis=1)
        nhl_base = hi.notna().sum(axis=1)
        out[f"{key}_nhl_n"] = nhl_base        # 신고/신저 판정 모수

        # 이평선 위 비율(%)
        for w in (20, 50, MA_LONG):
            ma = c.rolling(w, min_periods=w).mean()
            valid = ma.notna().sum(axis=1)
            above = (c > ma).sum(axis=1)
            out[f"{key}_ma{w}"] = np.where(valid > 0, above / valid * 100, np.nan)

        # 상승/하락 거래대금(조원) — 90% 데이 판정용
        upv = a.where(ch > 0).sum(axis=1) / 1e12
        dnv = a.where(ch < 0).sum(axis=1) / 1e12
        out[f"{key}_upvol"] = upv
        out[f"{key}_dnvol"] = dnv

        # 거래대금 합(조원)·회전율(%)
        tot_amt = a.sum(axis=1)
        out[f"{key}_amt"] = tot_amt / 1e12
        tot_mcap = mcap[cols].sum(axis=1)
        out[f"{key}_turnover"] = np.where(tot_mcap > 0, tot_amt / tot_mcap * 100, np.nan)

    # ---- 전체 시장 기준 투기/집중 지표 ----
    rank = amt.rank(axis=1, ascending=False)
    tot = amt.sum(axis=1)
    out["top10_share"] = amt.where(rank <= 10).sum(axis=1) / tot * 100
    penny = close < PENNY_KRW
    out["penny_share"] = amt.where(penny).sum(axis=1) / tot * 100
    out["top50_penny"] = (penny & (rank <= 50)).sum(axis=1)
    out["cap_up"] = (chg >= CAP_PCT).sum(axis=1)
    out["cap_down"] = (chg <= -CAP_PCT).sum(axis=1)

    # 첫 행(전일 없음)은 등락 판정 불가 → 제거
    out = out.iloc[1:]
    out.index.name = "date"
    return out


# ------------------------------------------------------------------ 지수(네이버)
def fetch_index(symbol: str, start: dt.date) -> pd.Series:
    url = ("https://api.finance.naver.com/siseJson.naver"
           f"?symbol={symbol}&requestType=1&startTime={start:%Y%m%d}"
           f"&endTime={dt.date.today():%Y%m%d}&timeframe=day")
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    rows = re.findall(r'\["(\d{8})",\s*([\d.]+),\s*[\d.]+,\s*[\d.]+,\s*([\d.]+)', r.text)
    if not rows:
        raise RuntimeError(f"{symbol} 지수 파싱 실패")
    s = pd.Series({pd.Timestamp(d): float(c) for d, _, c in rows}, name=symbol)
    return s.sort_index()


def fetch_usdkrw(start: dt.date) -> pd.Series:
    """원달러 환율 (FDR) — 공포탐욕지수의 안전자산 수요 요소."""
    import FinanceDataReader as fdr
    df = fdr.DataReader("USD/KRW", start.isoformat())
    return df["Close"].rename("usdkrw")


# ------------------------------------------------------------------ VKOSPI 스크레이핑
def fetch_vkospi() -> pd.Series:
    """인베스팅닷컴 히스토리 테이블(최근 1개월) — 놓친 날짜도 자동 보정된다."""
    from playwright.sync_api import sync_playwright
    url = "https://kr.investing.com/indices/kospi-volatility-historical-data"
    vals = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA["User-Agent"], locale="ko-KR")
        page = ctx.new_page()
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        for tr in page.query_selector_all("table tbody tr"):
            tds = [td.inner_text().strip() for td in tr.query_selector_all("td")]
            if len(tds) < 2:
                continue
            m = re.search(r"(\d{4})[.\s년-]+(\d{1,2})[.\s월-]+(\d{1,2})", tds[0])
            v = re.match(r"^[\d,]+\.?\d*$", tds[1].replace(",", ""))
            if not m or not v:
                continue
            try:
                d = pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                vals[d] = float(tds[1].replace(",", ""))
            except ValueError:
                continue
        browser.close()
    if not vals:
        raise RuntimeError("VKOSPI 테이블 파싱 실패")
    return pd.Series(vals, name="vkospi").sort_index()


# ------------------------------------------------------------------ 히스토리 병합
def merge_history(fresh: pd.DataFrame) -> pd.DataFrame:
    """기존 마스터 CSV 와 병합.

    폭 지표 열은 겹치는 날짜에서 새 계산값 우선, 옛 날짜 행은 그대로 보존.
    fresh 에 없는 열(vkospi·kospi_close 등 수집 시계열)은 겹치는 날짜에서도
    기존 값을 유지한다 — 스크레이핑 윈도우(약 1개월) 밖 과거값 보호.
    """
    if not os.path.exists(HIST_PATH):
        return fresh.sort_index()
    old = pd.read_csv(HIST_PATH, parse_dates=["date"], index_col="date")
    extra = [c for c in old.columns if c not in fresh.columns]
    fresh = fresh.join(old[extra], how="left") if extra else fresh
    return pd.concat([old[~old.index.isin(fresh.index)], fresh]).sort_index()


def apply_series(hist: pd.DataFrame, s: pd.Series, col: str) -> pd.DataFrame:
    """지수/VKOSPI 시계열을 히스토리에 갱신 병합(새 값 우선, 과거값 보존)."""
    hist = hist.copy()
    if col not in hist.columns:
        hist[col] = np.nan
    aligned = s[s.index.isin(hist.index)]
    hist.loc[aligned.index, col] = aligned.values
    return hist


# ------------------------------------------------------------------ 파생(누적·EMA)
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def derive(hist: pd.DataFrame) -> pd.DataFrame:
    """병합된 전체 히스토리에서 누적선·EMA 파생 지표 계산."""
    d = hist.copy()
    for key in ("all", "kospi", "kosdaq"):
        adv, dec = d[f"{key}_adv"], d[f"{key}_dec"]
        net = adv - dec
        d[f"{key}_ad"] = net.cumsum()
        tot = (adv + dec).replace(0, np.nan)
        rana = 1000 * net / tot                      # ratio-adjusted net advances
        d[f"{key}_mco"] = ema(rana, 19) - ema(rana, 39)
        d[f"{key}_mcs"] = d[f"{key}_mco"].cumsum()   # Summation Index
        d[f"{key}_adr20"] = (adv.rolling(20).sum()
                             / dec.rolling(20).sum().replace(0, np.nan) * 100)
        d[f"{key}_zbt"] = ema(adv / tot, 10)         # Zweig Breadth Thrust
        d[f"{key}_nhnl"] = (d[f"{key}_nh"] - d[f"{key}_nl"]).fillna(0).cumsum()
        upv, dnv = d[f"{key}_upvol"], d[f"{key}_dnvol"]
        volsum = (upv + dnv).replace(0, np.nan)
        d[f"{key}_upshare"] = upv / volsum * 100
    if "kospi_close" in d.columns and "kosdaq_close" in d.columns:
        d["kq_rel20"] = (d["kosdaq_close"].pct_change(20)
                         - d["kospi_close"].pct_change(20)) * 100
        ret = np.log(d["kospi_close"]).diff()
        d["kospi_rv20"] = ret.rolling(20).std() * np.sqrt(252) * 100
    return d


# ------------------------------------------------------------------ 레버리지/수급
def load_leverage() -> dict | None:
    try:
        cr = pd.read_csv(os.path.join(LEV_DIR, "credit_balance.csv"),
                         encoding="utf-8-sig")
        mf = pd.read_csv(os.path.join(LEV_DIR, "market_funds.csv"),
                         encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    for df in (cr, mf):
        df["date"] = pd.to_datetime(df["구 분"], format="%Y/%m/%d")
        df.sort_values("date", inplace=True)
    credit = pd.to_numeric(cr["신용거래융자_전체"], errors="coerce") / 1e6   # 조원
    deposit = pd.to_numeric(
        mf["투자자예탁금 (장내파생상품 거래예수금제외)"], errors="coerce") / 1e6
    ratio = pd.to_numeric(mf["미수금 대비 반대매매비중(%)"], errors="coerce")
    lev = pd.DataFrame({"date": cr["date"], "credit": credit.values}).merge(
        pd.DataFrame({"date": mf["date"], "deposit": deposit.values,
                      "liq_ratio": ratio.values}), on="date", how="outer"
    ).sort_values("date").dropna(subset=["credit", "deposit"], how="all")
    lev["cd_ratio"] = lev["credit"] / lev["deposit"] * 100
    lev = lev.tail(500)
    return {
        "dates": [f"{d:%Y-%m-%d}" for d in lev["date"]],
        "credit": _round(lev["credit"], 2),
        "deposit": _round(lev["deposit"], 2),
        "liq_ratio": _round(lev["liq_ratio"], 2),
        "cd_ratio": _round(lev["cd_ratio"], 2),
    }


def load_flows() -> dict | None:
    out = {}
    for name in ("kospi", "kosdaq", "futures"):
        path = os.path.join(FLOW_DIR, f"{name}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        if df.empty:
            continue
        tail = df.tail(5)
        out[name] = {
            "date": str(df.iloc[-1]["date"]),
            "indiv": int(df.iloc[-1]["individual"]),
            "forgn": int(df.iloc[-1]["foreign"]),
            "inst": int(df.iloc[-1]["inst_total"]),
            "indiv5": int(tail["individual"].sum()),
            "forgn5": int(tail["foreign"].sum()),
            "inst5": int(tail["inst_total"].sum()),
        }
    return out or None


# ------------------------------------------------------------------ 스코어
def pct_rank(s: pd.Series) -> pd.Series:
    """전 히스토리 대비 백분위(0~100). NaN 은 그대로 둔다."""
    return s.rank(pct=True) * 100


def build_scores(d: pd.DataFrame, lev: dict | None) -> tuple[dict, pd.DataFrame]:
    comp = pd.DataFrame(index=d.index)

    def mean_rank(cols_or_series: list) -> pd.Series:
        ranks = [pct_rank(s) for s in cols_or_series]
        return pd.concat(ranks, axis=1).mean(axis=1)

    nh, nl = d["all_nh"], d["all_nl"]
    nh_ratio = nh / (nh + nl).replace(0, np.nan) * 100
    comp["trend"] = mean_rank([d["all_ma200"], d["all_ma50"], d["all_adr20"],
                               nh_ratio, d["all_mco"]])
    # 투기 열기 = 개인 투기(저가주·상한가·회전율)만. TOP10 집중도·코스닥
    # 상대강도는 대형주 쏠림 장세에서 투기와 무관하게 스코어를 밀어올려 제외.
    comp["spec"] = mean_rank([d["penny_share"], pd.to_numeric(d["cap_up"]),
                              d["all_turnover"]])
    # 쏠림(별도 게이지): 높음 = 과열이 아니라 '취약성'이라 종합 체온에는 안 섞는다
    comp["conc"] = pct_rank(d["top10_share"])

    vol_parts = []
    if "kospi_rv20" in d.columns:
        vol_parts.append(d["kospi_rv20"])
    if "vkospi" in d.columns and d["vkospi"].notna().any():
        vol_parts.append(d["vkospi"].ffill(limit=5))
    comp["vol"] = mean_rank(vol_parts) if vol_parts else np.nan

    if lev:
        ld = pd.Series(pd.to_datetime(lev["dates"]))
        credit = pd.Series(lev["credit"], index=ld, dtype=float).reindex(d.index).ffill(limit=7)
        cd = pd.Series(lev["cd_ratio"], index=ld, dtype=float).reindex(d.index).ffill(limit=7)
        liq = pd.Series(lev["liq_ratio"], index=ld, dtype=float).reindex(d.index).ffill(limit=7)
        comp["lev"] = mean_rank([credit.pct_change(20), cd, liq.rolling(5).mean()])
    else:
        comp["lev"] = np.nan

    comp["overall"] = comp[["trend", "spec", "vol", "lev"]].mean(axis=1)   # conc 제외

    # ---- 공포탐욕지수 (CNN Fear & Greed 한국판 6요소) ----
    # 각 요소를 전 히스토리 백분위(0~100)로 정규화해 평균. 높음=탐욕, 낮음=공포.
    # 팩터랩 월간판과 같은 설계 — 미국 신용스프레드는 일일 파이프라인에서 제외,
    # 풋/콜은 KRX 데이터 접근 불가로 원조(CNN 7요소)에서 빠졌다.
    # 각 요소를 (이름, 백분위 시리즈, 원시값 시리즈, 원시값 단위)로 들고 있으면
    # 요소별 최신값을 화면(핵심 수치 행)에 보여줄 수 있다.
    fg_items = []   # (name, pct_series, raw_series, unit)
    ks = d["kospi_close"] if "kospi_close" in d.columns else None
    if ks is not None and ks.notna().sum() > 130:
        raw = (ks / ks.rolling(125).mean() - 1) * 100
        fg_items.append(("모멘텀", pct_rank(ks / ks.rolling(125).mean() - 1),
                         raw, "% vs125일"))                                  # ① 모멘텀
    nhl_n = d["all_nhl_n"].replace(0, np.nan)
    raw = ((d["all_nh"] - d["all_nl"]) / nhl_n * 100).rolling(10).mean()
    fg_items.append(("신고저폭", pct_rank(raw), raw, "%"))                   # ② 신고-신저 폭
    raw = d["all_upshare"].rolling(20).mean()
    fg_items.append(("상승대금", pct_rank(raw), raw, "%"))                   # ③ 상승 거래대금
    if "kospi_rv20" in d.columns:
        raw = (d["kospi_rv20"] / d["kospi_rv20"].rolling(50).mean() - 1) * 100
        fg_items.append(("변동성(역)", 100 - pct_rank(
            d["kospi_rv20"] / d["kospi_rv20"].rolling(50).mean() - 1),
            raw, "% vs50일"))                                                # ④ 변동성(역)
    if "usdkrw" in d.columns and d["usdkrw"].notna().any():
        raw = d["usdkrw"].ffill().pct_change(20) * 100
        fg_items.append(("환율(역)", 100 - pct_rank(d["usdkrw"].ffill().pct_change(20)),
                         raw, "%/20일"))                                     # ⑤ 환율(역)
    if lev:
        ld_fg = pd.Series(pd.to_datetime(lev["dates"]))
        cr = pd.Series(lev["credit"], index=ld_fg, dtype=float) \
            .reindex(d.index).ffill(limit=7)
        raw = cr.pct_change(20) * 100
        fg_items.append(("신용잔고", pct_rank(cr.pct_change(20)), raw, "%/20일"))  # ⑥ 신용잔고
    fgdf = pd.concat([p for _, p, _, _ in fg_items], axis=1)
    comp["fg"] = fgdf.mean(axis=1).where(fgdf.notna().sum(axis=1) >= 3)

    last = comp.dropna(subset=["overall"]).iloc[-1] if comp["overall"].notna().any() else None
    latest = {k: (round(float(last[k]), 1) if last is not None and pd.notna(last[k]) else None)
              for k in ("overall", "trend", "spec", "conc", "vol", "lev", "fg")}
    latest["n_days"] = int(comp["overall"].notna().sum())
    # 공포탐욕 요소별 최신값 (p=백분위 0~100, r=원시값, u=원시값 단위)
    if last is not None:
        idx = last.name
        detail = []
        for name, pct, raw, unit in fg_items:
            pv, rv = pct.asof(idx), raw.asof(idx)
            if pd.notna(pv):
                detail.append({"n": name, "p": round(float(pv)),
                               "r": None if pd.isna(rv) else round(float(rv), 1),
                               "u": unit})
        latest["fg_detail"] = detail
    return latest, comp


# ------------------------------------------------------------------ 매크로 핵심 수치
def fetch_macro(d: pd.DataFrame) -> dict:
    """요약 행에 표기할 매크로 수치 — NFCI·미국채 10년물·VIX(FRED)·원달러(수집분).

    FRED 는 이 네트워크에서 직접 접근이 막혀 aicyclemonitor 의 fetch_fred
    (allorigins 폴백 내장)를 재사용한다. 각 항목은 실패해도 None 으로 두고
    파이프라인은 계속 간다. 미국 지표는 전일(현지) 값이 최신이다.
    """
    macro = {}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(BASE), "aicyclemonitor"))
        from ai_cycle_monitor import fetch_fred
        specs = [("nfci", "NFCI", 2), ("us10y", "DGS10", 2), ("vix", "VIXCLS", 1)]
        for key, sid, nd in specs:
            try:
                s = fetch_fred(sid).dropna()
                macro[key] = {"v": round(float(s.iloc[-1]), nd),
                              "chg": (round(float(s.iloc[-1] - s.iloc[-2]), nd)
                                      if len(s) > 1 else None),
                              "date": f"{s.index[-1]:%m-%d}"}
            except Exception as e:
                log.warning("%s 수집 실패: %s", sid, e)
                macro[key] = None
    except Exception as e:
        log.warning("FRED 모듈 로드 실패: %s", e)
        macro.update({"nfci": None, "us10y": None, "vix": None})
    # VIX 폴백: 한국 사이클 모델이 매일 야후에서 받아 두는 캐시(global.csv)
    if not macro.get("vix"):
        try:
            kc = pd.read_csv(os.path.join(os.path.dirname(BASE),
                                          "aicyclemonitor", "kc_cache", "global.csv"),
                             parse_dates=["date"])
            vx = kc.set_index("date")["vix"].dropna()
            macro["vix"] = {"v": round(float(vx.iloc[-1]), 1),
                            "chg": (round(float(vx.iloc[-1] - vx.iloc[-2]), 1)
                                    if len(vx) > 1 else None),
                            "date": f"{vx.index[-1]:%m-%d}"}
            log.info("VIX 는 kc_cache 폴백 사용 (%s)", macro["vix"]["date"])
        except Exception as e:
            log.warning("VIX kc_cache 폴백도 실패: %s", e)
    if "usdkrw" in d.columns and d["usdkrw"].notna().any():
        fx = d["usdkrw"].dropna()
        macro["usdkrw"] = {"v": round(float(fx.iloc[-1]), 1),
                           "chg": (round(float(fx.iloc[-1] - fx.iloc[-2]), 1)
                                   if len(fx) > 1 else None),
                           "date": f"{fx.index[-1]:%m-%d}"}
    else:
        macro["usdkrw"] = None
    return macro


# ------------------------------------------------------------------ 출력
def _round(x, nd=1):
    """리스트/시리즈 → JSON 직렬화용(반올림, NaN→None)."""
    out = []
    for v in (x.tolist() if hasattr(x, "tolist") else x):
        out.append(None if v is None or (isinstance(v, float) and not np.isfinite(v))
                   else round(float(v), nd))
    return out


def write_outputs(d: pd.DataFrame, comp: pd.DataFrame, scores: dict,
                  lev: dict | None, flows: dict | None, macro: dict | None = None):
    recent = d.tail(CHART_ROWS)
    comp_recent = comp.reindex(recent.index)

    def series(prefix: str) -> dict:
        g = {}
        g["adv"] = _round(recent[f"{prefix}_adv"], 0)
        g["dec"] = _round(recent[f"{prefix}_dec"], 0)
        g["ad"] = _round(recent[f"{prefix}_ad"], 0)
        g["mco"] = _round(recent[f"{prefix}_mco"], 1)
        g["mcs"] = _round(recent[f"{prefix}_mcs"], 0)
        g["adr20"] = _round(recent[f"{prefix}_adr20"], 1)
        g["zbt"] = _round(recent[f"{prefix}_zbt"], 3)
        g["nh"] = _round(recent[f"{prefix}_nh"], 0)
        g["nl"] = _round(recent[f"{prefix}_nl"], 0)
        g["nhnl"] = _round(recent[f"{prefix}_nhnl"], 0)
        g["ma20"] = _round(recent[f"{prefix}_ma20"], 1)
        g["ma50"] = _round(recent[f"{prefix}_ma50"], 1)
        g["ma200"] = _round(recent[f"{prefix}_ma200"], 1)
        g["upshare"] = _round(recent[f"{prefix}_upshare"], 1)
        g["amt"] = _round(recent[f"{prefix}_amt"], 2)
        g["turnover"] = _round(recent[f"{prefix}_turnover"], 3)
        return g

    data = {
        "generated": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
        "asof": f"{recent.index[-1]:%Y-%m-%d}",
        "dates": [f"{ts:%Y-%m-%d}" for ts in recent.index],
        "scores": {**scores,
                   "series": {k: _round(comp_recent[k], 1)
                              for k in ("overall", "trend", "spec", "conc", "vol", "lev", "fg")}},
        "markets": {k: series(k) for k in ("all", "kospi", "kosdaq")},
        "spec": {
            "top10_share": _round(recent["top10_share"], 1),
            "penny_share": _round(recent["penny_share"], 2),
            "top50_penny": _round(recent["top50_penny"], 0),
            "cap_up": _round(recent["cap_up"], 0),
            "cap_down": _round(recent["cap_down"], 0),
        },
        "index": {
            "kospi": _round(recent.get("kospi_close", pd.Series(index=recent.index)), 2),
            "kosdaq": _round(recent.get("kosdaq_close", pd.Series(index=recent.index)), 2),
            "kq_rel20": _round(recent.get("kq_rel20", pd.Series(index=recent.index)), 2),
            "rv20": _round(recent.get("kospi_rv20", pd.Series(index=recent.index)), 1),
        },
        "vkospi": _round(recent.get("vkospi", pd.Series(index=recent.index)), 2),
        "leverage": lev,
        "flows": flows,
        "macro": macro or {},
        "meta": {
            "penny_krw": PENNY_KRW,
            "notes": "거래대금은 종가×거래량 근사. 신고/신저·이평 비율은 상장 250/200일 이상 종목 기준.",
        },
    }
    os.makedirs(os.path.dirname(JS_PATH), exist_ok=True)
    with open(JS_PATH, "w", encoding="utf-8") as f:
        f.write("window.MARKET_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    log.info("market_data.js 저장 (%d거래일, %.0fKB)",
             len(recent), os.path.getsize(JS_PATH) / 1024)

    # ---- 홈 카드 요약 ----
    last = recent.iloc[-1]
    summary = {
        "date": f"{recent.index[-1]:%Y-%m-%d}",
        "overall": scores.get("overall"),
        "trend": scores.get("trend"),
        "spec": scores.get("spec"),
        "conc": scores.get("conc"),
        "vol": scores.get("vol"),
        "lev": scores.get("lev"),
        "fg": scores.get("fg"),
        "ma200": None if pd.isna(last["all_ma200"]) else round(float(last["all_ma200"]), 1),
        "nh": None if pd.isna(last["all_nh"]) else int(last["all_nh"]),
        "nl": None if pd.isna(last["all_nl"]) else int(last["all_nl"]),
        "adv": int(last["all_adv"]), "dec": int(last["all_dec"]),
        "vkospi": (None if "vkospi" not in recent.columns or pd.isna(last.get("vkospi"))
                   else round(float(last["vkospi"]), 2)),
    }
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    log.info("market_summary.json 저장: %s", summary)


# ------------------------------------------------------------------ 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vkospi", action="store_true", help="VKOSPI 스크레이핑 생략")
    args = ap.parse_args()
    _setup_logging()

    uni = load_universe()
    ohlcv = load_ohlcv(uni)
    log.info("유니버스 %d종목(스팩 제외), 일봉 %s행", len(uni), f"{len(ohlcv):,}")

    fresh = compute_breadth(ohlcv, uni)
    log.info("폭 지표 계산: %s ~ %s (%d거래일)",
             fresh.index[0].date(), fresh.index[-1].date(), len(fresh))

    hist = merge_history(fresh)

    start = (hist.index[0] - pd.Timedelta(days=40)).date()
    for symbol, col in (("KOSPI", "kospi_close"), ("KOSDAQ", "kosdaq_close")):
        try:
            hist = apply_series(hist, fetch_index(symbol, start), col)
        except Exception as e:
            log.warning("%s 지수 수집 실패: %s", symbol, e)

    try:
        hist = apply_series(hist, fetch_usdkrw(start), "usdkrw")
    except Exception as e:
        log.warning("원달러 환율 수집 실패(기존 값 유지): %s", e)

    if not args.no_vkospi:
        try:
            vk = fetch_vkospi()
            hist = apply_series(hist, vk, "vkospi")
            log.info("VKOSPI %d일 수집 (최근 %s = %.2f)",
                     len(vk), vk.index[-1].date(), vk.iloc[-1])
        except Exception as e:
            log.warning("VKOSPI 수집 실패(기존 값 유지): %s", e)

    # 마스터 저장 — 파생(누적선) 열은 저장하지 않는다
    os.makedirs(os.path.dirname(HIST_PATH), exist_ok=True)
    hist.round(4).to_csv(HIST_PATH, encoding="utf-8")
    log.info("market_history.csv 저장: %d행 × %d열", len(hist), hist.shape[1])

    d = derive(hist)
    lev = load_leverage()
    flows = load_flows()
    if not lev:
        log.warning("레버리지 데이터 없음 — 관련 타일/스코어 생략")
    scores, comp = build_scores(d, lev)
    log.info("스코어: %s", scores)
    macro = fetch_macro(d)
    write_outputs(d, comp, scores, lev, flows, macro)
    log.info("완료")


if __name__ == "__main__":
    main()
