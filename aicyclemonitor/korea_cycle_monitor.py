# -*- coding: utf-8 -*-
"""
한국 시장 사이클 모델 (korea_cycle_monitor.py)
================================================================================
켄 피셔식 접근: "지수 레벨·밸류에이션을 예측하지 말고, 강세장의 어느 단계인지 확인하라."
Global → 반도체 → Breadth → Euphoria 순서로 5개 팩터를 0~100점으로 채점해
가중 합산한 Market Regime Score 를 만든다.

  팩터 (가중치)                 소스
  ① Global Trend      (25)     yfinance: S&P500·SOX 200일선 이격도, VIX, 달러인덱스
  ② 반도체·이익 리비전 (25)     리포트서머리.xlsx(삼전·하이닉스 목표주가 컨센서스 기울기),
                               memory-cycle/data/quarterly.csv(메모리 사이클 국면),
                               quant-data 주간 스냅샷(전종목 영업이익(E) 상향비율),
                               삼전·하이닉스 주가 200일선 이격도
  ③ Market Breadth    (20)     reports/data/market_history.csv (breadth_build.py 산출)
                               — MA200/MA50 위 비율·ADR20·신고/신저·맥클렐런
  ④ 신용·유동성       (15)     market_leverage_collector(예탁금·반대매매·신용/예탁금),
                               FRED 하이일드 OAS(레벨+90일 변화, allorigins 폴백)
  ⑤ Euphoria          (15)     저가주 거래대금 비중·상한가 수·회전율·신용융자 20일 증가율
                               — 과열일수록 감점(100-heat)

  채점: 각 하위지표를 자기 히스토리 대비 백분위(0~100)로 변환(방향 통일) 후 평균.
        목표주가 기울기·사이클 국면은 규칙 기반 매핑(히스토리가 짧아 백분위 부적합).
  국면: ≥80 Strong Bull(주식 90~100%) / 65~80 Bull(75~90%) / 50~65 Neutral(50~75%)
        / 35~50 Risk-off(30~50%) / <35 Bear(현금·헤지 확대)

실행:  python korea_cycle_monitor.py            # 수집 + korea_cycle.html 생성
       python korea_cycle_monitor.py --offline  # 네트워크 생략(캐시만 사용)

주의:  market_history 는 2025-02~, 레버리지는 약 3개월치라 백분위의 기준 분포가
       아직 짧다. 히스토리가 쌓일수록 점수의 안정성이 올라간다.
       투자 판단·권유가 아니라 시장 국면의 '체온계' 기록이다.
================================================================================
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CACHE = os.path.join(BASE, "kc_cache")
os.makedirs(CACHE, exist_ok=True)

MARKET_HIST = os.path.join(ROOT, "reports", "data", "market_history.csv")
LEV_DIR = os.path.join(ROOT, "market_leverage_collector", "data")
MC_QUARTERLY = os.path.join(ROOT, "memory-cycle", "data", "quarterly.csv")
MC_CONSENSUS = os.path.join(ROOT, "memory-cycle", "data", "consensus.csv")
REPORT_XLSX = os.path.join(ROOT, "리포트서머리.xlsx")
QD_OUT = os.path.join(ROOT, "quant-data", "output")

GLOBAL_CACHE = os.path.join(CACHE, "global.csv")
REVB_CACHE = os.path.join(CACHE, "revision_breadth.csv")
SCORE_LOG = os.path.join(CACHE, "score_log.csv")
TEMPLATE = os.path.join(BASE, "korea_cycle_template.html")
OUTPUT = os.path.join(BASE, "korea_cycle.html")
DEPLOY = os.path.join(ROOT, "reports", "korea_cycle.html")   # 알파노트 배포 사본

FIRMS = {"005930": "삼성전자", "000660": "SK하이닉스"}
WEIGHTS = {"global": 25, "semi": 25, "breadth": 20, "liq": 15, "euphoria": 15}
PHASE_SCORE = {"확장": 85, "회복": 65, "피크권": 35, "수축": 15}
REGIMES = [  # (하한, 라벨, 권장 주식비중, 색 클래스)
    (80, "Strong Bull", "90~100%", "good"),
    (65, "Bull", "75~90%", "good"),
    (50, "Neutral", "50~75%", "warning"),
    (35, "Risk-off", "30~50%", "serious"),
    (0, "Bear", "현금·헤지 확대", "critical"),
]
YF_TICKERS = {"^GSPC": "spx", "^SOX": "sox", "^VIX": "vix", "DX-Y.NYB": "dxy",
              "005930.KS": "ss_px", "000660.KS": "hx_px"}
CHART_ROWS = 370            # 대시보드에 싣는 최근 거래일 수 (market_history 전 구간)
PRICE_LOOKBACK = "3y"


def log(msg):
    print(f"[korea-cycle] {msg}")


def pct_rank(s: pd.Series) -> pd.Series:
    """자기 히스토리 대비 백분위(0~100). NaN 유지."""
    return s.rank(pct=True) * 100


def _round(x, nd=1):
    out = []
    for v in (x.tolist() if hasattr(x, "tolist") else x):
        out.append(None if v is None or (isinstance(v, float) and not np.isfinite(v))
                   else round(float(v), nd))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 수집 ① 글로벌 (yfinance + FRED, 캐시 병합 — 실패해도 캐시로 계속)
# ──────────────────────────────────────────────────────────────────────────────
def _load_global_cache() -> pd.DataFrame:
    if os.path.exists(GLOBAL_CACHE):
        return pd.read_csv(GLOBAL_CACHE, parse_dates=["date"], index_col="date")
    return pd.DataFrame()


def fetch_global(offline: bool) -> pd.DataFrame:
    cache = _load_global_cache()
    if offline:
        log(f"오프라인 모드 — 글로벌 캐시 {len(cache)}행 사용")
        return cache
    fresh = pd.DataFrame()
    try:
        import yfinance as yf
        px = yf.download(list(YF_TICKERS), period=PRICE_LOOKBACK, interval="1d",
                         auto_adjust=True, progress=False)["Close"]
        fresh = px.rename(columns=YF_TICKERS)
        fresh.index = pd.to_datetime(fresh.index).tz_localize(None).normalize()
        fresh.index.name = "date"
        log(f"yfinance {len(fresh)}행 수집 (~{fresh.index[-1].date()})")
    except Exception as e:
        log(f"yfinance 수집 실패(캐시 사용): {e}")
    try:
        sys.path.insert(0, BASE)
        from ai_cycle_monitor import fetch_fred          # allorigins 폴백 포함
        hy = fetch_fred("BAMLH0A0HYM2") * 100            # % → bp
        hy.index = pd.to_datetime(hy.index).normalize()
        fresh = fresh.join(hy.rename("hyoas"), how="outer") if not fresh.empty \
            else hy.rename("hyoas").to_frame()
        log(f"FRED HY OAS 수집 (최근 {hy.index[-1].date()} = {hy.iloc[-1]:.0f}bp)")
    except Exception as e:
        log(f"FRED HY OAS 수집 실패(캐시 사용): {e}")
    if fresh.empty:
        return cache
    # 병합: 새 값 우선, 캐시의 옛 날짜·다른 열 보존
    for col in cache.columns:
        if col not in fresh.columns:
            fresh[col] = np.nan
    merged = pd.concat([cache[~cache.index.isin(fresh.index)], fresh]).sort_index()
    for col in merged.columns:                            # 겹치는 날짜의 NaN 은 캐시값
        merged[col] = merged[col].fillna(cache[col]) if col in cache.columns else merged[col]
    merged.to_csv(GLOBAL_CACHE, encoding="utf-8")
    return merged


# ──────────────────────────────────────────────────────────────────────────────
# 수집 ② 반도체·이익 리비전
# ──────────────────────────────────────────────────────────────────────────────
def load_consensus() -> pd.DataFrame:
    """리포트서머리.xlsx → 삼전·하이닉스 목표주가 일별 기록.
    xlsx 가 없으면 memory-cycle 의 consensus.csv 폴백."""
    try:
        xl = pd.ExcelFile(REPORT_XLSX)
        rows = []
        for sheet in xl.sheet_names:                      # dt_YYYYMMDD
            d = xl.parse(sheet)
            if "기업명" not in d.columns:
                continue
            for code in FIRMS:
                m = d[d["기업명"].astype(str).str.contains(f"\\({code}\\)", na=False)]
                for _, r in m.iterrows():
                    tp = pd.to_numeric(r.get("목표주가"), errors="coerce")
                    rows.append({"date": pd.to_datetime(str(r.get("수집일자"))[:10]),
                                 "code": code, "target": tp})
        df = pd.DataFrame(rows).dropna(subset=["date", "target"])
        log(f"리포트서머리 목표주가 {len(df)}건 "
            f"({df.date.min().date()} ~ {df.date.max().date()})")
        return df
    except Exception as e:
        log(f"리포트서머리.xlsx 파싱 실패 → consensus.csv 폴백: {e}")
        df = pd.read_csv(MC_CONSENSUS, parse_dates=["date"], dtype={"code": str})
        return df.dropna(subset=["target"])[["date", "code", "target"]]


def target_revision(cons: pd.DataFrame) -> dict:
    """종목별: 일별 중앙값 → 20일 평활 → 60일 기울기(%) 일별 시계열."""
    out = {}
    for code in FIRMS:
        c = cons[cons.code == code]
        daily = c.groupby("date").target.median()
        if len(daily) < 10:
            out[code] = None
            continue
        idx = pd.date_range(daily.index.min(), daily.index.max())
        sm = daily.reindex(idx).ffill().rolling(20, min_periods=5).mean()
        slope = sm.diff(60) / sm.shift(60) * 100          # 60일 변화율(%)
        out[code] = {"smooth": sm, "slope": slope, "last_median": daily.iloc[-1]}
    return out


def load_memory_phase() -> pd.DataFrame:
    """memory-cycle 분기 재무 → 국면 라벨 (cycle_model.classify 와 동일 규칙).
    실전 가용 시점은 분기말+45일(공시 시차)."""
    q = pd.read_csv(MC_QUARTERLY, dtype={"code": str})
    q["qend"] = pd.PeriodIndex(q.year.astype(str) + "Q" + q.q.astype(str),
                               freq="Q").to_timestamp(how="end").normalize()
    frames = []
    for code in FIRMS:
        d = q[q.code == code].sort_values("qend").reset_index(drop=True).copy()
        d["op_yoy"] = d["op"] - d["op"].shift(4)
        d["d_opm"] = d["opm"] - d["opm"].shift(1)
        d["opm_p80"] = d["opm"].rolling(12, min_periods=8).quantile(0.8)

        def _one(r):
            if pd.isna(r.op_yoy) or pd.isna(r.d_opm):
                return None
            if not pd.isna(r.opm_p80) and r.opm >= r.opm_p80 and r.d_opm <= 0:
                return "피크권"
            if r.op_yoy > 0 and r.d_opm > 0:
                return "확장"
            if r.op_yoy < 0 and r.d_opm < 0:
                return "수축"
            return "회복"

        d["phase"] = d.apply(_one, axis=1)
        d["avail"] = d["qend"] + pd.Timedelta(days=45)
        frames.append(d.dropna(subset=["phase"]))
    return pd.concat(frames, ignore_index=True)


def phase_series(phases: pd.DataFrame, index: pd.DatetimeIndex
                 ) -> tuple[pd.Series, pd.Series, dict]:
    """가용일 기준 두 종목 국면 점수 일별 시계열 + 일별 국면 라벨 + 최신 요약."""
    scores, firm_labels = [], []
    latest = {}
    for code, name in FIRMS.items():
        d = phases[phases.code == code].sort_values("avail")
        s = pd.Series(np.nan, index=index)
        lab = pd.Series([None] * len(index), index=index, dtype=object)
        for _, r in d.iterrows():
            mask = s.index >= r.avail
            s.loc[mask] = PHASE_SCORE[r.phase]
            lab.loc[mask] = f"{name} {r.phase}({int(r.year)}Q{int(r.q)})"
        scores.append(s)
        firm_labels.append(lab)
        if len(d):
            last = d.iloc[-1]
            latest[name] = {"q": f"{int(last.year)}Q{int(last.q)}", "phase": last.phase,
                            "opm": round(float(last.opm), 1),
                            "d_opm": round(float(last.d_opm), 1)}
    labels = pd.Series(
        [" · ".join(x for x in pair if x) or None
         for pair in zip(*(l.tolist() for l in firm_labels))],
        index=index, dtype=object)
    return pd.concat(scores, axis=1).mean(axis=1), labels, latest


def revision_breadth() -> pd.DataFrame:
    """퀀트데이터 주간 스냅샷 쌍에서 전종목 영업이익(E) 상향 비율(%)을 계산해 캐시 누적.
    같은 컨센서스 기준(결산기)끼리만 비교, ±0.5% 이내는 중립으로 제외."""
    hist = (pd.read_csv(REVB_CACHE, parse_dates=["date"])
            if os.path.exists(REVB_CACHE) else pd.DataFrame(columns=["date"]))
    files = sorted(f for f in glob.glob(os.path.join(QD_OUT, "퀀트데이터_*.csv"))
                   if re.fullmatch(r"퀀트데이터_\d{8}\.csv", os.path.basename(f)))
    snaps = []
    for f in files:
        try:
            d = pd.read_csv(f, usecols=["코드", "컨센서스기준", "영업이익(E,억)"],
                            dtype={"코드": str})
            d["코드"] = d["코드"].str.zfill(6)
            snaps.append((os.path.basename(f)[6:14], d.dropna()))
        except Exception as e:
            log(f"스냅샷 스킵 {os.path.basename(f)}: {e}")
    rows = []
    for (d0, a), (d1, b) in zip(snaps, snaps[1:]):
        m = a.merge(b, on="코드", suffixes=("_p", "_c"))
        m = m[(m["컨센서스기준_p"] == m["컨센서스기준_c"]) & (m["영업이익(E,억)_p"] != 0)]
        chg = m["영업이익(E,억)_c"] / m["영업이익(E,억)_p"] - 1
        up, dn = int((chg > 0.005).sum()), int((chg < -0.005).sum())
        if up + dn >= 30:
            rows.append({"date": pd.Timestamp(d1), "up_ratio": up / (up + dn) * 100,
                         "n_up": up, "n_dn": dn})
    fresh = pd.DataFrame(rows)
    if not fresh.empty:
        old = hist[~hist["date"].isin(fresh["date"])]
        hist = pd.concat([old, fresh]) if not old.empty else fresh
    hist = hist.sort_values("date").reset_index(drop=True)
    if not hist.empty:
        hist.to_csv(REVB_CACHE, index=False, encoding="utf-8")
        log(f"리비전 폭 {len(hist)}개 시점 (최근 {hist.iloc[-1]['date'].date()} "
            f"상향 {hist.iloc[-1]['up_ratio']:.0f}%)")
    return hist


# ──────────────────────────────────────────────────────────────────────────────
# 수집 ③④⑤ 국내 (전부 로컬 CSV)
# ──────────────────────────────────────────────────────────────────────────────
def load_market_history() -> pd.DataFrame:
    d = pd.read_csv(MARKET_HIST, parse_dates=["date"], index_col="date").sort_index()
    # 파생(누적·EMA) — breadth_build.derive 와 동일 규칙 중 필요분만
    adv, dec = d["all_adv"], d["all_dec"]
    tot = (adv + dec).replace(0, np.nan)
    rana = 1000 * (adv - dec) / tot
    d["mco"] = rana.ewm(span=19, adjust=False).mean() - rana.ewm(span=39, adjust=False).mean()
    d["adr20"] = adv.rolling(20).sum() / dec.rolling(20).sum().replace(0, np.nan) * 100
    nh, nl = d["all_nh"], d["all_nl"]
    d["nh_ratio"] = np.where(d["all_nhl_n"] > 0,
                             nh / (nh + nl).replace(0, np.nan) * 100, np.nan)
    log(f"market_history {len(d)}행 ({d.index[0].date()} ~ {d.index[-1].date()})")
    return d


def load_leverage() -> pd.DataFrame | None:
    try:
        cr = pd.read_csv(os.path.join(LEV_DIR, "credit_balance.csv"), encoding="utf-8-sig")
        mf = pd.read_csv(os.path.join(LEV_DIR, "market_funds.csv"), encoding="utf-8-sig")
    except FileNotFoundError:
        log("레버리지 데이터 없음 — 신용·유동성 팩터 일부 생략")
        return None
    for df in (cr, mf):
        df["date"] = pd.to_datetime(df["구 분"], format="%Y/%m/%d")
        df.sort_values("date", inplace=True)
    lev = pd.DataFrame({
        "date": cr["date"].values,
        "credit": pd.to_numeric(cr["신용거래융자_전체"], errors="coerce").values / 1e6,
    }).merge(pd.DataFrame({
        "date": mf["date"].values,
        "deposit": pd.to_numeric(
            mf["투자자예탁금 (장내파생상품 거래예수금제외)"], errors="coerce").values / 1e6,
        "liq_ratio": pd.to_numeric(mf["미수금 대비 반대매매비중(%)"], errors="coerce").values,
    }), on="date", how="outer").sort_values("date").set_index("date")
    lev["cd_ratio"] = lev["credit"] / lev["deposit"] * 100
    log(f"레버리지 {len(lev)}행 (~{lev.index[-1].date()})")
    return lev


# ──────────────────────────────────────────────────────────────────────────────
# 채점
# ──────────────────────────────────────────────────────────────────────────────
def dist200(s: pd.Series) -> pd.Series:
    """200일선 이격도(%)"""
    ma = s.rolling(200, min_periods=120).mean()
    return (s / ma - 1) * 100


def build_model(mh, glob_df, lev, rev, phase_sc, phase_latest, revb):
    idx = mh.index                                        # KRX 거래일 = 마스터 축
    sub = {}                                              # 하위지표 점수(방향 통일 후)
    raw = {}                                              # 하위지표 원시값(표시용)

    def align(s, limit=7):
        return s.reindex(idx.union(s.index)).ffill(limit=limit).reindex(idx)

    # ── ① Global Trend
    if glob_df is not None and not glob_df.empty:
        g = glob_df.sort_index()
        spx_d, sox_d = dist200(g["spx"]), dist200(g["sox"])
        vix, dxy = g.get("vix"), g.get("dxy")
        raw["spx_d200"], raw["sox_d200"] = align(spx_d), align(sox_d)
        raw["vix"], raw["dxy"] = align(vix), align(dxy)
        sub["global.spx"] = align(pct_rank(spx_d))
        sub["global.vix"] = 100 - align(pct_rank(vix))
        sub["global.dxy"] = 100 - align(pct_rank(dxy.ffill().pct_change(60) * 100))
    factors = pd.DataFrame(index=idx)
    factors["global"] = pd.concat(
        [sub[k] for k in sub if k.startswith("global.")], axis=1).mean(axis=1) \
        if any(k.startswith("global.") for k in sub) else np.nan

    # ── ② 반도체·이익 리비전
    if glob_df is not None and "sox" in glob_df.columns:
        sub["semi.sox"] = align(pct_rank(dist200(glob_df["sox"])))
    if glob_df is not None and {"ss_px", "hx_px"} <= set(glob_df.columns):
        semi_px = pd.concat([pct_rank(dist200(glob_df["ss_px"])),
                             pct_rank(dist200(glob_df["hx_px"]))], axis=1).mean(axis=1)
        sub["semi.px"] = align(semi_px)
        raw["ss_d200"] = align(dist200(glob_df["ss_px"]))
        raw["hx_d200"] = align(dist200(glob_df["hx_px"]))
    slopes = []
    for code, r in rev.items():
        if r is None:
            continue
        # 기울기(%) → 점수: -10%→0, 0→50, +10%→100 (선형, 클립)
        sc = (50 + r["slope"] * 5).clip(0, 100)
        slopes.append(align(sc, limit=10))
        raw[f"tp_slope_{code}"] = align(r["slope"], limit=10)
    if slopes:
        sub["semi.revision"] = pd.concat(slopes, axis=1).mean(axis=1)
    if phase_sc is not None:
        sub["semi.phase"] = phase_sc
    if revb is not None and not revb.empty:
        rb = pd.Series(revb["up_ratio"].values, index=revb["date"])
        sub["semi.revbreadth"] = align(rb, limit=15)      # 주간 스냅샷 → 15일 유지
        raw["rev_breadth"] = sub["semi.revbreadth"]
    factors["semi"] = pd.concat(
        [sub[k] for k in sub if k.startswith("semi.")], axis=1).mean(axis=1)

    # ── ③ Market Breadth  (breadth_build 'trend' 구성과 동일)
    sub["breadth.ma200"] = pct_rank(mh["all_ma200"])
    sub["breadth.ma50"] = pct_rank(mh["all_ma50"])
    sub["breadth.adr20"] = pct_rank(mh["adr20"])
    sub["breadth.nh"] = pct_rank(mh["nh_ratio"])
    sub["breadth.mco"] = pct_rank(mh["mco"])
    for k in ("all_ma200", "all_ma50", "adr20", "nh_ratio", "mco"):
        raw[k] = mh[k]
    factors["breadth"] = pd.concat(
        [sub[k] for k in sub if k.startswith("breadth.")], axis=1).mean(axis=1)

    # ── ④ 신용·유동성
    if lev is not None:
        dep = align(lev["deposit"].ffill())
        raw["deposit_mom"] = dep.pct_change(20) * 100
        sub["liq.deposit"] = pct_rank(raw["deposit_mom"])
        sub["liq.liqratio"] = 100 - pct_rank(align(lev["liq_ratio"]).rolling(5).mean())
        sub["liq.cdratio"] = 100 - pct_rank(align(lev["cd_ratio"]))
        raw["deposit"], raw["credit"] = dep, align(lev["credit"])
        raw["liq_ratio"], raw["cd_ratio"] = align(lev["liq_ratio"]), align(lev["cd_ratio"])
    if glob_df is not None and "hyoas" in glob_df.columns:
        hy = align(glob_df["hyoas"].dropna())
        sub["liq.hyoas"] = 100 - pd.concat(
            [pct_rank(hy), pct_rank(hy.diff(63))], axis=1).mean(axis=1)
        raw["hyoas"] = hy
    factors["liq"] = pd.concat(
        [sub[k] for k in sub if k.startswith("liq.")], axis=1).mean(axis=1) \
        if any(k.startswith("liq.") for k in sub) else np.nan

    # ── ⑤ Euphoria (역방향: 과열 백분위 → 100-heat)
    heat_parts = [pct_rank(mh["penny_share"]), pct_rank(pd.to_numeric(mh["cap_up"])),
                  pct_rank(mh["all_turnover"])]
    if lev is not None:
        cr_mom = align(lev["credit"].ffill()).pct_change(20) * 100
        heat_parts.append(pct_rank(cr_mom))
        raw["credit_mom"] = cr_mom
    heat = pd.concat(heat_parts, axis=1).mean(axis=1)
    sub["euphoria.heat"] = 100 - heat
    factors["euphoria"] = sub["euphoria.heat"]
    raw["euphoria_heat"] = heat

    # ── 평활: 일별 백분위 노이즈로 국면이 하루 단위로 뒤집히지 않도록 5일 이동평균
    for k in WEIGHTS:
        factors[k] = factors[k].rolling(5, min_periods=1).mean()

    # ── 종합 (가용 팩터 가중 재정규화)
    w = pd.Series(WEIGHTS, dtype=float)
    fv = factors[list(WEIGHTS)]
    wsum = fv.notna().mul(w, axis=1).sum(axis=1)
    factors["composite"] = fv.mul(w, axis=1).sum(axis=1, min_count=1) / wsum.replace(0, np.nan)
    return factors, sub, raw


def regime_of(score: float):
    for lo, label, band, cls in REGIMES:
        if score >= lo:
            return {"label": label, "band": band, "cls": cls}
    return {"label": "Bear", "band": "현금·헤지 확대", "cls": "critical"}


# ──────────────────────────────────────────────────────────────────────────────
# 체크리스트 + 출력
# ──────────────────────────────────────────────────────────────────────────────
# (팩터, 지표명, 점수 키, 값 파트) — 값 파트는 (라벨, raw 키, 소수, 접미) 목록.
# "PHASE" 는 일별 국면 라벨 시계열 사용. 날짜 선택 시 JS 가 시계열에서 그 시점을 찾는다.
CHECKLIST_SPEC = [
    ("① Global", "S&P500 200일선 이격도", "global.spx", [["", "spx_d200", 1, "%"]]),
    ("① Global", "VIX (낮을수록 우호)", "global.vix", [["", "vix", 1, ""]]),
    ("① Global", "달러인덱스 (60일 모멘텀 채점, 약달러=우호)", "global.dxy", [["", "dxy", 1, ""]]),
    ("② 반도체", "SOX 200일선 이격도", "semi.sox", [["", "sox_d200", 1, "%"]]),
    ("② 반도체", "목표주가 컨센서스 60일 기울기", "semi.revision",
     [["삼성", "tp_slope_005930", 1, "%"], ["SK", "tp_slope_000660", 1, "%"]]),
    ("② 반도체", "메모리 사이클 국면", "semi.phase", "PHASE"),
    ("② 반도체", "전종목 영업이익(E) 상향 비율", "semi.revbreadth", [["", "rev_breadth", 0, "%"]]),
    ("② 반도체", "삼전·하이닉스 200일선 이격도", "semi.px",
     [["삼성", "ss_d200", 1, "%"], ["SK", "hx_d200", 1, "%"]]),
    ("③ Breadth", "MA200 위 종목 비율", "breadth.ma200", [["", "all_ma200", 1, "%"]]),
    ("③ Breadth", "MA50 위 종목 비율", "breadth.ma50", [["", "all_ma50", 1, "%"]]),
    ("③ Breadth", "ADR20 (20일 등락비)", "breadth.adr20", [["", "adr20", 0, ""]]),
    ("③ Breadth", "52주 신고가 비율 (신고/신고+신저)", "breadth.nh", [["", "nh_ratio", 0, "%"]]),
    ("③ Breadth", "맥클렐런 오실레이터", "breadth.mco", [["", "mco", 0, ""]]),
    ("④ 유동성", "예탁금 20일 증가율", "liq.deposit", [["", "deposit_mom", 1, "%"]]),
    ("④ 유동성", "반대매매 비중 (높을수록 경계)", "liq.liqratio", [["", "liq_ratio", 1, "%"]]),
    ("④ 유동성", "신용융자/예탁금 (높을수록 취약)", "liq.cdratio", [["", "cd_ratio", 1, "%"]]),
    ("④ 유동성", "美 하이일드 OAS", "liq.hyoas", [["", "hyoas", 0, "bp"]]),
    ("⑤ Euphoria", "과열 종합 (저가주·상한가·회전율·신용증가)", "euphoria.heat",
     [["열기", "euphoria_heat", 0, "/100"]]),
]


def assemble(mh, factors, sub, raw, rev, phase_latest, phase_labels, revb, lev):
    recent = factors.tail(CHART_ROWS)
    dates = [f"{d:%Y-%m-%d}" for d in recent.index]
    mhr = mh.reindex(recent.index)

    def ser(s, nd=1):
        return _round(s.reindex(recent.index) if s is not None else
                      pd.Series(index=recent.index, dtype=float), nd)

    comp_last = recent["composite"].dropna()
    score_now = round(float(comp_last.iloc[-1]), 1) if len(comp_last) else None
    reg = regime_of(score_now) if score_now is not None else \
        {"label": "—", "band": "—", "cls": "warning"}

    factor_last = {}
    for k in WEIGHTS:
        s = recent[k].dropna()
        factor_last[k] = round(float(s.iloc[-1]), 1) if len(s) else None

    # 목표주가 평활(시작=100 인덱스) — 종목별 시작일이 달라도 공통 날짜축으로
    # 정렬해서 내보낸다 (축이 어긋나면 차트가 선을 잇지 못하고 점만 남는다)
    tp_idx = {}
    smooths = {c: r["smooth"].dropna() for c, r in rev.items()
               if r and len(r["smooth"].dropna())}
    if smooths:
        axis = pd.date_range(min(s.index.min() for s in smooths.values()),
                             max(s.index.max() for s in smooths.values()))[::3]
        tp_dates = [f"{d:%Y-%m-%d}" for d in axis]
        for code, sm in smooths.items():
            aligned = sm.reindex(axis).ffill()
            tp_idx[FIRMS[code]] = {"dates": tp_dates,
                                   "values": _round(aligned / sm.iloc[0] * 100, 1)}

    data = {
        "generated_at": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
        "asof": dates[-1] if dates else "—",
        "regime": {"score": score_now, **reg,
                   "factors": {k: {"score": factor_last[k], "weight": WEIGHTS[k]}
                               for k in WEIGHTS},
                   "phase": phase_latest},
        "series": {
            "dates": dates,
            "composite": ser(recent["composite"]),
            "kospi": ser(mhr.get("kospi_close"), 2),
            "factors": {k: ser(recent[k]) for k in WEIGHTS},
        },
        "global": {
            "spx_d200": ser(raw.get("spx_d200")), "sox_d200": ser(raw.get("sox_d200")),
            "vix": ser(raw.get("vix")), "dxy": ser(raw.get("dxy")),
        },
        "semi": {
            "target_idx": tp_idx,
            "rev_breadth": ({"dates": [f"{d:%Y-%m-%d}" for d in revb["date"]],
                             "values": _round(revb["up_ratio"], 0)}
                            if revb is not None and not revb.empty else None),
            "ss_d200": ser(raw.get("ss_d200")), "hx_d200": ser(raw.get("hx_d200")),
        },
        "breadth": {
            "ma20": ser(mhr["all_ma20"]), "ma50": ser(mhr["all_ma50"]),
            "ma200": ser(mhr["all_ma200"]), "mco": ser(mh["mco"].reindex(recent.index)),
            "nh": ser(mhr["all_nh"], 0), "nl": ser(mhr["all_nl"], 0),
            "adr20": ser(mh["adr20"].reindex(recent.index)),
        },
        "liq": {
            "credit": ser(raw.get("credit"), 2), "deposit": ser(raw.get("deposit"), 2),
            "liq_ratio": ser(raw.get("liq_ratio"), 2), "cd_ratio": ser(raw.get("cd_ratio"), 2),
            "hyoas": ser(raw.get("hyoas"), 0),
        },
        "euphoria": {
            "penny_share": ser(mhr["penny_share"], 2),
            "cap_up": ser(mhr["cap_up"], 0),
            "turnover": ser(mhr["all_turnover"], 3),
            "credit_mom": ser(raw.get("credit_mom")),
            "heat": ser(raw.get("euphoria_heat")),
        },
        # ── 날짜 선택(타임머신) 렌더용: 체크리스트 스펙 + 하위지표 점수/원시값 시계열
        "checklist": [{"pillar": p, "name": n, "key": k, "parts": parts}
                      for p, n, k, parts in CHECKLIST_SPEC],
        "sub_series": {k: ser(s) for k, s in sub.items()},
        "raw_series": {k: ser(raw[k], 2) for k in
                       {pt[1] for _, _, _, parts in CHECKLIST_SPEC
                        if parts != "PHASE" for pt in parts} if k in raw},
        "phase_labels": [None if pd.isna(v) else v
                         for v in phase_labels.reindex(recent.index).tolist()],
        "meta": {
            "weights": WEIGHTS,
            "history_note": ("백분위 기준: 글로벌 3년 · Breadth 2025-02~ · "
                             "레버리지 약 3개월 — 히스토리가 짧은 지표는 점수 변동이 클 수 있음"),
        },
    }
    return data


def append_score_log(data):
    """일별 종합/팩터 점수를 CSV 로 남긴다(재계산으로 과거 점수가 흔들려도 당시 기록 보존)."""
    if data["regime"]["score"] is None:
        return
    row = {"date": data["asof"], "composite": data["regime"]["score"],
           **{k: v["score"] for k, v in data["regime"]["factors"].items()}}
    hist = (pd.read_csv(SCORE_LOG) if os.path.exists(SCORE_LOG)
            else pd.DataFrame(columns=list(row)))
    old = hist[hist["date"] != row["date"]]
    hist = pd.concat([old, pd.DataFrame([row])]) if not old.empty else pd.DataFrame([row])
    hist.sort_values("date").to_csv(SCORE_LOG, index=False, encoding="utf-8")


def render(data):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"대시보드 생성 완료 → {OUTPUT}")
    # reports/ 배포 사본 — 그쪽에선 AI 대시보드 파일명이 aicycle.html
    if os.path.isdir(os.path.dirname(DEPLOY)):
        with open(DEPLOY, "w", encoding="utf-8") as f:
            f.write(html.replace('href="dashboard.html"', 'href="aicycle.html"'))
        log(f"배포 사본 저장 → {DEPLOY}")


def main():
    ap = argparse.ArgumentParser(description="한국 시장 사이클 모델")
    ap.add_argument("--offline", action="store_true", help="네트워크 수집 생략(캐시만)")
    args = ap.parse_args()

    mh = load_market_history()
    glob_df = fetch_global(args.offline)
    lev = load_leverage()
    cons = load_consensus()
    rev = target_revision(cons)
    phases = load_memory_phase()
    phase_sc, phase_labels, phase_latest = phase_series(phases, mh.index)
    revb = revision_breadth()

    factors, sub, raw = build_model(mh, glob_df, lev, rev, phase_sc, phase_latest, revb)
    data = assemble(mh, factors, sub, raw, rev, phase_latest, phase_labels, revb, lev)
    append_score_log(data)
    render(data)

    r = data["regime"]
    log(f"종합 {r['score']} → {r['label']} (권장 주식비중 {r['band']})")
    for k, v in r["factors"].items():
        log(f"  {k:<9} {v['score']} (w{v['weight']})")


if __name__ == "__main__":
    main()
