# -*- coding: utf-8 -*-
"""
시장 건전성(Market Breadth) 일일 빌더 — 알파노트 '시장 건전성' 페이지 데이터 생성.

소스:
  - cache/ohlcv_full.parquet (chart_build.py 가 평일 16:20 갱신하는 전종목 일봉)
      → 등락종목수(A/D)·52주 신고/신저·이평선 위 비율·상승/하락 거래대금·
        저가주 비중·TOP10 집중도·상하한가 수·회전율
  - output/퀀트데이터_latest.csv                : 유니버스(시장 구분)·상장주식수 근사
  - 네이버 siseJson (KOSPI/KOSDAQ 지수)         : 지수 종가·실현변동성·상대강도
  - KRX Open API (krx_api.py)                    : VKOSPI·풋콜비율(코스피200옵션)
      → 키 없음/실패 시 VKOSPI 는 인베스팅닷컴 Playwright 스크레이핑으로 폴백
        (2026-09-02 KRX 전환. 인베스팅은 최근 1개월만, KRX 는 히스토리 전 구간 백필)
  - market_leverage_collector/data/*.csv        : 신용융자·예탁금·반대매매비중 요약
  - 수급모니터링/data/*.csv                     : 투자자별 수급 요약 타일

출력:
  - docs/data/market_history.csv : 일일 지표 마스터(누적 병합, git 추적 → 백업 겸용)
      * 누적선(A/D라인·McClellan 등)은 저장하지 않고 매번 병합 히스토리에서 재계산
        — parquet 롤링 윈도우(550일)가 지나가도 연속성이 유지된다.
  - docs/market_data.js          : window.MARKET_DATA = {...} (market.html 이 읽음)
  - docs/data/market_summary.json: make_summary.py 홈 카드용 최신 요약

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

REPORTS = os.path.join(PROJ, "docs")
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
_KRX_INDEX_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


def fetch_index(symbol: str, start: dt.date) -> pd.Series:
    """지수 일봉 종가. 신형 공식 차트 API (finance.naver 9/10 종료 대비, 2026-08-28 전환).

    네이버 실패 시 KRX Open API 로 최근 14일만 보충한다(과거값은 히스토리에 이미
    있고, KRX 는 하루 단위 조회라 전 구간 재수신은 낭비). 코스닥은 KOSDAQ 시리즈
    서비스 승인 전까지 KrxNotApproved 로 그대로 실패 → 기존값 유지.
    """
    try:
        url = (f"https://api.stock.naver.com/chart/domestic/index/{symbol}/day"
               f"?startDateTime={start:%Y%m%d}00&endDateTime={dt.date.today():%Y%m%d}23")
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            raise RuntimeError(f"{symbol} 지수 응답이 비어 있음")
        s = pd.Series({pd.Timestamp(str(x["localDate"])): float(x["closePrice"])
                       for x in rows}, name=symbol)
        return s.sort_index()
    except Exception as e:
        log.warning("%s 네이버 지수 실패 → KRX 폴백: %s", symbol, e)
        import krx_api
        since = max(start, dt.date.today() - dt.timedelta(days=14))
        rows = krx_api.index_series(_KRX_INDEX_NAME[symbol], since)
        if not rows:
            raise RuntimeError(f"{symbol} KRX 폴백도 값 없음") from e
        log.info("%s KRX 폴백 %d일 (최근 %s)", symbol, len(rows), max(rows))
        return pd.Series({pd.Timestamp(d): c for d, (c, _) in rows.items()}, name=symbol).sort_index()


def fetch_usdkrw(start: dt.date) -> pd.Series:
    """원달러 환율 (FDR) — 공포탐욕지수의 안전자산 수요 요소."""
    import FinanceDataReader as fdr
    df = fdr.DataReader("USD/KRW", start.isoformat())
    return df["Close"].rename("usdkrw")


# ------------------------------------------------------------------ KRX (VKOSPI·풋콜)
def fetch_krx_series(hist: pd.DataFrame, col: str, fn, recent_n: int = 5) -> pd.Series:
    """히스토리 날짜 중 col 이 비어 있는 날 + 최근 recent_n 거래일을 KRX 에서 받는다.

    KRX 는 하루 단위 조회뿐이라 날짜마다 호출하지만 krx_api 가 확정일을 디스크
    캐시하므로 첫 백필 뒤에는 하루 몇 건만 나간다. 값이 하나도 없으면 예외.
    """
    import krx_api
    if col in hist.columns:
        need = list(hist.index[hist[col].isna()])
    else:
        need = list(hist.index)
    need = sorted(set(need) | set(hist.index[-recent_n:]))
    vals, miss = {}, 0
    for ts in need:
        v = fn(ts.date())
        if v is None:
            miss += 1
        else:
            vals[ts] = v
    if not vals:
        raise RuntimeError(f"KRX {col}: {len(need)}일 조회했지만 값 없음")
    if miss:
        log.info("KRX %s: %d일 조회, %d일 값 없음(휴장·미게시)", col, len(need), miss)
    return pd.Series(vals, name=col).sort_index()


def fetch_vkospi_investing() -> pd.Series:
    """인베스팅닷컴 히스토리 테이블(최근 1개월) — KRX 실패 시 폴백."""
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


EW_ETF = "252650"   # KODEX 200동일가중 — 시총가중 지수 대신 '평균 대형주'의 추세


def fetch_ew_mom(start: dt.date) -> tuple[pd.Series, pd.Series]:
    """동일가중 ETF 종가와 125일선 대비 모멘텀(%).

    125일선이 히스토리 첫날부터 있도록 start 보다 200 달력일 앞에서 받는다
    (ETF 는 2016년 상장이라 백필 여유 충분). 매 실행 전 구간을 다시 받아
    apply_series 로 덮으므로 하루 실패해도 기존 값이 유지된다.
    """
    import FinanceDataReader as fdr
    df = fdr.DataReader(EW_ETF, (start - dt.timedelta(days=200)).isoformat())
    if df is None or df.empty:
        raise RuntimeError("동일가중 ETF 응답이 비어 있음")
    close = df["Close"].astype(float)
    close.index = pd.to_datetime(close.index)
    mom = (close / close.rolling(125).mean() - 1) * 100
    return close, mom


def apply_series(hist: pd.DataFrame, s: pd.Series, col: str) -> pd.DataFrame:
    """지수/VKOSPI 시계열을 히스토리에 갱신 병합(새 값 우선, 과거값 보존)."""
    hist = hist.copy()
    if col not in hist.columns:
        hist[col] = np.nan
    # NaN 은 병합하지 않는다 — FDR 이 당일 행을 NaN 으로 주면 전날 저장한 값이
    # 지워졌다(2026-09-02 원달러 09-01 소실). 값이 있는 날짜만 덮어쓴다.
    s = s.dropna()
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

    # 종합 체온 (2026-09-15 재설계): 성분을 '높음=뜨거움' 방향으로 정렬(변동성 반전)해
    # 합산한 뒤 표준화(평균 50·σ16). 백분위 단순평균은 성분이 무상관이라 σ10 으로 압축돼
    # 50~60 에 갇혔고, 변동성 부호가 반대라 폭락장(2026-07-30)에 '중립'이 나오던 결함 수정.
    # 쏠림(conc)은 과열이 아니라 취약성이라 여전히 제외.
    # 모멘텀(동일가중): KODEX 200동일가중 ETF / 125일선. 시총가중 지수 모멘텀은 공포탐욕에
    # 이미 있고 대형주 쏠림에 끌려가(2026-06-22 지수 +51% vs 동일가중 +4.5%) 제외.
    parts = [comp["trend"], comp["spec"], comp["lev"], 100 - comp["vol"]]
    if "ew_mom" in d.columns and d["ew_mom"].notna().any():
        comp["mom"] = pct_rank(d["ew_mom"])
        parts.append(comp["mom"])
    else:
        comp["mom"] = np.nan
    raw = pd.concat(parts, axis=1).mean(axis=1)
    comp["overall"] = (50 + 16 * (raw - raw.mean()) / raw.std()).clip(0, 100)

    # ---- 공포탐욕지수 (CNN Fear & Greed 한국판 7요소) ----
    # 각 요소를 전 히스토리 백분위(0~100)로 정규화해 평균. 높음=탐욕, 낮음=공포.
    # 팩터랩 월간판과 같은 설계 — 미국 신용스프레드는 일일 파이프라인에서 제외.
    # 풋/콜은 2026-09-02 KRX Open API 연동으로 ⑦ 로 편입(원조 CNN 과 같은 5일 평균·역방향).
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
    if "putcall" in d.columns and d["putcall"].notna().sum() > 60:
        raw = d["putcall"].ffill(limit=5).rolling(5).mean()
        fg_items.append(("풋콜(역)", 100 - pct_rank(raw), raw, " 5일"))         # ⑦ 풋콜비율(역)
    fgdf = pd.concat([p for _, p, _, _ in fg_items], axis=1)
    comp["fg"] = fgdf.mean(axis=1).where(fgdf.notna().sum(axis=1) >= 3)

    last = comp.dropna(subset=["overall"]).iloc[-1] if comp["overall"].notna().any() else None
    latest = {k: (round(float(last[k]), 1) if last is not None and pd.notna(last[k]) else None)
              for k in ("overall", "trend", "spec", "conc", "vol", "lev", "fg", "mom")}
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
    (allorigins 폴백 내장)를 재사용한다. 각 항목은 실패해도 파이프라인은 계속 간다.
    미국 지표는 전일(현지) 값이 최신이다.

    실패한 항목은 마지막 성공값(data/macro_last.json)으로 채우고 stale=True 를 붙인다
    (2026-09-02 allorigins 장애로 NFCI·10Y 가 화면에서 사라진 뒤 추가). 성공값은
    None 으로 덮어쓰지 않으므로 며칠 장애가 이어져도 마지막 값이 유지된다.
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
    return _macro_with_fallback(macro)


MACRO_LAST = os.path.join(BASE, "data", "macro_last.json")


def _macro_with_fallback(macro: dict) -> dict:
    """실패(None) 항목을 마지막 성공값으로 채우고, 성공값은 파일에 갱신한다."""
    try:
        with open(MACRO_LAST, encoding="utf-8") as f:
            last = json.load(f)
    except Exception:
        last = {}
    for key, val in list(macro.items()):
        if val:
            last[key] = {k: v for k, v in val.items() if k != "stale"}
        elif last.get(key):
            macro[key] = {**last[key], "stale": True}
            log.info("%s 는 마지막 성공값 유지 (%s)", key, last[key].get("date"))
    try:
        os.makedirs(os.path.dirname(MACRO_LAST), exist_ok=True)
        with open(MACRO_LAST, "w", encoding="utf-8") as f:
            json.dump(last, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning("macro_last.json 저장 실패: %s", e)
    return macro


# ------------------------------------------------------------------ 출력
def _round(x, nd=1):
    """리스트/시리즈 → JSON 직렬화용(반올림, NaN→None)."""
    out = []
    for v in (x.tolist() if hasattr(x, "tolist") else x):
        out.append(None if v is None or (isinstance(v, float) and not np.isfinite(v))
                   else round(float(v), nd))
    return out


# ------------------------------------------------------------------ 신호·과거 통계
# (키, 이름, 규칙 설명) — 화면 자동 해석이 활성 신호를 최우선으로 보여주고
# "과거 N회 → 이후 20/60일 코스피 평균" 근거를 붙인다.
SIGNAL_DEFS = [
    ("bear_div",     "약세 다이버전스", "지수 60일 신고가(1년 고점의 90% 이내)인데 온도계 40 미만"),
    ("narrow_rebound", "폭 좁은 반등",  "지수 60일 신고가지만 1년 고점보다 10% 이상 아래 + 온도계 40 미만"),
    ("bull_div",     "강세 다이버전스", "지수 60일 신저가인데 온도계가 20일간 +5 이상 상승"),
    ("healthy_high", "건전한 신고가",   "지수 60일 신고가 + 온도계 55 이상"),
    ("cold",         "냉각 구간",       "온도계 20 미만"),
    ("hot",          "과열 구간",       "온도계 80 이상"),
]


def build_signals(d: pd.DataFrame, comp: pd.DataFrame) -> dict:
    """신호 플래그(최근 CHART_ROWS일)와 신호별 과거 성과 통계.

    통계는 연속 신호일을 10일 간격으로 묶은 '에피소드 첫날' 기준 — 같은 국면의
    연속일을 여러 번 세는 중복을 줄인다. 표본이 적으면 화면에서 '참고용' 표기.
    """
    def _r(x):
        return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 1)

    T = comp["overall"]
    ks = d["kospi_close"].reindex(T.index)
    hi60 = ks >= ks.rolling(60).max()
    lo60 = ks <= ks.rolling(60).min()
    # 고점권 조건(1년 고점의 90% 이내): 폭락 뒤 60일 창이 낮아지며 낮은 고점 반등이
    # '신고가'로 잡혀 고점 경고로 오인되는 것을 막는다 (2026-09-15 사용자 결정, 90%).
    near_long = ks >= ks.rolling(250, min_periods=120).max() * 0.90
    tchg = T - T.shift(20)
    flags = pd.DataFrame({
        "bear_div": hi60 & near_long & (T < 40),
        "narrow_rebound": hi60 & ~near_long & (T < 40),
        "bull_div": lo60 & (tchg >= 5),
        "healthy_high": hi60 & (T >= 55),
        "cold": T < 20,
        "hot": T >= 80,
    }, index=T.index).fillna(False)
    fwd20 = ks.shift(-20) / ks - 1
    fwd60 = ks.shift(-60) / ks - 1
    base20, base60 = fwd20.dropna(), fwd60.dropna()
    base = {"n": int(len(base20)), "r20": _r(base20.mean() * 100),
            "win20": _r((base20 > 0).mean() * 100), "r60": _r(base60.mean() * 100)}

    stats = {}
    for key, name, rule in SIGNAL_DEFS:
        m = flags[key]
        starts, prev = [], None
        for ts in m[m].index:
            if prev is None or (ts - prev).days > 10:
                starts.append(ts)
            prev = ts
        ep20 = fwd20.reindex(starts).dropna()
        ep60 = fwd60.reindex(starts).dropna()
        stats[key] = {
            "name": name, "rule": rule, "episodes": len(starts), "n_days": int(m.sum()),
            "n_eval": int(len(ep20)),
            "r20": _r(ep20.mean() * 100) if len(ep20) else None,
            "win20": _r((ep20 > 0).mean() * 100) if len(ep20) else None,
            "r60": _r(ep60.mean() * 100) if len(ep60) else None,
            "last": f"{starts[-1]:%Y-%m-%d}" if starts else None,
        }
    recent = flags.tail(CHART_ROWS)
    return {"base": base, "stats": stats,
            "flags": {k: recent[k].astype(int).tolist() for k in flags.columns}}


# ------------------------------------------------------------------ 자동 해석(문장 생성)
# 웹(market.html)과 텔레그램이 같은 문장을 쓰도록 여기서 한 번만 만든다.
# 규칙 우선순위: 다이버전스 신호 → 극단(과열/냉각) → 건전/뜨거운 상승 → 심리·내부 괴리 → 차가움 → 중립.
def _f(v, nd=0):
    return "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{nd}f}"


def _sgn(v, nd=1):
    return ("+" if v > 0 else "") + _f(v, nd)


def build_interp(d: pd.DataFrame, comp: pd.DataFrame, signals: dict) -> list:
    """최근 CHART_ROWS일 각각의 해석 {icon, main, sub, lines[3](HTML), summary, stat}. 값 없으면 None."""
    recent = d.tail(CHART_ROWS)
    off = len(d) - len(recent)
    g = lambda col, frame=d: frame[col].to_numpy(dtype=float) if col in frame.columns else None  # noqa: E731
    ks, T = g("kospi_close"), comp["overall"].reindex(d.index).to_numpy(dtype=float)
    FG, TR, SP, LV, VOL = (comp[c].reindex(d.index).to_numpy(dtype=float)
                           for c in ("fg", "trend", "spec", "lev", "vol"))
    MO, CONC = g("ew_mom"), g("top10_share")
    ADV, DEC, NH, NL, MA200, MA50 = (g(c) for c in ("all_adv", "all_dec", "all_nh", "all_nl", "all_ma200", "all_ma50"))
    flags = signals.get("flags", {})
    ok = lambda x: x is not None and np.isfinite(x)  # noqa: E731
    out = []
    for j in range(len(recent)):
        i = off + j
        t = T[i]
        if not ok(t):
            out.append(None)
            continue
        on = lambda k: bool(flags.get(k) and flags[k][j] == 1)  # noqa: E731
        fg, tr, sp, lv = FG[i], TR[i], SP[i], LV[i]
        conc = CONC[i] if CONC is not None else np.nan
        mo = MO[i] if MO is not None and ok(MO[i]) else None
        kv = ks[i] if ok(ks[i]) else None
        hist_hi = np.nanmax(ks[:i + 1]) if kv is not None else None
        near_high = kv is not None and hist_hi and kv >= hist_hi * 0.97
        r20 = (ks[i] / ks[i - 20] - 1) * 100 if i >= 20 and ok(ks[i]) and ok(ks[i - 20]) else None
        # 달력 기준 한 달(30일) 수익률 — 20거래일 수익률은 기준일이 급락 저점에 걸리면
        # 방향이 뒤집힌다(2026-09-16: 20거래일 +3.8% vs 달력 1개월 -3.7%). 문장에서 병기.
        r1m = None
        if kv is not None:
            j1 = d.index.searchsorted(d.index[i] - pd.Timedelta(days=30), side="right") - 1
            if 0 <= j1 < i and ok(ks[j1]):
                r1m = (kv / ks[j1] - 1) * 100
        tch = t - T[i - 20] if i >= 20 and ok(T[i - 20]) else None
        chg = lambda arr: arr[i] - arr[i - 20] if i >= 20 and ok(arr[i]) and ok(arr[i - 20]) else None  # noqa: E731
        win = ks[max(0, i - 59):i + 1]
        hi60 = np.nanmax(win) if kv is not None else None
        lo60 = np.nanmin(win) if kv is not None else None
        off_hi = (kv / hi60 - 1) * 100 if kv is not None and hi60 else None
        mo_txt = f" 동일가중 200종목은 125일선 대비 {_sgn(mo)}%." if mo is not None else ""
        tch_txt = f" 온도계는 20일간 {_sgn(tch, 0)}." if tch is not None else ""

        # ---- 강세 다이버전스 신뢰도 (실전 독법 — 2026-09-17 사용자 요청) ----
        #  high  : 최근 40거래일 안에 냉각(20 미만)을 거쳤고 레버리지가 가벼움(40 미만, 청산 완료)
        #  fake  : 온도계 상승이 변동성 진정에서만 나오고 추세(폭)는 20일 전보다 내려가는 중
        #  normal: 그 외
        bull_grade, tr_ch = None, chg(TR)
        if on("bull_div"):
            was_cold = np.nanmin(T[max(0, i - 40):i + 1]) < 20
            vol_ch = chg(VOL)
            fake = tr_ch is not None and tr_ch <= -3 and vol_ch is not None and vol_ch <= -3
            bull_grade = "fake" if fake else "high" if (was_cold and ok(lv) and lv < 40) else "normal"

        # ---- 헤드라인 규칙 ----
        stat = None
        if on("bear_div"):
            icon, main = "🔻", f"약세 다이버전스: 지수는 60일 신고가인데 온도계는 {t:.0f}입니다."
            sub = (f"지수를 소수 종목이 끌어올리고(TOP10 집중 {_f(conc)}%) 시장 내부는 따라오지 못하는 상태."
                   f"{mo_txt}{tch_txt} 폭 분석에서 고점 경고로 쓰는 고전적 신호.")
            stat = "bear_div"
        elif on("bull_div"):
            icon, main = "🔺", f"강세 다이버전스: 지수는 60일 신저가인데 온도계는 오르고 있습니다({t:.0f})."
            grade_txt = {"high": " 신뢰도 높음 — 냉각(20 미만)을 거친 뒤의 회복이고 레버리지도 가벼움(청산 완료).",
                         "fake": " ⚠ 가짜 가능성 — 상승이 변동성 진정에서만 나오고 추세(폭)는 아직 내려가는 중.",
                         "normal": " 신뢰도 보통 — 냉각을 거치지 않았거나 레버리지가 아직 가볍지 않음."}[bull_grade]
            sub = (f"가격은 저점을 낮추지만 내부(폭·투기·레버리지)는 회복 중 — 바닥 다지기의 전형.{tch_txt}{grade_txt}"
                   " 매수 신호가 아니라 '관심 구간 진입' 신호.")
            stat = "bull_div"
        elif on("healthy_high"):
            icon, main = "✅", f"건전한 신고가: 지수 신고가에 시장 내부도 함께 뜨겁습니다({t:.0f})."
            sub = f"폭이 넓은 상승 — 추세를 의심할 근거가 없는 구간.{tch_txt}"
            stat = "healthy_high"
        elif on("narrow_rebound"):
            win250 = ks[max(0, i - 249):i + 1]
            off250 = (kv / np.nanmax(win250) - 1) * 100 if kv is not None else None
            icon, main = "↗️", (f"폭 좁은 반등: 지수는 60일 신고가지만 1년 고점보다 {_f(abs(off250)) if off250 is not None else '–'}% 아래이고 "
                                f"온도계는 {t:.0f}입니다.")
            sub = (f"폭락 뒤 소수 종목이 이끄는 반등(TOP10 집중 {_f(conc)}%). 고점 경고는 아니지만 폭이 넓어지지 않으면 "
                   f"저항에 막히기 쉬운 구간.{mo_txt}{tch_txt}")
            stat = "narrow_rebound"
        elif near_high and t < 40:
            icon, main = "⚠️", f"지수는 고점권인데 내부 온도는 차갑습니다({t:.0f})."
            sub = (f"소수 종목이 지수를 끌어올리는 폭 좁은 상승(TOP10 집중 {_f(conc)}%).{mo_txt}{tch_txt}"
                   " 역사적으로 폭 붕괴에 앞서 나타나는 패턴 — 경계.")
            stat = "bear_div"
        elif t >= 80:
            icon, main = "🔥", "과열: 폭·투기·레버리지가 동시에 역사적 상단입니다."
            sub = f"상승 사이클 후반부의 전형 — 신규 진입보다 리스크 관리가 우선인 구간.{tch_txt}"
            stat = "hot"
        elif t < 20:
            icon, main = "🧊", "냉각: 공포와 청산이 극단입니다."
            sub = f"역사적으로 바닥 탐색 구간 — 반전 신호(Zweig 스러스트·90% 업데이·신저가 감소)를 확인하며 분할 대응.{tch_txt}"
            stat = "cold"
        elif t >= 60 and sp < 40 and lv < 60:
            icon, main = "✅", "건전한 상승: 폭이 넓고 광기·빚이 없는 상승입니다."
            sub = "사이클 초·중반의 가장 좋은 조합 — 투기·레버리지가 뒤따라 오르는지가 다음 관전 포인트."
        elif t >= 60:
            icon, main = "🌡️", f"뜨거운 상승: {'레버리지' if lv >= 80 else '투기 열기'}가 동반 상승 중입니다."
            sub = "폭은 넓지만 연료가 빚·단타로 바뀌는 중 — 과열(80) 진입 여부 감시."
        elif ok(fg) and fg - t >= 25:
            icon, main = "↕️", f"심리(공포탐욕 {fg:.0f})가 내부 온도({t:.0f})보다 크게 앞서 있습니다."
            sub = "기대가 실체(폭·체력)보다 먼저 달아오른 상태 — 내부가 따라오지 못하면 되돌림 위험."
        elif ok(fg) and t - fg >= 25:
            icon, main = "🧱", f"내부는 뜨거운데 심리는 차갑습니다(공포탐욕 {fg:.0f})."
            sub = "의심 속의 상승(wall of worry) — 역사적으로 지속성이 좋은 편."
        elif t < 40:
            icon, main = "❄️", "차가움: 폭이 좁고 열기가 없습니다." + (" 최근 급락의 여파." if r20 is not None and r20 < -8 else "")
            sub = "지수 방향과 별개로 시장 체력이 약한 상태 — 반등이 와도 폭이 넓어지는지 확인 필요."
        else:
            icon, main = "➖", "중립: 뚜렷한 극단 신호가 없습니다."
            sub = f"추세(폭) {_f(tr)} · 투기 {_f(sp)} · 레버리지 {_f(lv)} — 서브 게이지의 방향 변화를 지켜볼 구간."

        # ---- 현 상황 데이터 3줄 (HTML) ----
        lines = []
        if kv is not None:
            s = f"<b>지수</b>코스피 {_f(kv)}"
            if r20 is not None:
                s += f" · 20일 {_sgn(r20)}%"
            if hi60:
                s += f" · 60일 고점 대비 {_f(off_hi, 1)}%, 저점 대비 +{_f((kv / lo60 - 1) * 100, 1)}%"
            lines.append(s)
        ma, nhv, nlv = MA200[i], NH[i], NL[i]
        if ok(ma):
            s = (f"<b>내부</b>200일선 위 {_f(ma, 1)}%, 50일선 위 {_f(MA50[i], 1)}% · 등락 "
                 f'<span style="color:var(--up)">+{_f(ADV[i])}</span>/<span style="color:var(--dn)">-{_f(DEC[i])}</span>')
            if (ok(nhv) and nhv) or (ok(nlv) and nlv):
                s += f" · 52주 신고 {_f(nhv)} / 신저 {_f(nlv)}"
            s += " → 폭 " + ("넓음" if tr >= 60 else "보통" if tr >= 40 else "좁음")
            lines.append(s)
        s = (f"<b>심리·자금</b>공포탐욕 {_f(fg)} · 레버리지 {_f(lv)} · 투기 열기 {_f(sp)} · TOP10 쏠림 {_f(conc)}%")
        if mo is not None:
            s += f" · 동일가중 모멘텀 {_sgn(mo)}%"
        lines.append(s)

        # ---- 요약(완결 문장 2줄) ----
        move = ("" if r20 is None else f"20거래일 전보다 {_f(r20, 1)}% 올라" if r20 >= 3
                else f"20거래일 전보다 {_f(abs(r20), 1)}% 내려" if r20 <= -3 else "20거래일째 횡보하며")
        # 20거래일과 달력 한 달의 방향이 다르면(예: 기준일이 급락 저점) 달력 기준을 병기해 오해를 막는다
        if move and r1m is not None and (r20 is None or (r20 >= 3) != (r1m >= 3) or (r20 <= -3) != (r1m <= -3)):
            move += (f"(달력 한 달 전보다는 {_f(abs(r1m), 1)}% {'높은' if r1m > 0 else '낮은'} 수준)"
                     if abs(r1m) >= 1 else "(달력 한 달 전과는 비슷한 수준)")
        pos = ("" if off_hi is None else "60일 고점 부근에" if off_hi >= -2
               else f"60일 고점보다 {_f(abs(off_hi))}% 낮은 자리에")
        idx_txt = f"지수는 {move} {pos} 있고"
        fg_ch, fg_up, fg_dn = chg(FG), False, False
        if fg_ch is not None:
            fg_up, fg_dn = fg_ch >= 3, fg_ch <= -3
        t_up, t_dn = tch is not None and tch >= 3, tch is not None and tch <= -3
        fg_name = f"심리(공포탐욕 {_f(fg)})"
        fg_txt = ("" if not ok(fg) else
                  f"{fg_name}도 함께 회복되고 있고 " if fg_up and t_up else
                  f"{fg_name}는 회복되는 중이지만 " if fg_up else
                  f"{fg_name}도 함께 식고 있고 " if fg_dn and t_dn else
                  f"{fg_name}는 식고 있지만 " if fg_dn else f"{fg_name}는 제자리이고 ")
        tch_txt2 = ("" if tch is None else
                    f"온도계는 20일 전보다 {_f(abs(tch))}점 더 내려온 상태" if t_dn else
                    f"온도계는 20일 전보다 {_f(tch)}점 올라온 상태" if t_up else "온도계는 20일째 제자리")
        if on("bear_div") or (near_high and t < 40):
            summ = (f"{idx_txt}, 200일선 위 종목은 {_f(ma)}%에 그치고 신저가({_f(nlv)})가 신고가({_f(nhv)})보다 많아 "
                    "소수 종목이 지수를 끌어올리는 상승입니다. 이런 괴리는 대개 폭이 무너지며 해소되므로 200일선 위 비율과 "
                    "신고가 종목수가 더 줄어드는지 확인하고, 신규 진입은 보수적으로 가져가는 것이 정석입니다.")
        elif on("narrow_rebound"):
            summ = (f"{idx_txt}, 60일 기준으로는 신고가지만 1년 고점에서는 아직 멀고 200일선 위 종목이 {_f(ma)}%뿐이라 "
                    "소수 종목이 끌어올리는 폭 좁은 반등입니다. 6월 같은 고점 경고와는 다른 국면이지만, 온도계가 40을 넘어서며 "
                    "200일선 위 비율이 늘어나지 않으면 반등이 저항에서 멈추기 쉬우니 추격 매수보다 폭 확장 확인이 먼저입니다.")
        elif on("bull_div"):
            head = f"지수는 60일 저점을 다시 낮췄지만 {tch_txt2}로, 내부가 가격보다 먼저 회복되는 바닥 다지기의 모습입니다. "
            if bull_grade == "high":
                summ = (head + f"온도계가 최근 20 아래 냉각을 거친 뒤 올라오고 있고 레버리지({_f(lv)})도 가벼워, 청산이 끝난 뒤의 "
                        "회복일 가능성이 높은 조합입니다. 다만 매수 신호가 아니라 관심 구간 진입 신호이므로 Zweig 스러스트·"
                        f"90% 업데이·신저가 종목수({_f(nlv)}) 감소 같은 반전 확인과 함께 분할로 접근하는 것이 안전합니다.")
            elif bull_grade == "fake":
                summ = (head + f"그러나 온도계 상승이 변동성 진정에서만 나오고 추세(폭)는 20일 전보다 {_f(abs(tr_ch))}점 더 "
                        "내려가는 중이라 가짜 신호일 수 있습니다. 하락 종목과 신저가가 줄며 추세 게이지가 돌아서기 전까지는 "
                        "관망하고, 돌아선 뒤에도 반전 확인 신호와 함께 분할로 접근하는 것이 맞습니다.")
            else:
                summ = (head + f"다만 냉각(20 미만)을 거치지 않았거나 레버리지({_f(lv)})가 아직 가볍지 않아 신뢰도는 보통입니다. "
                        "이 패턴은 몇 주씩 이어질 수 있으니 매수 신호가 아닌 관심 구간 진입으로 보고, 신저가 종목수"
                        f"({_f(nlv)}) 감소와 반전 신호(Zweig 스러스트·90% 업데이)를 확인하며 분할로 대응합니다.")
        elif t >= 80:
            summ = (f"{idx_txt}, 폭·투기·레버리지가 동시에 역사적 상단이라 상승 사이클 후반부의 전형적인 과열 상태입니다. "
                    "이 구간에서는 신규 진입보다 이익 실현과 비중 관리가 우선이며, 온도계가 75 아래로 내려오면 과열 해제로 봅니다.")
        elif t < 20:
            summ = (f"{idx_txt}, 온도계가 {t:.0f}까지 내려와 공포와 청산이 극단에 달한 냉각 국면입니다. "
                    "이번 표본에서는 냉각 진입 뒤에도 하락이 더 이어진 경우가 많았으므로 냉각 자체를 매수 신호로 보지 말고, "
                    "반전 신호(Zweig 스러스트·90% 업데이·신저가 감소)를 기다려 분할로 대응하는 것이 맞습니다.")
        elif t >= 60 and sp < 40 and lv < 60:
            summ = (f"{idx_txt}, 200일선 위 종목이 {_f(ma)}%로 상승이 넓게 퍼져 있는데 투기 열기({_f(sp)})와 "
                    f"레버리지({_f(lv)})는 아직 낮아 광기 없는 건전한 상승입니다. 다음 국면은 투기와 레버리지가 뒤따라 "
                    "오르는지로 판단하며, 세 지표가 함께 80을 넘기 전까지는 추세를 의심할 이유가 없습니다.")
        elif t >= 60:
            hot = f"레버리지({_f(lv)})" if lv >= 80 else f"투기 열기({_f(sp)})"
            summ = (f"{idx_txt}, 폭은 넓지만 {hot}가 함께 달아오르며 상승의 연료가 빚·단타로 바뀌는 중입니다. "
                    "아직 과열(80)은 아니지만 온도계·투기·레버리지가 동시에 80을 넘으면 후반부 신호로 보고 리스크 관리로 "
                    "전환할 준비를 하는 구간입니다.")
        elif t < 40 and t_dn:
            summ = (f"{idx_txt}, 200일선 위 종목이 {_f(ma)}%뿐이라 시장 내부는 아직 차갑습니다. {fg_txt}{tch_txt2}라, "
                    f"온도계가 저점을 찍고 올라서고 신저가 종목수({_f(nlv)})가 줄어드는 것이 확인되기 전까지는 반등이 와도 "
                    "폭 좁은 반등에 그칠 가능성이 큽니다.")
        elif t < 40:
            summ = (f"{idx_txt}, 200일선 위 종목이 {_f(ma)}%로 내부는 아직 차갑지만 {tch_txt2}로 회복이 시작되는 초기 "
                    "신호입니다. 200일선 위 비율이 30%대로 올라서고 신저가 종목수가 줄어들면 회복 국면으로 볼 수 있고, "
                    "그 전까지는 확인 단계로 봅니다.")
        else:
            nxt = ("내부가 회복되는 흐름이 이어져 온도계가 60을 넘어서면 회복 국면으로 격상됩니다." if t_up and not fg_dn else
                   "식는 흐름이 이어져 온도계가 40 아래로 내려가면 경계 쪽으로 기울어집니다." if t_dn and not fg_up else
                   "온도계와 공포탐욕이 같은 방향으로 움직이기 시작하는 쪽이 다음 국면의 단서가 됩니다.")
            summ = f"{idx_txt}, 온도계 {t:.0f}의 중립 구간이라 뚜렷한 극단 신호는 없습니다. {fg_txt}{tch_txt2}라, {nxt}"
        out.append({"icon": icon, "main": main, "sub": sub, "lines": lines, "summary": summ, "stat": stat})
    return out


def write_outputs(d: pd.DataFrame, comp: pd.DataFrame, scores: dict,
                  lev: dict | None, flows: dict | None, macro: dict | None = None,
                  signals: dict | None = None, interp: list | None = None):
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
                              for k in ("overall", "trend", "spec", "conc", "vol", "lev", "fg", "mom")}},
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
        "putcall": _round(recent.get("putcall", pd.Series(index=recent.index)), 3),
        "ew": {"close": _round(recent.get("ew_close", pd.Series(index=recent.index)), 0),
               "mom": _round(recent.get("ew_mom", pd.Series(index=recent.index)), 2)},
        "signals": signals or {},
        "interp": interp or [],
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
        "mom": scores.get("mom"),
        # 카톡 알림용: 오늘 활성 신호 + 신호별 과거 성과
        "signals": ({"active": [k for k, arr in signals["flags"].items() if arr and arr[-1] == 1],
                     "stats": signals["stats"], "base": signals["base"]} if signals else {}),
        # 텔레그램·카톡 공용 해석 문장(최신일): icon·main·summary
        "interp": ({k: v for k, v in next((x for x in reversed(interp) if x), {}).items()
                    if k in ("icon", "main", "summary")} if interp else {}),
        "ma200": None if pd.isna(last["all_ma200"]) else round(float(last["all_ma200"]), 1),
        "nh": None if pd.isna(last["all_nh"]) else int(last["all_nh"]),
        "nl": None if pd.isna(last["all_nl"]) else int(last["all_nl"]),
        "adv": int(last["all_adv"]), "dec": int(last["all_dec"]),
        "vkospi": (None if "vkospi" not in recent.columns or pd.isna(last.get("vkospi"))
                   else round(float(last["vkospi"]), 2)),
        "putcall": (None if "putcall" not in recent.columns or pd.isna(last.get("putcall"))
                    else round(float(last["putcall"]), 2)),
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
        ew_close, ew_mom = fetch_ew_mom(start)
        hist = apply_series(hist, ew_close, "ew_close")
        hist = apply_series(hist, ew_mom, "ew_mom")
        log.info("동일가중 ETF(%s) %d일 (최근 %s 125일선 대비 %+.1f%%)",
                 EW_ETF, len(ew_close), ew_mom.index[-1].date(), ew_mom.iloc[-1])
    except Exception as e:
        log.warning("동일가중 ETF 수집 실패(기존 값 유지): %s", e)

    try:
        hist = apply_series(hist, fetch_usdkrw(start), "usdkrw")
    except Exception as e:
        log.warning("원달러 환율 수집 실패(기존 값 유지): %s", e)

    if not args.no_vkospi:
        import krx_api
        try:
            vk = fetch_krx_series(hist, "vkospi", krx_api.vkospi)
            hist = apply_series(hist, vk, "vkospi")
            log.info("VKOSPI(KRX) %d일 갱신 (최근 %s = %.2f)",
                     len(vk), vk.index[-1].date(), vk.iloc[-1])
        except Exception as e:
            log.warning("VKOSPI KRX 실패 → 인베스팅 폴백: %s", e)
            try:
                vk = fetch_vkospi_investing()
                hist = apply_series(hist, vk, "vkospi")
                log.info("VKOSPI(인베스팅) %d일 수집 (최근 %s = %.2f)",
                         len(vk), vk.index[-1].date(), vk.iloc[-1])
            except Exception as e2:
                log.warning("VKOSPI 수집 실패(기존 값 유지): %s", e2)
        try:
            pc = fetch_krx_series(hist, "putcall", krx_api.putcall_ratio)
            hist = apply_series(hist, pc, "putcall")
            log.info("풋콜비율(KRX) %d일 갱신 (최근 %s = %.3f)",
                     len(pc), pc.index[-1].date(), pc.iloc[-1])
        except Exception as e:
            log.warning("풋콜비율 수집 실패(기존 값 유지): %s", e)

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
    signals = build_signals(d, comp)
    log.info("신호 통계: %s", {k: (v["episodes"], v["r20"], v["r60"]) for k, v in signals["stats"].items()})
    interp = build_interp(d, comp, signals)
    latest_interp = next((x for x in reversed(interp) if x), None)
    if latest_interp:
        log.info("해석: %s %s", latest_interp["icon"], latest_interp["main"])
    write_outputs(d, comp, scores, lev, flows, macro, signals, interp)
    log.info("완료")


if __name__ == "__main__":
    main()
