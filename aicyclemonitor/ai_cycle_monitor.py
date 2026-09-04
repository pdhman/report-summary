#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 AI 사이클 리스크 모니터  (ai_cycle_monitor.py)
================================================================================
"호황은 어떻게 끝나는가" — 3가지 종료 경로를 무료 데이터로 추적하는 모니터.

  [1] 공급과잉 (1999년형) : GPU 임대가격(500.farm, H100·B200 '임대중' 중앙값),
                            데이터센터 REIT(DLR/EQIX), 공실률(CBRE → 수동 입력)
  [2] 수요둔화 (2022년형) : Hyperscaler(MSFT/GOOGL/AMZN/META) 분기 매출 YoY,
                            클라우드 부문(Azure/AWS/GCP) YoY (실적발표 → 수동 입력)
  [3] 레버리지 (2008년형) : BBB·HY 신용 스프레드(FRED), 네오클라우드 주가
                            (CRWV/NBIS/IREN/APLD), 오라클(ORCL) 낙폭,
                            사모신용/BDC 프록시(BIZD/PBDC)

사용법
------
  pip install yfinance pandas requests

  python ai_cycle_monitor.py            # 실데이터 수집 → dashboard.html 생성
  python ai_cycle_monitor.py --sample   # 네트워크 없이 예시 데이터로 생성(레이아웃 확인용)

  * 최초 실행 시 manual_data.json 이 생성됩니다. 공실률·클라우드 부문 YoY 등
    "무료 API가 없는" 항목은 이 파일에 분기/반기마다 직접 숫자를 채워 넣으세요.
  * GPU 임대가격과 분기 매출은 실행할 때마다 로컬 CSV에 누적 저장되어
    실행 횟수가 쌓일수록 히스토리가 길어집니다. (cron/작업스케줄러로 매일 실행 권장)

출력: dashboard.html (단일 파일, 브라우저로 열면 됨)
================================================================================
"""

import argparse
import csv
import io
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MANUAL_JSON = os.path.join(BASE_DIR, "manual_data.json")
GPU_HISTORY_CSV = os.path.join(BASE_DIR, "gpu_rental_history.csv")
REVENUE_CACHE_CSV = os.path.join(BASE_DIR, "revenue_history.csv")
CRWV_CAPEX_CSV = os.path.join(BASE_DIR, "crwv_capex_history.csv")
HS_CAPEX_CSV = os.path.join(BASE_DIR, "hyperscaler_capex_history.csv")
ORCL_RPO_CSV = os.path.join(BASE_DIR, "orcl_rpo_history.csv")
CAPEX_CIK = {"GOOGL": "0001652044", "MSFT": "0000789019", "AMZN": "0001018724",
             "META": "0001326801", "ORCL": "0001341439"}
CAPEX_CONCEPTS = ["PaymentsToAcquirePropertyPlantAndEquipment",
                  "PaymentsToAcquireProductiveAssets",
                  "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"]
OUTPUT_HTML = os.path.join(BASE_DIR, "dashboard.html")

# ──────────────────────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────────────────────
NEOCLOUD = ["CRWV", "NBIS", "IREN", "APLD"]       # 네오클라우드 (GPU 클라우드)
GPU_MODELS = ["H100 SXM", "B200"]                  # 임대가 추적 대상 (구세대 + 최신세대)
DC_REIT = ["DLR", "EQIX"]                          # 데이터센터 REIT
PRIVATE_CREDIT = ["BIZD", "PBDC"]                  # BDC/사모신용 상장 프록시
HYPERSCALER = ["MSFT", "GOOGL", "AMZN", "META"]
ORACLE = "ORCL"
MEMORY_TICKER = "MU"                               # HBM 주문 프록시 (회계분기 2·5·8·11월 — 조기 신호)
FRED_SERIES = {
    "AA_OAS": "BAMLC0A2CAA",      # ICE BofA AA 회사채 OAS (MSFT/GOOGL/AMZN/META=AA급 프록시)
    "BBB_OAS": "BAMLC0A4CBBB",    # ICE BofA BBB 회사채 OAS (오라클=BBB 프록시)
    "HY_OAS": "BAMLH0A0HYM2",     # ICE BofA 하이일드 OAS
    "CCC_OAS": "BAMLH0A3HYC",     # ICE BofA CCC 이하 OAS (최약체 차입 창구 — 저신용 조달 용이성)
}
PRICE_LOOKBACK = "2y"

# 경계 임계값 (신호등 판정 기준 — 취향에 맞게 조정)
TH = {
    "gpu_rent_chg_90d": -20.0,     # GPU 임대중 중앙값 90일 변화율(%) — H100·B200 공통 임계값
    "dc_reit_chg_180d": -15.0,     # DC REIT 6개월 수익률(%) 이 값 이하 → 경계
    "vacancy_pct": 5.0,            # 데이터센터 공실률(%) 이 값 이상 → 경계
    "hs_yoy_consec_slowdown": 2,   # 합산 매출 YoY 연속 둔화 분기 수 → 경계
    "cloud_yoy_floor": 20.0,       # 클라우드 부문 YoY(%) 이 값 미만 → 경계
    "nvda_dc_yoy_floor": 20.0,     # NVIDIA DC 매출 YoY(%) 이 값 미만 → 경계 (국면 악화 확정)
    "nvda_dc_qoq": 0.0,            # NVIDIA DC 매출 QoQ(%) 이 값 이하 → 경계 (성장 정지 조기 경보)
    "capex_runrate_vs_ttm": 0.0,   # 5사 capex 런레이트(최근2Q×2)가 직전 4분기 합 이하(가속 멈춤) → 경계
    "capex_consensus_rev": -5.0,   # 5사 capex 내년 컨센서스가 직전 집계 대비 이 % 이하(하향 수정) → 경계
    "bbb_oas_level": 200.0,        # BBB OAS(bp) 이 값 이상 → 경계
    "bbb_oas_chg_90d": 50.0,       # BBB OAS 90일 상승폭(bp) 이 값 이상 → 경계
    "aa_oas_chg_90d": 30.0,        # AA OAS 90일 상승폭(bp) 이 값 이상 → 경계 (하이퍼스케일러급)
    "orcl_cds_level": 200.0,       # 오라클 5Y CDS(bp, 수동 스냅샷) 이 값 이상 → 경계
    "hy_oas_level": 450.0,         # 하이일드 OAS(bp) 이 값 이상 → 경계 (신용시장 전반 스트레스)
    "ccc_oas_chg_90d": 150.0,      # CCC OAS 90일 상승폭(bp) 이 값 이상 → 경계 (최약체 조달창구 경색)
    "orcl_rpo_qoq": 0.0,           # 오라클 RPO QoQ(%) 이 값 이하 → 경계 (계약 취소/전환 실패)
    "mu_slowdown_q": 2,            # 마이크론 매출 YoY 연속 둔화 분기 이상 → 경계 (HBM 주문 프록시)
    "neocloud_drawdown": -50.0,    # 네오클라우드 평균 52주 낙폭(%) → 경계
    "crwv_capex_yoy": 0.0,         # CRWV 분기 capex YoY(%) 이 값 이하(감소 전환) → 경계
    "orcl_drawdown": -40.0,        # 오라클 52주 낙폭(%) → 경계
    "bdc_chg_180d": -15.0,         # BDC 프록시 6개월 수익률(%) → 경계
}


# ──────────────────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[monitor] {msg}")


def pct(a, b):
    """b 대비 a 의 % 변화"""
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1.0) * 100.0, 1)


def series_to_lists(s):
    """pandas Series(DatetimeIndex) → (dates[], values[])"""
    s = s.dropna()
    return [d.strftime("%Y-%m-%d") for d in s.index], [round(float(v), 2) for v in s.values]


def change_over_days(s, days):
    """일별 Series 에서 최근값 vs days일 전 값의 % 변화"""
    s = s.dropna()
    if len(s) < 2:
        return None
    last = s.iloc[-1]
    cutoff = s.index[-1] - timedelta(days=days)
    past = s[s.index <= cutoff]
    if past.empty:
        return None
    return pct(float(last), float(past.iloc[-1]))


def drawdown_from_52w_high(s):
    s = s.dropna()
    if s.empty:
        return None
    recent = s[s.index >= s.index[-1] - timedelta(days=365)]
    return pct(float(s.iloc[-1]), float(recent.max()))


# ──────────────────────────────────────────────────────────────────────────────
# 수동 입력 파일 (무료 API가 없는 데이터)
# ──────────────────────────────────────────────────────────────────────────────
MANUAL_TEMPLATE = {
    "_설명": {
        "datacenter_vacancy": "CBRE 'North America Data Center Trends' 반기 보고서(무료 공개)에서 공실률을 옮겨 적으세요. https://www.cbre.com/insights",
        "cloud_segment_yoy": "분기 실적발표에서 Azure/AWS/Google Cloud 매출 YoY(%)를 옮겨 적으세요. 각사 IR 페이지 또는 실적 기사.",
        "notes": "값을 채우면 대시보드의 해당 차트가 자동으로 갱신됩니다. 비워두면 '수동 입력 대기'로 표시됩니다.",
    },
    "datacenter_vacancy": [
        # {"date": "2025-H2", "region": "북미 1차 시장", "vacancy_pct": 1.6, "source": "CBRE"},
    ],
    "cloud_segment_yoy": {
        # "2025Q4": {"Azure": 31.0, "AWS": 17.0, "Google Cloud": 30.0},
    },
}


def load_manual():
    if not os.path.exists(MANUAL_JSON):
        with open(MANUAL_JSON, "w", encoding="utf-8") as f:
            json.dump(MANUAL_TEMPLATE, f, ensure_ascii=False, indent=2)
        log(f"수동 입력 템플릿 생성: {MANUAL_JSON} (공실률/클라우드 YoY를 채워 넣으세요)")
    with open(MANUAL_JSON, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# 데이터 수집 (실데이터 모드)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_prices(tickers):
    import yfinance as yf
    log(f"주가 수집: {', '.join(tickers)}")
    df = yf.download(tickers, period=PRICE_LOOKBACK, interval="1d",
                     auto_adjust=True, progress=False)["Close"]
    if hasattr(df, "columns") and df.__class__.__name__ == "Series":
        df = df.to_frame(tickers[0])
    return df


_FRED_DIRECT_OK = True  # 직접 접속이 한 번 실패하면 이후 시리즈는 바로 프록시로


def _fred_csv_text(series_id):
    """FRED fredgraph.csv — 직접 접속이 막힌 네트워크(해외 IP 차단)에서는
    미국 경유 공개 프록시(allorigins.win)로 폴백"""
    global _FRED_DIRECT_OK
    import requests
    import urllib.parse
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    if _FRED_DIRECT_OK:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.text
        except Exception as e:
            _FRED_DIRECT_OK = False
            log(f"FRED 직접 접속 실패({type(e).__name__}) → allorigins 프록시 폴백")
    # allorigins는 간헐적으로 5xx를 반환 → 백오프를 두고 raw/get 엔드포인트 교차 재시도
    import time
    q = urllib.parse.quote(url, safe="")
    last = None
    # 시리즈당 최대 ~2.5분(45초×3회+백오프) — 실패해도 로컬 캐시로 폴백되므로 오래 붙들지 않음
    for attempt, endpoint in enumerate(["raw", "get", "raw"]):
        try:
            r = requests.get(f"https://api.allorigins.win/{endpoint}?url={q}",
                             timeout=45, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            text = r.json().get("contents", "") if endpoint == "get" else r.text
            if text.startswith("data:"):  # /get은 base64 데이터 URI로 감싸서 반환
                import base64
                text = base64.b64decode(text.split(",", 1)[1]).decode("utf-8")
            if not text or "," not in text.splitlines()[0] or text.lstrip().startswith("<"):
                raise ValueError(f"프록시 응답이 CSV가 아님: {text[:80]!r}")
            return text
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last


def fetch_fred(series_id):
    """FRED — API 키 불필요한 fredgraph.csv 엔드포인트 (+프록시 폴백)"""
    import pandas as pd
    df = pd.read_csv(io.StringIO(_fred_csv_text(series_id)))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["value"].dropna()
    cutoff = s.index[-1] - timedelta(days=365 * 2)
    return s[s.index >= cutoff]


def _gpu_stats_500farm():
    """500.farm(vast.ai 통계 미러) — 모델별 verified 중앙값($/GPU/hr).
    '임대중(rented)'이 체결가 근사(1차 지표), '가용(available)'은 잔존 매물 호가(참고).
    vast.ai 직접 API는 로컬·GitHub 러너 모두 403이라 미러가 기본 소스."""
    import requests
    r = requests.get("https://500.farm/vastai-exporter/gpu-stats", timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = {}
    for m in r.json().get("models", []):
        if m.get("name") in GPU_MODELS:
            st = m.get("stats", {})

            def med(sec):
                for k in ("verified", "all", "unverified"):
                    rows = st.get(sec, {}).get(k) or []
                    if rows and rows[0].get("price_median"):
                        return round(float(rows[0]["price_median"]), 3)
                return None

            out[m["name"]] = (med("rented"), med("available"))
    if not out:
        raise ValueError("대상 GPU 모델을 찾지 못함")
    return out


def fetch_gpu_rental():
    """모델별(H100 SXM·B200) 임대중/가용 중앙값을 CSV에 누적.
    같은 날짜·모델 행은 최신 값으로 덮어쓴다. 수집 실패 시 기존 히스토리만 사용."""
    hist = read_gpu_history()
    try:
        stats = _gpu_stats_500farm()
        today = date.today().isoformat()
        for model, vals in stats.items():
            hist[(today, model)] = list(vals)
        with open(GPU_HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for (d, model), (rented, avail) in sorted(hist.items()):
                w.writerow([d, model,
                            "" if rented is None else rented,
                            "" if avail is None else avail])
        log("GPU 임대가(500.farm): " + ", ".join(
            f"{m} 임대중 ${r:.2f}/가용 ${a:.2f}" if r is not None and a is not None
            else f"{m} 일부 누락" for m, (r, a) in stats.items()))
    except Exception as e:
        log(f"500.farm 수집 실패(기존 히스토리 사용): {e}")
    return hist


def read_gpu_history():
    """CSV(date,model,rented,avail) → {(date, model): [rented, avail]}"""
    out = {}
    if not os.path.exists(GPU_HISTORY_CSV):
        return out
    with open(GPU_HISTORY_CSV, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 4:
                out[(row[0], row[1])] = [float(row[2]) if row[2] else None,
                                         float(row[3]) if row[3] else None]
    return out


def fetch_hyperscaler_revenue():
    """yfinance 분기 손익계산서에서 Total Revenue를 뽑아 로컬 CSV에 누적.
    (yfinance는 최근 5~6개 분기만 주므로, 누적 캐시로 히스토리를 길게 유지)"""
    import yfinance as yf
    cache = {}  # {(ticker, quarter_end): revenue}
    if os.path.exists(REVENUE_CACHE_CSV):
        with open(REVENUE_CACHE_CSV, encoding="utf-8") as f:
            for t, q, v in csv.reader(f):
                cache[(t, q)] = float(v)
    for t in HYPERSCALER + [MEMORY_TICKER]:
        try:
            inc = yf.Ticker(t).quarterly_income_stmt
            if inc is None or inc.empty or "Total Revenue" not in inc.index:
                continue
            for col, val in inc.loc["Total Revenue"].items():
                if val == val and val is not None:  # not NaN
                    cache[(t, col.strftime("%Y-%m-%d"))] = float(val)
        except Exception as e:
            log(f"{t} 분기매출 수집 실패: {e}")
    with open(REVENUE_CACHE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for (t, q), v in sorted(cache.items()):
            w.writerow([t, q, v])
    return cache


def _sec_quarterly_series(cik, concepts):
    """SEC XBRL 현금흐름 항목 → {분기말: 분기값}.
    10-Q 현금흐름은 회계연도 시작 기점 YTD 누적이라, 같은 시작일을 가진 값들 중
    분기말이 한 분기 간격인 쌍의 차분으로 분기값을 복원한다
    (회계연도가 1월 시작이 아닌 MSFT·ORCL 도 동일하게 처리됨)."""
    import requests
    entries = []
    for c in concepts:  # 태그를 갈아탄 회사(예: AMZN 2017)를 위해 전부 병합
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{c}.json",
                         timeout=30, headers={"User-Agent": "aicyclemonitor personal research"})
        if r.status_code == 200:
            entries += r.json().get("units", {}).get("USD", [])
    best = {}  # (start, end) -> (filed, val) — 같은 기간은 최신 공시 우선
    for e in entries:
        if e.get("start") and e.get("end") and e.get("val") is not None:
            k = (e["start"], e["end"])
            if k not in best or e.get("filed", "") > best[k][0]:
                best[k] = (e.get("filed", ""), float(e["val"]))
    by_start = {}
    for (s, en), (_, v) in best.items():
        by_start.setdefault(s, {})[en] = v
    q = {}
    for s, ends in by_start.items():
        es = sorted(ends)
        for i, en in enumerate(es):
            dur = (date.fromisoformat(en) - date.fromisoformat(s)).days
            if 80 <= dur <= 100:  # 분기 단독값
                q[en] = ends[en]
            elif i > 0 and 80 <= (date.fromisoformat(en) - date.fromisoformat(es[i - 1])).days <= 100:
                q[en] = ends[en] - ends[es[i - 1]]  # YTD 차분
    return q


def fetch_crwv_capex():
    """CoreWeave 분기 capex를 SEC XBRL에서 수집해 CSV 누적. 실패 시 기존 캐시 사용."""
    cache = {}
    if os.path.exists(CRWV_CAPEX_CSV):
        with open(CRWV_CAPEX_CSV, encoding="utf-8") as f:
            for d, v in csv.reader(f):
                cache[d] = float(v)
    try:
        cache.update(_sec_quarterly_series("0001769628", CAPEX_CONCEPTS[:1]))
        with open(CRWV_CAPEX_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for d, v in sorted(cache.items()):
                w.writerow([d, v])
        if cache:
            last = sorted(cache)[-1]
            log(f"CRWV capex(SEC): 최근 {last} ${cache[last]/1e9:.1f}B, 총 {len(cache)}개 분기")
    except Exception as e:
        log(f"CRWV capex 수집 실패(기존 캐시 사용): {e}")
    return cache


def fetch_hyperscaler_capex():
    """5대 하이퍼스케일러 분기 capex(SEC)를 CSV에 누적 → {(ticker, 분기말): 값}"""
    cache = {}
    if os.path.exists(HS_CAPEX_CSV):
        with open(HS_CAPEX_CSV, encoding="utf-8") as f:
            for t, d, v in csv.reader(f):
                cache[(t, d)] = float(v)
    for t, cik in CAPEX_CIK.items():
        try:
            for en, v in _sec_quarterly_series(cik, CAPEX_CONCEPTS).items():
                if en >= "2023-01-01":
                    cache[(t, en)] = v
        except Exception as e:
            log(f"{t} capex 수집 실패(캐시 사용): {e}")
    with open(HS_CAPEX_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for (t, d), v in sorted(cache.items()):
            w.writerow([t, d, v])
    return cache


def fetch_orcl_rpo():
    """오라클 RPO(잔여수행의무 잔고, 시점값)를 SEC XBRL에서 수집해 CSV 누적.
    장기 컴퓨팅 계약의 총량 — 증가 정체·감소 전환이 '계약 취소/사용률 하락'의 정량 프록시."""
    cache = {}
    if os.path.exists(ORCL_RPO_CSV):
        with open(ORCL_RPO_CSV, encoding="utf-8") as f:
            for d, v in csv.reader(f):
                cache[d] = float(v)
    try:
        import requests
        r = requests.get("https://data.sec.gov/api/xbrl/companyconcept/CIK0001341439"
                         "/us-gaap/RevenueRemainingPerformanceObligation.json",
                         timeout=30, headers={"User-Agent": "aicyclemonitor personal research"})
        r.raise_for_status()
        best = {}  # end(시점) -> (filed, val)
        for e in r.json().get("units", {}).get("USD", []):
            en = e.get("end")
            if en and e.get("val") is not None and (not e.get("start") or e["start"] == en):
                if en not in best or e.get("filed", "") > best[en][0]:
                    best[en] = (e.get("filed", ""), float(e["val"]))
        for en, (_, v) in best.items():
            if en >= "2023-01-01":
                cache[en] = v
        with open(ORCL_RPO_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for d, v in sorted(cache.items()):
                w.writerow([d, v])
        if cache:
            last = sorted(cache)[-1]
            log(f"ORCL RPO(SEC): 최근 {last} ${cache[last]/1e9:.0f}B, 총 {len(cache)}개 분기")
    except Exception as e:
        log(f"ORCL RPO 수집 실패(기존 캐시 사용): {e}")
    return cache


def revenue_yoy_series(cache, tickers=HYPERSCALER):
    """분기 매출 캐시 → 티커별 YoY 시계열 {ticker: {"quarters":[...], "yoy":[...]}}"""
    out = {}
    for t in tickers:
        items = sorted((q, v) for (tk, q), v in cache.items() if tk == t)
        yoy_q, yoy_v = [], []
        bydate = dict(items)
        for q, v in items:
            d = datetime.strptime(q, "%Y-%m-%d").date()
            # 4분기(약 1년) 전 분기말을 ±20일 오차로 탐색
            prev = [pv for pq, pv in items
                    if abs((d - datetime.strptime(pq, "%Y-%m-%d").date()).days - 365) <= 20]
            if prev:
                yoy_q.append(f"{d.year}Q{(d.month - 1)//3 + 1}")
                yoy_v.append(pct(v, prev[0]))
        out[t] = {"quarters": yoy_q, "yoy": yoy_v}
    return out


def collect_live():
    import pandas as pd
    manual = load_manual()
    all_tickers = NEOCLOUD + DC_REIT + PRIVATE_CREDIT + [ORACLE]
    px = fetch_prices(all_tickers)

    # ── 레버리지: 신용 스프레드 (성공 시 로컬 캐시 저장, 실패 시 캐시 사용) ──
    spreads = {}
    for name, sid in FRED_SERIES.items():
        cache_path = os.path.join(BASE_DIR, f"fred_{name.lower()}_history.csv")
        try:
            s = fetch_fred(sid)
            d, v = series_to_lists(s * 100)  # % → bp
            spreads[name] = {"dates": d, "values": v}
            with open(cache_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(zip(d, v))
            log(f"FRED {sid}: 최근 {v[-1]:.0f}bp")
        except Exception as e:
            log(f"FRED {sid} 실패: {e}")
            if os.path.exists(cache_path):
                with open(cache_path, encoding="utf-8") as f:
                    rows = [r for r in csv.reader(f) if r]
                if rows:
                    spreads[name] = {"dates": [r[0] for r in rows],
                                     "values": [float(r[1]) for r in rows]}
                    log(f"FRED {sid}: 지난 캐시 사용 (마지막 {rows[-1][0]})")

    # ── 공급과잉: GPU 임대가(모델별 임대중/가용) + DC REIT ──
    gpu_hist = fetch_gpu_rental()
    gpu = {}
    for model in GPU_MODELS:
        items = sorted((d, v) for (d, mo), v in gpu_hist.items() if mo == model)
        if items:
            gpu[model] = {"dates": [d for d, _ in items],
                          "rented": [v[0] for _, v in items],
                          "avail": [v[1] for _, v in items]}

    def indexed(df_cols):
        """여러 종목을 시작=100으로 지수화 (이중축 회피)"""
        out = {}
        sub = px[df_cols].dropna(how="all")
        for c in df_cols:
            s = sub[c].dropna()
            if s.empty:
                continue
            d, v = series_to_lists(s / s.iloc[0] * 100)
            out[c] = {"dates": d, "values": v}
        return out

    # ── 수요: 분기 매출 YoY (+메모리 MU = HBM 주문 프록시) ──
    rev_cache = fetch_hyperscaler_revenue()
    rev_yoy = revenue_yoy_series(rev_cache)
    mu_yoy = revenue_yoy_series(rev_cache, [MEMORY_TICKER]).get(
        MEMORY_TICKER, {"quarters": [], "yoy": []})

    # ── 수요(공급자 프록시): NVIDIA DC 부문 매출 — 세그먼트라 XBRL에 없어 수동 입력 ──
    nv = manual.get("nvda_dc_revenue_bil", {}) or {}

    def _prev_q(k):  # 회계분기 말월(1·4·7·10) 기준 직전 분기 키
        y, mth = int(k[:4]), int(k[5:7])
        return f"{y - 1}-10" if mth == 1 else f"{y}-{mth - 3:02d}"

    nvda_dc = {"labels": [], "yoy": [], "qoq": [], "values": []}
    for k in sorted(nv.keys()):
        y, mth = k.split("-")
        prev = f"{int(y) - 1}-{mth}"
        if nv.get(prev):
            nvda_dc["labels"].append(k)
            nvda_dc["yoy"].append(pct(nv[k], nv[prev]))
            nvda_dc["qoq"].append(pct(nv[k], nv[_prev_q(k)]) if nv.get(_prev_q(k)) else None)
            nvda_dc["values"].append(nv[k])
    for kk in nvda_dc:  # 초기 하이퍼성장(+400%대)이 축을 짓누르지 않게 최근 8개 분기만
        nvda_dc[kk] = nvda_dc[kk][-8:]

    # ── 수요: 5대 하이퍼스케일러 연간 capex — 컨센서스(수동) vs 실적·런레이트(SEC 자동) ──
    cap = manual.get("hyperscaler_capex_total_bil", {}) or {}
    capex_total = {"labels": [], "values": []}
    for k in sorted(cap.keys()):
        if k[:4] >= "2025":
            capex_total["labels"].append(k)
            capex_total["values"].append(cap[k])
    hs_cx = fetch_hyperscaler_capex()
    # 역년(분기말 기준) 합산: 5사 모두 4개 분기가 있는 해만 '실적'으로 인정
    peryear = {}
    for (t, en), v in hs_cx.items():
        peryear.setdefault(en[:4], {}).setdefault(t, []).append(v)
    annual = {y: round(sum(sum(vs) for vs in d.values()) / 1e9, 1)
              for y, d in peryear.items()
              if len(d) == 5 and all(len(vs) == 4 for vs in d.values())}
    # 런레이트: 5사 각자 최근 2개 분기 합 ×2 (당해 실행 속도의 연율화)
    runrate = None
    latest2 = [sorted(en for (tk, en) in hs_cx if tk == t)[-2:] for t in CAPEX_CIK]
    if all(len(qs) == 2 for qs in latest2):
        runrate = round(sum(hs_cx[(t, en)] for t, qs in zip(CAPEX_CIK, latest2)
                            for en in qs) * 2 / 1e9, 1)
    this_year = str(date.today().year)
    capex_total["actual"] = [annual.get(lb[:4]) if lb[:4] != this_year
                             else (runrate if runrate else annual.get(lb[:4]))
                             for lb in capex_total["labels"]]
    if annual or runrate:
        log(f"5사 capex(SEC): 연간 실적 {annual}, 런레이트 ${runrate}B/yr")
    # 컨센서스 집계 이력: 최신 vs 직전 집계 — 내년(E) 전망치가 하향 수정되면 수요 조기 경보
    vint = manual.get("hyperscaler_capex_consensus_vintages", {}) or {}
    vdates = sorted(vint)
    capex_total["vintage"] = vdates[-1] if vdates else None
    capex_total["prev_vintage"] = vdates[-2] if len(vdates) >= 2 else None
    prev_v = vint.get(capex_total["prev_vintage"], {}) if capex_total["prev_vintage"] else {}
    capex_total["prev_consensus"] = [prev_v.get(lb) for lb in capex_total["labels"]]
    consensus_rev = None
    if len(vdates) >= 2:
        key = f"{int(this_year) + 1}E"
        cur_v = vint[vdates[-1]]
        if cur_v.get(key) and prev_v.get(key):
            consensus_rev = pct(cur_v[key], prev_v[key])

    # ── 레버리지: CoreWeave 분기 capex (GPU 담보부채로 조달하는 buildout) ──
    crwv_cx = sorted(fetch_crwv_capex().items())
    crwv_capex = {"labels": [f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}" for d, _ in crwv_cx],
                  "values": [round(v / 1e9, 2) for _, v in crwv_cx]}

    # ── 레버리지: 오라클 RPO (장기 계약 잔고 — 취소/사용률 하락의 정량 프록시) ──
    rp = sorted(fetch_orcl_rpo().items())
    orcl_rpo = {"labels": [d[:7] for d, _ in rp],
                "values": [round(v / 1e9, 0) for _, v in rp]}

    # ── 지표 계산 ──
    metrics = {}
    for model, key in [("H100 SXM", "gpu"), ("B200", "b200")]:
        obj = gpu.get(model, {})
        pts = [(d, r) for d, r in zip(obj.get("dates", []), obj.get("rented", []))
               if r is not None]
        if pts:
            s = pd.Series([r for _, r in pts],
                          index=pd.to_datetime([d for d, _ in pts]))
            metrics[f"{key}_rent_chg_90d"] = change_over_days(s, 90)
            metrics[f"{key}_last"] = pts[-1][1]
    for t in DC_REIT + PRIVATE_CREDIT:
        metrics[f"{t}_chg_180d"] = change_over_days(px[t], 180)
    dd = {t: drawdown_from_52w_high(px[t]) for t in NEOCLOUD + [ORACLE]}
    metrics["neocloud_dd_avg"] = round(
        sum(v for v in [dd[t] for t in NEOCLOUD] if v is not None)
        / max(1, len([1 for t in NEOCLOUD if dd[t] is not None])), 1)
    metrics["orcl_dd"] = dd.get(ORACLE)
    for name, key in [("BBB_OAS", "bbb"), ("AA_OAS", "aa"), ("CCC_OAS", "ccc")]:
        if name in spreads:
            s = pd.Series(spreads[name]["values"],
                          index=pd.to_datetime(spreads[name]["dates"]))
            metrics[f"{key}_last"] = float(s.iloc[-1])
            past = s[s.index <= s.index[-1] - timedelta(days=90)]
            metrics[f"{key}_chg_90d_bp"] = (round(float(s.iloc[-1]) - float(past.iloc[-1]), 0)
                                            if not past.empty else None)
    metrics["nvda_dc_yoy_last"] = nvda_dc["yoy"][-1] if nvda_dc["yoy"] else None
    metrics["nvda_dc_qoq_last"] = nvda_dc["qoq"][-1] if nvda_dc["qoq"] else None
    metrics["nvda_dc_last_bil"] = nvda_dc["values"][-1] if nvda_dc["values"] else None
    metrics["capex_runrate_bil"] = runrate
    metrics["capex_consensus_rev"] = consensus_rev
    # 같은 SEC 기준끼리 비교: 런레이트 vs 직전 4분기 합 — 가속이 멈추면(≤0%) 경계.
    # (컨센서스와의 직접 비교는 집계 정의 차이·램프업 편향으로 가짜 신호가 남)
    ttm4 = [sorted(en for (tk, en) in hs_cx if tk == t)[-4:] for t in CAPEX_CIK]
    if runrate and all(len(qs) == 4 for qs in ttm4):
        ttm = sum(hs_cx[(t, en)] for t, qs in zip(CAPEX_CIK, ttm4) for en in qs) / 1e9
        metrics["capex_runrate_vs_ttm"] = pct(runrate, ttm)
    if crwv_cx:
        last_d, last_v = crwv_cx[-1]
        prev = [v for d, v in crwv_cx
                if d[:4] == str(int(last_d[:4]) - 1) and d[5:7] == last_d[5:7]]
        metrics["crwv_capex_yoy"] = pct(last_v, prev[0]) if prev else None
        metrics["crwv_capex_last_bil"] = round(last_v / 1e9, 1)
    if len(rp) >= 2:
        metrics["orcl_rpo_qoq"] = pct(rp[-1][1], rp[-2][1])
        metrics["orcl_rpo_last_bil"] = round(rp[-1][1] / 1e9, 0)
    mu_vals = [v for v in mu_yoy["yoy"] if v is not None]
    if mu_vals:
        k = 0
        for i in range(len(mu_vals) - 1, 0, -1):
            if mu_vals[i] < mu_vals[i - 1]:
                k += 1
            else:
                break
        metrics["mu_slowdown_q"] = k
        metrics["mu_yoy_last"] = mu_vals[-1]

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mode": "live",
        "supply": {
            "gpu_rental": gpu,
            "dc_reit_indexed": indexed(DC_REIT),
            "vacancy": manual.get("datacenter_vacancy", []),
        },
        "demand": {
            "revenue_yoy": rev_yoy,
            "cloud_yoy": manual.get("cloud_segment_yoy", {}),
            "nvda_dc": nvda_dc,
            "capex_total": capex_total,
            "mu_yoy": mu_yoy,
        },
        "leverage": {
            "spreads": spreads,
            "neocloud_indexed": indexed(NEOCLOUD),
            "private_credit_indexed": indexed(PRIVATE_CREDIT),
            "orcl_indexed": indexed([ORACLE]),
            "drawdowns": dd,
            "crwv_capex": crwv_capex,
            "cds": manual.get("bigtech_cds_5y_bp", {}),
            "orcl_rpo": orcl_rpo,
        },
        "metrics": metrics,
    }
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 예시 데이터 (--sample) : 네트워크 없이 레이아웃 확인용
# ──────────────────────────────────────────────────────────────────────────────
def collect_sample():
    rng = random.Random(42)
    n = 500  # 약 2년 일별
    end = date(2026, 7, 22)
    dates = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]

    def walk(start, drift, vol, floor=0.1):
        v, out = start, []
        for i in range(n):
            v = max(floor, v * (1 + drift + rng.gauss(0, vol)))
            out.append(round(v, 2))
        return out

    def peaked(start, peak_i, peak, endv, vol):
        out = []
        for i in range(n):
            base = (start + (peak - start) * i / peak_i if i <= peak_i
                    else peak + (endv - peak) * (i - peak_i) / (n - peak_i))
            out.append(round(max(0.5, base * (1 + rng.gauss(0, vol))), 2))
        return out

    def idx(vals):
        return [round(v / vals[0] * 100, 2) for v in vals]

    # 공급: GPU 임대중 가격 — H100 완만한 하락, B200 고점 후 소폭 하락. DC REIT 횡보 후 하락
    gpu_vals = peaked(2.4, 250, 2.6, 1.55, 0.008)
    b200_vals = peaked(6.6, 300, 6.9, 5.8, 0.006)
    dlr = peaked(100, 300, 118, 96, 0.006)
    eqix = peaked(100, 280, 112, 90, 0.006)

    # 수요: 매출 YoY 서서히 둔화
    quarters = ["2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
    rev_yoy = {
        "MSFT": [16.0, 12.3, 13.3, 18.1, 17.5, 16.2, 14.8, 13.1],
        "GOOGL": [15.1, 11.8, 12.0, 13.8, 14.5, 13.6, 12.1, 10.9],
        "AMZN": [11.0, 10.5, 8.6, 13.3, 12.8, 11.9, 10.4, 9.2],
        "META": [18.9, 20.6, 16.1, 21.6, 20.2, 18.4, 15.9, 13.5],
    }
    cloud_yoy = {
        "2025Q3": {"Azure": 33.0, "AWS": 19.0, "Google Cloud": 32.0, "OCI": 52.0},
        "2025Q4": {"Azure": 31.0, "AWS": 17.5, "Google Cloud": 30.0, "OCI": 47.0},
        "2026Q1": {"Azure": 28.0, "AWS": 16.0, "Google Cloud": 27.0, "OCI": 41.0},
        "2026Q2": {"Azure": 25.5, "AWS": 14.5, "Google Cloud": 24.0, "OCI": 34.0},
    }

    # 레버리지: 스프레드 저점 후 확대, 네오클라우드 급락
    aa = peaked(58, 260, 50, 95, 0.01)
    bbb = peaked(105, 260, 92, 168, 0.01)
    hy = peaked(300, 260, 262, 445, 0.012)
    ccc = peaked(680, 260, 590, 1050, 0.012)
    crwv = peaked(60, 270, 187, 62, 0.03)
    nbis = peaked(30, 300, 130, 55, 0.03)
    iren = peaked(9, 310, 75, 33, 0.03)
    apld = peaked(5, 300, 38, 15, 0.03)
    orcl = peaked(120, 290, 345, 148, 0.015)
    bizd = peaked(100, 250, 104, 87, 0.004)
    pbdc = peaked(100, 250, 103, 85, 0.004)

    import statistics
    neo = {"CRWV": crwv, "NBIS": nbis, "IREN": iren, "APLD": apld}
    dd = {}
    for t, vals in {**neo, "ORCL": orcl}.items():
        hi = max(vals[-365:] if len(vals) >= 365 else vals)
        dd[t] = round((vals[-1] / hi - 1) * 100, 1)

    monthly = list(range(0, n, 7))  # 주간 샘플링으로 용량 축소

    def sub(vals):
        return [vals[i] for i in monthly]

    sdates = [dates[i] for i in monthly]

    def pack(vals):
        return {"dates": sdates, "values": sub(vals)}

    data = {
        "generated_at": "2026-07-22 (예시 데이터)",
        "mode": "sample",
        "supply": {
            "gpu_rental": {
                "H100 SXM": {"dates": sdates, "rented": sub(gpu_vals),
                             "avail": sub([round(v * 1.35, 2) for v in gpu_vals])},
                "B200": {"dates": sdates, "rented": sub(b200_vals),
                         "avail": sub([round(v * 1.02, 2) for v in b200_vals])},
            },
            "dc_reit_indexed": {"DLR": pack(idx(dlr)), "EQIX": pack(idx(eqix))},
            "vacancy": [
                {"date": "2024-H2", "region": "북미 1차 시장", "vacancy_pct": 1.9, "source": "CBRE(예시)"},
                {"date": "2025-H1", "region": "북미 1차 시장", "vacancy_pct": 1.6, "source": "CBRE(예시)"},
                {"date": "2025-H2", "region": "북미 1차 시장", "vacancy_pct": 2.4, "source": "CBRE(예시)"},
                {"date": "2026-H1", "region": "북미 1차 시장", "vacancy_pct": 3.8, "source": "CBRE(예시)"},
            ],
        },
        "demand": {
            "revenue_yoy": {t: {"quarters": quarters, "yoy": v} for t, v in rev_yoy.items()},
            "cloud_yoy": cloud_yoy,
            "nvda_dc": {"labels": ["2024-10", "2025-01", "2025-04", "2025-07", "2025-10", "2026-01", "2026-04"],
                        "yoy": [112.0, 93.4, 73.1, 56.5, 66.5, 75.1, 92.3],
                        "qoq": [None, 15.6, 9.8, 5.1, 24.6, 21.7, 20.7],
                        "values": [30.8, 35.6, 39.1, 41.1, 51.2, 62.3, 75.2]},
            "capex_total": {"labels": ["2025", "2026E", "2027E", "2028E"],
                            "values": [446.4, 815.5, 1138.4, 1266.4],
                            "actual": [446.4, 802.0, None, None],
                            "prev_consensus": [None, 813.1, 1119.6, 1267.1],
                            "vintage": "2026-08-31", "prev_vintage": "2026-07-31"},
            "mu_yoy": {"quarters": ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"],
                       "yoy": [38.3, 36.6, 30.3, 46.1, 44.0, 40.2]},
        },
        "leverage": {
            "spreads": {"AA_OAS": pack(aa), "BBB_OAS": pack(bbb),
                        "HY_OAS": pack(hy), "CCC_OAS": pack(ccc)},
            "neocloud_indexed": {t: pack(idx(v)) for t, v in neo.items()},
            "private_credit_indexed": {"BIZD": pack(idx(bizd)), "PBDC": pack(idx(pbdc))},
            "orcl_indexed": {"ORCL": pack(idx(orcl))},
            "drawdowns": dd,
            "crwv_capex": {"labels": ["2024Q1", "2024Q2", "2024Q3", "2024Q4",
                                      "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"],
                           "values": [1.7, 2.0, 2.2, 2.8, 1.4, 2.5, 2.4, 4.1, 7.7]},
            "cds": {"date": "2026-07-29", "source": "S&P Global MI (예시)",
                    "ytd_chg": {"ORCL": 70, "AVGO": 48, "META": 39, "NVDA": 32, "AMZN": 30, "GOOGL": 29},
                    "level": {"ORCL": 215, "META": 95, "NVDA": 82}},
            "orcl_rpo": {"labels": ["2025-05", "2025-08", "2025-11", "2026-02", "2026-05"],
                         "values": [138, 455, 523, 553, 638]},
        },
        "metrics": {
            "gpu_rent_chg_90d": round((gpu_vals[-1] / gpu_vals[-90] - 1) * 100, 1),
            "gpu_last": gpu_vals[-1],
            "b200_rent_chg_90d": round((b200_vals[-1] / b200_vals[-90] - 1) * 100, 1),
            "b200_last": b200_vals[-1],
            "DLR_chg_180d": round((dlr[-1] / dlr[-180] - 1) * 100, 1),
            "EQIX_chg_180d": round((eqix[-1] / eqix[-180] - 1) * 100, 1),
            "BIZD_chg_180d": round((bizd[-1] / bizd[-180] - 1) * 100, 1),
            "PBDC_chg_180d": round((pbdc[-1] / pbdc[-180] - 1) * 100, 1),
            "neocloud_dd_avg": round(statistics.mean([dd[t] for t in neo]), 1),
            "orcl_dd": dd["ORCL"],
            "nvda_dc_yoy_last": 92.3,
            "nvda_dc_qoq_last": 20.7,
            "nvda_dc_last_bil": 75.2,
            "crwv_capex_yoy": 449.3,
            "crwv_capex_last_bil": 7.7,
            "capex_runrate_bil": 802.0,
            "capex_runrate_vs_ttm": 16.0,
            "capex_consensus_rev": 1.7,
            "bbb_last": bbb[-1],
            "bbb_chg_90d_bp": round(bbb[-1] - bbb[-90], 0),
            "aa_last": aa[-1],
            "aa_chg_90d_bp": round(aa[-1] - aa[-90], 0),
            "ccc_last": ccc[-1],
            "ccc_chg_90d_bp": round(ccc[-1] - ccc[-90], 0),
            "orcl_rpo_qoq": 15.4,
            "orcl_rpo_last_bil": 638,
            "mu_slowdown_q": 2,
            "mu_yoy_last": 40.2,
        },
    }
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 신호 판정
# ──────────────────────────────────────────────────────────────────────────────
def judge(data):
    """지표 → 알림 리스트 + 축별 종합 상태(good/warning/serious)"""
    m = data["metrics"]
    alerts = []

    def add(pillar, name, value, unit, threshold_desc, bad):
        alerts.append({
            "pillar": pillar, "name": name,
            "value": (f"{value:+.1f}{unit}" if isinstance(value, (int, float)) and unit == "%"
                      else (f"{value:.0f}{unit}" if isinstance(value, (int, float)) else "—")),
            "threshold": threshold_desc,
            "status": "serious" if bad else "good",
            "known": value is not None,
        })

    v = m.get("gpu_rent_chg_90d")
    add("공급", "H100 임대가(임대중) 90일 변화 (500.farm)", v, "%",
        f"{TH['gpu_rent_chg_90d']}% 이하", v is not None and v <= TH["gpu_rent_chg_90d"])
    v = m.get("b200_rent_chg_90d")
    add("공급", "B200 임대가(임대중) 90일 변화 (500.farm)", v, "%",
        f"{TH['gpu_rent_chg_90d']}% 이하", v is not None and v <= TH["gpu_rent_chg_90d"])
    for t in DC_REIT:
        v = m.get(f"{t}_chg_180d")
        add("공급", f"{t} 6개월 수익률 (DC REIT)", v, "%",
            f"{TH['dc_reit_chg_180d']}% 이하", v is not None and v <= TH["dc_reit_chg_180d"])
    vac = data["supply"]["vacancy"]
    vv = vac[-1]["vacancy_pct"] if vac else None
    add("공급", "데이터센터 공실률 (CBRE, 수동)", vv, "%",
        f"{TH['vacancy_pct']}% 이상", vv is not None and vv >= TH["vacancy_pct"])

    # 수요: 합산 YoY 연속 둔화
    ry = data["demand"]["revenue_yoy"]
    common = None
    for t, obj in ry.items():
        qs = obj["quarters"]
        common = qs if common is None else [q for q in common if q in qs]
    slow = None
    if common and len(common) >= TH["hs_yoy_consec_slowdown"] + 1:
        totals = []
        for q in common:
            vals = [obj["yoy"][obj["quarters"].index(q)] for obj in ry.values()
                    if q in obj["quarters"] and obj["yoy"][obj["quarters"].index(q)] is not None]
            totals.append(sum(vals) / len(vals) if vals else None)
        k = 0
        for i in range(len(totals) - 1, 0, -1):
            if totals[i] is not None and totals[i - 1] is not None and totals[i] < totals[i - 1]:
                k += 1
            else:
                break
        slow = k
    add("수요", "Hyperscaler 평균 매출 YoY 연속 둔화 분기", slow, "개",
        f"{TH['hs_yoy_consec_slowdown']}분기 이상", slow is not None and slow >= TH["hs_yoy_consec_slowdown"])
    cy = data["demand"]["cloud_yoy"]
    if cy:
        lastq = sorted(cy.keys())[-1]
        worst = min(cy[lastq].values())
        add("수요", f"클라우드 부문 최저 YoY ({lastq}, 수동)", worst, "%",
            f"{TH['cloud_yoy_floor']}% 미만", worst < TH["cloud_yoy_floor"])
    else:
        add("수요", "클라우드 부문 YoY (수동 입력 대기)", None, "%", f"{TH['cloud_yoy_floor']}% 미만", False)
    v = m.get("nvda_dc_yoy_last")
    add("수요", "NVIDIA DC 매출 YoY (공급자 프록시, 수동)", v, "%",
        f"{TH['nvda_dc_yoy_floor']}% 미만", v is not None and v < TH["nvda_dc_yoy_floor"])
    v = m.get("nvda_dc_qoq_last")
    add("수요", "NVIDIA DC 매출 QoQ (성장 정지 조기 경보)", v, "%",
        f"{TH['nvda_dc_qoq']:.0f}% 이하", v is not None and v <= TH["nvda_dc_qoq"])
    v = m.get("capex_runrate_vs_ttm")
    add("수요", "5사 capex 런레이트 vs 직전 4분기 (SEC 자동)", v, "%",
        f"{TH['capex_runrate_vs_ttm']:.0f}% 이하 (가속 멈춤)", v is not None and v <= TH["capex_runrate_vs_ttm"])
    v = m.get("capex_consensus_rev")
    add("수요", "5사 capex 내년 컨센서스 수정폭 (직전 집계 대비, 수동)", v, "%",
        f"{TH['capex_consensus_rev']:.0f}% 이하 (하향 수정)", v is not None and v <= TH["capex_consensus_rev"])
    v = m.get("mu_slowdown_q")
    add("수요", "메모리(MU) 매출 YoY 연속 둔화 — HBM 주문 프록시", v, "개",
        f"{TH['mu_slowdown_q']}분기 이상", v is not None and v >= TH["mu_slowdown_q"])

    v = m.get("bbb_last")
    add("레버리지", "BBB 회사채 OAS 수준 (오라클·브로드컴급)", v, "bp",
        f"{TH['bbb_oas_level']:.0f}bp 이상", v is not None and v >= TH["bbb_oas_level"])
    v = m.get("bbb_chg_90d_bp")
    add("레버리지", "BBB OAS 90일 상승폭", v, "bp",
        f"+{TH['bbb_oas_chg_90d']:.0f}bp 이상", v is not None and v >= TH["bbb_oas_chg_90d"])
    v = m.get("aa_chg_90d_bp")
    add("레버리지", "AA OAS 90일 상승폭 (하이퍼스케일러·NVIDIA급)", v, "bp",
        f"+{TH['aa_oas_chg_90d']:.0f}bp 이상", v is not None and v >= TH["aa_oas_chg_90d"])
    hy = (data["leverage"].get("spreads") or {}).get("HY_OAS") or {}
    v = hy["values"][-1] if hy.get("values") else None
    add("레버리지", "하이일드 OAS 수준 (신용시장 전반)", v, "bp",
        f"{TH['hy_oas_level']:.0f}bp 이상", v is not None and v >= TH["hy_oas_level"])
    v = m.get("ccc_chg_90d_bp")
    add("레버리지", "CCC OAS 90일 상승폭 (최약체 조달창구)", v, "bp",
        f"+{TH['ccc_oas_chg_90d']:.0f}bp 이상", v is not None and v >= TH["ccc_oas_chg_90d"])
    v = m.get("orcl_rpo_qoq")
    add("레버리지", "오라클 RPO QoQ (계약 취소·전환 프록시, SEC)", v, "%",
        f"{TH['orcl_rpo_qoq']:.0f}% 이하 (감소 전환)", v is not None and v <= TH["orcl_rpo_qoq"])
    v = m.get("neocloud_dd_avg")
    add("레버리지", "네오클라우드 평균 52주 낙폭", v, "%",
        f"{TH['neocloud_drawdown']}% 이하", v is not None and v <= TH["neocloud_drawdown"])
    v = m.get("orcl_dd")
    add("레버리지", "오라클(ORCL) 52주 낙폭", v, "%",
        f"{TH['orcl_drawdown']}% 이하", v is not None and v <= TH["orcl_drawdown"])
    for t in PRIVATE_CREDIT:
        v = m.get(f"{t}_chg_180d")
        add("레버리지", f"{t} 6개월 수익률 (BDC/사모신용 프록시)", v, "%",
            f"{TH['bdc_chg_180d']}% 이하", v is not None and v <= TH["bdc_chg_180d"])
    v = m.get("crwv_capex_yoy")
    add("레버리지", "CoreWeave 분기 capex YoY (SEC)", v, "%",
        f"{TH['crwv_capex_yoy']:.0f}% 이하 (감소 전환)", v is not None and v <= TH["crwv_capex_yoy"])
    cds = data["leverage"].get("cds") or {}
    v = (cds.get("level") or {}).get("ORCL")
    add("레버리지", f"오라클 5Y CDS (수동 스냅샷 {cds.get('date', '')})", v, "bp",
        f"{TH['orcl_cds_level']:.0f}bp 이상", v is not None and v >= TH["orcl_cds_level"])

    pillar_status = {}
    for p in ["공급", "수요", "레버리지"]:
        rows = [a for a in alerts if a["pillar"] == p and a["known"]]
        n_bad = sum(1 for a in rows if a["status"] == "serious")
        pillar_status[p] = ("serious" if n_bad >= 2 else "warning" if n_bad == 1 else "good")
    data["alerts"] = alerts
    data["pillar_status"] = pillar_status
    return data


# ──────────────────────────────────────────────────────────────────────────────
# HTML 렌더링
# ──────────────────────────────────────────────────────────────────────────────
def render(data):
    tpl_path = os.path.join(BASE_DIR, "dashboard_template.html")
    if os.path.exists(tpl_path):
        with open(tpl_path, encoding="utf-8") as f:
            tpl = f.read()
    else:
        tpl = TEMPLATE
    html = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"대시보드 생성 완료 → {OUTPUT_HTML}")


def main():
    ap = argparse.ArgumentParser(description="AI 사이클 리스크 모니터")
    ap.add_argument("--sample", action="store_true", help="네트워크 없이 예시 데이터로 생성")
    args = ap.parse_args()
    if args.sample:
        data = collect_sample()
    else:
        try:
            data = collect_live()
        except ModuleNotFoundError as e:
            log(f"필요 패키지 없음: {e}. → pip install yfinance pandas requests")
            sys.exit(1)
    data = judge(data)
    render(data)


# 템플릿은 파일 하단에 정의 (단일 파일 배포용)
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 사이클 리스크 모니터 — 호황은 어떻게 끝나는가</title>
<style>
  .viz-root{
    color-scheme: light;
    --page:#f9f9f7; --surface-1:#fcfcfb;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#898781;
    --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
    --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
    --good-text:#006300;
  }
  @media (prefers-color-scheme: dark){
    :root:where(:not([data-theme="light"])) .viz-root{
      color-scheme: dark;
      --page:#0d0d0d; --surface-1:#1a1a19;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#898781;
      --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
      --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
      --good-text:#0ca30c;
    }
  }
  :root[data-theme="dark"] .viz-root{
    color-scheme: dark;
    --page:#0d0d0d; --surface-1:#1a1a19;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --good-text:#0ca30c;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif;
       background:#f9f9f7;color:#0b0b0b;}
  @media (prefers-color-scheme: dark){
    :root:where(:not([data-theme="light"])) body{background:#0d0d0d;color:#ffffff;}
  }
  :root[data-theme="dark"] body{background:#0d0d0d;color:#ffffff;}
  .viz-root{max-width:1080px;margin:0 auto;padding:28px 20px 60px;}
  header h1{font-size:22px;margin:0 0 4px;}
  header .sub{color:var(--text-secondary);font-size:13px;}
  .banner{margin:14px 0 0;padding:10px 14px;border:1px solid var(--border);border-radius:8px;
          background:var(--surface-1);color:var(--text-secondary);font-size:13px;}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:20px 0 8px;}
  .tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}
  .tile .label{font-size:13px;color:var(--text-secondary);}
  .tile .value{font-size:30px;font-weight:600;margin:2px 0;}
  .tile .delta{font-size:12px;color:var(--text-muted);}
  .status{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
          padding:2px 8px;border-radius:999px;border:1px solid var(--border);}
  .status .ic{font-size:11px;}
  .status.good{color:var(--good-text);} .status.warning{color:var(--text-primary);}
  .status.serious{color:var(--critical);}
  section{margin-top:34px;}
  section>h2{font-size:16px;margin:0 0 2px;}
  section>.desc{font-size:13px;color:var(--text-secondary);margin:0 0 12px;}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px;}
  @media(max-width:920px){.cards{grid-template-columns:1fr;}}
  .card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px 10px;}
  .card h3{font-size:13.5px;margin:0 0 2px;font-weight:600;}
  .card .note{font-size:11.5px;color:var(--text-muted);margin:0 0 6px;}
  .legend{display:flex;flex-wrap:wrap;gap:12px;margin:6px 0 4px;font-size:12px;color:var(--text-secondary);}
  .legend .key{display:inline-flex;align-items:center;gap:5px;}
  .legend .sw{width:10px;height:10px;border-radius:3px;display:inline-block;}
  .chart{position:relative;}
  .chart svg{display:block;width:100%;}
  .tooltip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);
           border-radius:8px;padding:7px 10px;font-size:12px;box-shadow:0 3px 12px rgba(0,0,0,.12);
           display:none;z-index:5;min-width:120px;}
  .tooltip .tx{color:var(--text-muted);margin-bottom:3px;}
  .tooltip .row{display:flex;align-items:center;gap:6px;justify-content:space-between;color:var(--text-secondary);}
  .tooltip .row b{color:var(--text-primary);font-weight:600;font-variant-numeric:tabular-nums;}
  details.tbl{margin:4px 0 6px;}
  details.tbl summary{font-size:12px;color:var(--text-muted);cursor:pointer;}
  table{border-collapse:collapse;width:100%;font-size:12px;margin-top:6px;}
  th,td{text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid);
        font-variant-numeric:tabular-nums;color:var(--text-secondary);}
  th{color:var(--text-muted);font-weight:600;} th:first-child,td:first-child{text-align:left;}
  .alerts td:first-child,.alerts th:first-child{text-align:left;}
  .empty{padding:26px 10px;text-align:center;color:var(--text-muted);font-size:13px;
         border:1px dashed var(--grid);border-radius:8px;margin:8px 0;}
  footer{margin-top:40px;font-size:12px;color:var(--text-muted);line-height:1.7;}
  footer a{color:var(--text-secondary);}
</style>
</head>
<body>
<div class="viz-root">
  <header>
    <h1>AI 사이클 리스크 모니터</h1>
    <div class="sub">호황이 끝나는 3가지 경로 — <b>공급과잉(1999)</b> · <b>수요둔화(2022)</b> · <b>레버리지(2008)</b> · 생성: <span id="genAt"></span></div>
    <div class="banner" id="sampleBanner" style="display:none">
      ⚠ 지금 보이는 것은 <b>예시 데이터</b>입니다. <code>python ai_cycle_monitor.py</code> 를 실행하면 실제 데이터로 갱신됩니다.
    </div>
  </header>

  <div class="tiles" id="tiles"></div>

  <section id="sec-supply">
    <h2>① 공급과잉 — 1999년형</h2>
    <p class="desc">인프라가 실수요보다 많이 깔리고 있는가: GPU 임대가격, 데이터센터 REIT, 공실률</p>
    <div class="cards">
      <div class="card"><h3>GPU 시간당 임대가 ($/hr, 임대중 중앙값)</h3>
        <p class="note">500.farm(vast.ai 미러) 체결가 근사 — B200 하락 = 순수 공급과잉 신호, H100은 세대교체 감가 포함</p>
        <div id="ch-gpu"></div></div>
      <div class="card"><h3>데이터센터 REIT 주가 (시작=100)</h3>
        <p class="note">DLR·EQIX — 임대수요·공실 기대를 선반영하는 프록시</p>
        <div id="ch-reit"></div></div>
      <div class="card"><h3>데이터센터 공실률 (%)</h3>
        <p class="note">CBRE 반기 보고서 → manual_data.json 수동 입력</p>
        <div id="ch-vac"></div></div>
    </div>
  </section>

  <section id="sec-demand">
    <h2>② 수요둔화 — 2022년형</h2>
    <p class="desc">돈을 내는 쪽의 성장이 꺾이는가: Hyperscaler 매출과 클라우드 부문 성장률</p>
    <div class="cards">
      <div class="card"><h3>Hyperscaler 전사 매출 YoY (%)</h3>
        <p class="note">yfinance + SEC EDGAR 백필(2023Q1~) — 실행할수록 히스토리 누적</p>
        <div id="ch-rev"></div></div>
      <div class="card"><h3>클라우드 부문 매출 YoY (%)</h3>
        <p class="note">manual_data.json 수동 입력 — AWS=순수 부문 · Google Cloud=GCP+Workspace ·
          Azure='Azure 및 기타 클라우드' 성장률(달러 미공시) · OCI=오라클 IaaS 부문(회계분기 1개월 시차) · Meta=클라우드 사업 없음</p>
        <div id="ch-cloud"></div></div>
      <div class="card"><h3>NVIDIA 데이터센터 매출 성장률 (%)</h3>
        <p class="note">공급자 측 총 AI 인프라 투자 프록시 — YoY=국면(후행), QoQ=모멘텀(조기 경보,
          0% 이하=성장 정지). 실적 발표 후 manual_data.json 수동 입력, 라벨=회계분기 말월</p>
        <div id="ch-nvda"></div></div>
      <div class="card"><h3>5대 Hyperscaler 연간 capex ($B)</h3>
        <p class="note">GOOGL+MSFT+AMZN+META+ORCL — 컨센서스(Bloomberg, 수동·E) vs 직전 집계 vs
          SEC 실적·최근2Q×2 런레이트(자동). 경계: 런레이트가 직전 4분기 합 아래로(가속 멈춤), 내년 컨센서스가
          직전 집계보다 5% 이상 하향 수정. SEC 현금 capex 기준이라 Bloomberg 합계와 정의가 소폭 다름</p>
        <div id="ch-capex"></div></div>
      <div class="card"><h3>메모리(MU) 매출 YoY (%)</h3>
        <p class="note">HBM 주문의 카나리아 — 마이크론은 회계분기(2·5·8·11월 마감)가 빨라
          빅테크·엔비디아보다 분기 신호가 먼저 찍힘. 물량 둔화는 매출 둔화로 나타남</p>
        <div id="ch-mu"></div></div>
    </div>
  </section>

  <section id="sec-leverage">
    <h2>③ 레버리지 — 2008년형</h2>
    <p class="desc">자금조달이 막히는가: 신용 스프레드, 네오클라우드 주가, 사모신용 프록시</p>
    <div class="cards">
      <div class="card"><h3>투자등급 신용 스프레드 (bp)</h3>
        <p class="note">FRED — AA(하이퍼스케일러·NVIDIA급)·BBB(오라클·브로드컴급) OAS.
          개별 CDS는 유료 데이터라 등급 버킷 OAS로 프록시</p>
        <div id="ch-spread"></div></div>
      <div class="card"><h3>하이일드(HY) OAS (bp)</h3>
        <p class="note">투기등급(BB 이하) 회사채 스프레드 — 신용시장 전반의 위험선호 온도계.
          레벨이 투자등급의 3~5배라 축 왜곡을 피해 별도 표시. 급등 = 2008년형 스트레스</p>
        <div id="ch-hy"></div></div>
      <div class="card"><h3>CCC 등급 OAS (bp)</h3>
        <p class="note">최약체 투기등급의 조달비용(FRED) — '저신용 AI 사업자도 쉽게 빌리는가'의 프록시.
          이례적 축소=과열, 90일 +150bp 급등=조달창구 폐쇄 신호</p>
        <div id="ch-ccc"></div></div>
      <div class="card"><h3>네오클라우드 주가 (시작=100)</h3>
        <p class="note">CRWV·NBIS·IREN·APLD — GPU 담보 레버리지의 체온계</p>
        <div id="ch-neo"></div></div>
      <div class="card"><h3>52주 고점 대비 낙폭 (%)</h3>
        <p class="note">네오클라우드 + 오라클</p>
        <div id="ch-dd"></div></div>
      <div class="card"><h3>사모신용/BDC 프록시 (시작=100)</h3>
        <p class="note">BIZD·PBDC — 사모대출 시장의 상장 그림자</p>
        <div id="ch-bdc"></div></div>
      <div class="card"><h3>CoreWeave 분기 capex ($B)</h3>
        <p class="note">SEC 현금흐름표(YTD 차분) — GPU 담보부채로 조달하는 buildout, 급감 = 자금줄 경색 신호</p>
        <div id="ch-crwv"></div></div>
      <div class="card"><h3>빅테크 5Y CDS 레벨 (bp)</h3>
        <p class="note" id="cdsNote">개별 CDS는 유료라 언론 인용 스냅샷을 수동 기록 — 괄호는 연초 대비 변화</p>
        <div id="ch-cds"></div></div>
      <div class="card"><h3>오라클 RPO 수주잔고 ($B)</h3>
        <p class="note">장기 컴퓨팅 계약의 총량(SEC) — 증가 정체·감소 전환 = 계약 취소/사용률 하락의
          정량 프록시. AI 랩發 계약 리스크가 숫자로 처음 찍히는 곳</p>
        <div id="ch-rpo"></div></div>
    </div>
  </section>

  <section>
    <h2>경계 신호 체크리스트</h2>
    <p class="desc">임계값은 ai_cycle_monitor.py 상단 TH 에서 조정</p>
    <div class="card"><div class="alerts" id="alertTable"></div></div>
  </section>

  <footer>
    데이터: Yahoo Finance(주가·분기매출) · FRED fredgraph.csv(신용 스프레드, 직접 차단 시 allorigins 프록시 폴백) ·
    500.farm=vast.ai 통계 미러(GPU 임대중/가용 중앙값) ·
    CBRE 반기 보고서(공실률, 수동) · 각사 실적발표(클라우드 부문 YoY, 수동)<br>
    개별 회사채 스프레드(오라클 CDS/채권)와 사모신용 실측 데이터는 무료 소스가 없어 BBB OAS·BDC 프록시로 대체.
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;

/* ── 팔레트 슬롯(고정 순서: 순환 금지) ── */
const css = s => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(s).trim();
let SLOT = [], INK = {};
function refreshColors(){
  SLOT = ['--s1','--s2','--s3','--s4'].map(css);
  INK = {primary:css('--text-primary'), secondary:css('--text-secondary'), muted:css('--text-muted'),
         grid:css('--grid'), baseline:css('--baseline'), surface:css('--surface-1'),
         good:css('--good-text'), critical:css('--critical')};
}

/* ── 유틸 ── */
const fmt = (v,d=1) => v==null ? '—' : Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:0});
function niceTicks(min,max,n=5){
  if(min===max){min-=1;max+=1;}
  const span=max-min, step0=span/n, mag=Math.pow(10,Math.floor(Math.log10(step0)));
  const step=[1,2,2.5,5,10].map(m=>m*mag).find(s=>span/s<=n+1)||mag*10;
  const lo=Math.floor(min/step)*step, hi=Math.ceil(max/step)*step, out=[];
  for(let v=lo;v<=hi+1e-9;v+=step) out.push(+v.toFixed(10));
  return out;
}
const svgEl=(t,a)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',t);
  for(const k in a) e.setAttribute(k,a[k]); return e;};

/* ── 라인 차트: 2px 라인, r4 엔드도트+서피스 링, 크로스헤어 툴팁, 표 보기 ── */
function lineChart(elId, series, opts={}){
  const host=document.getElementById(elId); if(!host) return;
  host.innerHTML='';
  series=series.filter(s=>s&&s.values&&s.values.length);
  if(!series.length){host.innerHTML='<div class="empty">데이터 없음 — 스크립트 실행 또는 manual_data.json 입력 필요</div>';return;}
  // 시리즈별 기간이 다르면(상장 시점 차이 등) 날짜 합집합 축으로 재정렬
  // — 첫 시리즈가 짧을 때 나머지가 플롯 영역 밖까지 그려지는 문제 방지
  if(!opts.labels&&series.length>1){
    const d0=series[0].dates;
    if(series.some(s=>s.dates.length!==d0.length||s.dates[0]!==d0[0]||s.dates[s.dates.length-1]!==d0[d0.length-1])){
      const all=[...new Set([].concat(...series.map(s=>s.dates)))].sort();
      series=series.map(s=>{const idx={};s.dates.forEach((d,i)=>idx[d]=s.values[i]);
        return {name:s.name,dates:all,values:all.map(d=>idx[d]??null)};});
    }
  }
  const labels=opts.labels||series[0].dates;
  const unit=opts.unit||'';
  // 범례 (2개 이상 시리즈면 항상)
  if(series.length>1){
    const lg=document.createElement('div');lg.className='legend';
    series.forEach((s,i)=>{lg.insertAdjacentHTML('beforeend',
      `<span class="key"><span class="sw" style="background:${SLOT[i]}"></span>${s.name}</span>`);});
    host.appendChild(lg);
  }
  const wrap=document.createElement('div');wrap.className='chart';host.appendChild(wrap);
  const W=Math.max(320,host.clientWidth||430), H=opts.h||210;
  const M={t:14,r:56,b:26,l:44};
  const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  wrap.appendChild(svg);
  const tip=document.createElement('div');tip.className='tooltip';wrap.appendChild(tip);

  const n=labels.length;
  const allV=series.flatMap(s=>s.values).filter(v=>v!=null);
  let ticks=niceTicks(Math.min(...allV),Math.max(...allV),4);
  const ymin=ticks[0], ymax=ticks[ticks.length-1];
  const X=i=>M.l+(n<=1?0:(W-M.l-M.r)*i/(n-1));
  const Y=v=>M.t+(H-M.t-M.b)*(1-(v-ymin)/(ymax-ymin));
  // 그리드 + y축 눈금 (헤어라인, 점선 금지)
  ticks.forEach(t=>{
    svg.appendChild(svgEl('line',{x1:M.l,x2:W-M.r,y1:Y(t),y2:Y(t),stroke:INK.grid,'stroke-width':1}));
    const tx=svgEl('text',{x:M.l-6,y:Y(t)+4,'text-anchor':'end','font-size':10.5,fill:INK.muted,
      style:'font-variant-numeric:tabular-nums'});
    tx.textContent=fmt(t,0); svg.appendChild(tx);
  });
  svg.appendChild(svgEl('line',{x1:M.l,x2:W-M.r,y1:Y(ymin),y2:Y(ymin),stroke:INK.baseline,'stroke-width':1}));
  // x축 눈금 ~6개
  const xtick=Math.max(1,Math.round(n/6));
  for(let i=0;i<n;i+=xtick){
    const t=svgEl('text',{x:X(i),y:H-8,'text-anchor':'middle','font-size':10.5,fill:INK.muted});
    t.textContent=String(labels[i]).slice(0,7); svg.appendChild(t);
  }
  // 시리즈 (끝 레이블: 겹치면 생략 — 범례·툴팁이 대신함 / 넘치면 생략)
  const usedLabelY=[];
  series.forEach((s,si)=>{
    const col=SLOT[si];
    const pts=s.values.map((v,i)=>v==null?null:[X(i),Y(v)]);
    const d=pts.map((p,i)=>p?(i&&pts[i-1]?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1):'').join('');
    svg.appendChild(svgEl('path',{d,fill:'none',stroke:col,'stroke-width':2,
      'stroke-linejoin':'round','stroke-linecap':'round'}));
    const last=pts.filter(Boolean).pop();
    if(last){
      svg.appendChild(svgEl('circle',{cx:last[0],cy:last[1],r:4,fill:col,stroke:INK.surface,'stroke-width':2}));
      const lv=s.values.filter(v=>v!=null).pop();
      const txt=(series.length<=2?fmt(lv):s.name);
      const estW=String(txt).length*6.6;
      const collide=usedLabelY.some(y=>Math.abs(y-last[1])<12);
      if(!collide && last[0]+8+estW<=W-2){
        const t=svgEl('text',{x:last[0]+8,y:last[1]+4,'font-size':11,fill:INK.secondary,
          style:'font-variant-numeric:tabular-nums'});
        t.textContent=txt; svg.appendChild(t);
        usedLabelY.push(last[1]);
      }
    }
  });
  // 크로스헤어 + 툴팁
  const cross=svgEl('line',{y1:M.t,y2:H-M.b,stroke:INK.baseline,'stroke-width':1,visibility:'hidden'});
  svg.appendChild(cross);
  svg.addEventListener('mousemove',ev=>{
    const r=svg.getBoundingClientRect();
    const x=(ev.clientX-r.left)*W/r.width;
    const i=Math.max(0,Math.min(n-1,Math.round((x-M.l)/((W-M.l-M.r)/Math.max(1,n-1)))));
    cross.setAttribute('x1',X(i));cross.setAttribute('x2',X(i));cross.setAttribute('visibility','visible');
    tip.style.display='block';
    tip.innerHTML=`<div class="tx">${labels[i]}</div>`+series.map((s,si)=>
      `<div class="row"><span class="key"><span class="sw" style="background:${SLOT[si]};width:8px;height:8px;border-radius:2px;display:inline-block;margin-right:4px"></span>${s.name}</span><b>${fmt(s.values[i])}${unit}</b></div>`).join('');
    const px=X(i)*r.width/W;
    tip.style.left=(px>r.width*0.6?px-tip.offsetWidth-12:px+12)+'px';
    tip.style.top='14px';
  });
  svg.addEventListener('mouseleave',()=>{tip.style.display='none';cross.setAttribute('visibility','hidden');});
  // 표 보기 (접근성 릴리프)
  const det=document.createElement('details');det.className='tbl';
  det.innerHTML='<summary>표 보기</summary>';
  const tb=document.createElement('table');
  tb.innerHTML='<tr><th>구분</th>'+series.map(s=>`<th>${s.name}</th>`).join('')+'</tr>';
  const step=Math.max(1,Math.round(n/12));
  for(let i=0;i<n;i+=step){
    tb.insertAdjacentHTML('beforeend',`<tr><td>${labels[i]}</td>`+
      series.map(s=>`<td>${fmt(s.values[i])}${unit}</td>`).join('')+'</tr>');
  }
  det.appendChild(tb);host.appendChild(det);
}

/* ── 가로 막대(낙폭): ≤24px 두께, 데이터 끝 4px 라운드, 팁 값 라벨 ── */
function hbarChart(elId, items, unit='%'){
  const host=document.getElementById(elId); if(!host) return;
  host.innerHTML='';
  items=items.filter(it=>it.value!=null);
  if(!items.length){host.innerHTML='<div class="empty">데이터 없음</div>';return;}
  const W=Math.max(320,host.clientWidth||430), rowH=34;
  // 왼쪽 여백은 가장 긴 라벨에 맞춰 동적으로 (괄호 라벨 잘림 방지)
  const lw=Math.max(...items.map(it=>String(it.label).length))*6.6+12;
  const M={t:8,r:56,b:22,l:Math.max(56,Math.ceil(lw))};
  const H=M.t+M.b+rowH*items.length;
  const wrap=document.createElement('div');wrap.className='chart';host.appendChild(wrap);
  const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,width:W,height:H});
  wrap.appendChild(svg);
  const minV=Math.min(...items.map(i=>i.value),0);
  const maxV=Math.max(...items.map(i=>i.value),0);
  const ticks=niceTicks(minV,maxV,4);
  const t0=ticks[0], tn=ticks[ticks.length-1];
  const X=v=>M.l+(W-M.l-M.r)*(v-t0)/((tn-t0)||1);
  ticks.forEach(t=>{
    svg.appendChild(svgEl('line',{x1:X(t),x2:X(t),y1:M.t,y2:H-M.b,stroke:INK.grid,'stroke-width':1}));
    const tx=svgEl('text',{x:X(t),y:H-6,'text-anchor':'middle','font-size':10.5,fill:INK.muted});
    tx.textContent=fmt(t,0);svg.appendChild(tx);
  });
  svg.appendChild(svgEl('line',{x1:X(0),x2:X(0),y1:M.t,y2:H-M.b,stroke:INK.baseline,'stroke-width':1}));
  items.forEach((it,i)=>{
    const y=M.t+rowH*i+(rowH-22)/2, x0=X(0), xv=X(it.value), neg=it.value<0;
    // 베이스라인(0) 쪽 직각, 데이터 끝 4px 라운드 (음수=왼쪽, 양수=오른쪽)
    const p=neg
      ?`M ${x0} ${y} L ${xv+4} ${y} Q ${xv} ${y} ${xv} ${y+4} L ${xv} ${y+18} Q ${xv} ${y+22} ${xv+4} ${y+22} L ${x0} ${y+22} Z`
      :`M ${x0} ${y} L ${xv-4} ${y} Q ${xv} ${y} ${xv} ${y+4} L ${xv} ${y+18} Q ${xv} ${y+22} ${xv-4} ${y+22} L ${x0} ${y+22} Z`;
    svg.appendChild(svgEl('path',{d:p,fill:SLOT[0]}));
    const lb=svgEl('text',{x:M.l-6,y:y+15,'text-anchor':'end','font-size':11.5,fill:INK.secondary});
    lb.textContent=it.label;svg.appendChild(lb);
    // 값 라벨은 막대 끝 바깥, 공간이 없으면 안쪽에 표시
    let inside,vx,anchor;
    if(neg){inside=(xv-6)<(M.l+44);vx=inside?xv+6:xv-6;anchor=inside?'start':'end';}
    else{inside=(xv+6)>(W-M.r-4);vx=inside?xv-6:xv+6;anchor=inside?'end':'start';}
    const vl=svgEl('text',{x:vx,y:y+15,'text-anchor':anchor,'font-size':11,
      fill:inside?INK.surface:INK.secondary,style:'font-variant-numeric:tabular-nums'});
    vl.textContent=fmt(it.value)+unit;svg.appendChild(vl);
  });
}

/* ── 상태 배지 ── */
const STATUS_META={good:{ic:'●',t:'정상'},warning:{ic:'▲',t:'주의'},serious:{ic:'■',t:'경계'}};
function badge(st){const m=STATUS_META[st]||STATUS_META.good;
  return `<span class="status ${st}"><span class="ic">${m.ic}</span>${m.t}</span>`;}

/* ── 렌더 ── */
function renderAll(){
  refreshColors();
  document.getElementById('genAt').textContent=DATA.generated_at;
  if(DATA.mode==='sample') document.getElementById('sampleBanner').style.display='block';

  // 축별 상태 타일
  const m=DATA.metrics, ps=DATA.pillar_status||{};
  const tiles=[
    {label:'① 공급과잉 신호', st:ps['공급'],
     value:m.gpu_rent_chg_90d!=null?fmt(m.gpu_rent_chg_90d)+'%':'—',
     delta:'H100 임대중 90일 변화'+(m.b200_rent_chg_90d!=null?' · B200 '+fmt(m.b200_rent_chg_90d)+'%':'')},
    {label:'② 수요둔화 신호', st:ps['수요'],
     value:(()=>{const ry=DATA.demand.revenue_yoy;const vs=Object.values(ry).map(o=>o.yoy[o.yoy.length-1]).filter(v=>v!=null);
       return vs.length?fmt(vs.reduce((a,b)=>a+b,0)/vs.length)+'%':'—';})(),
     delta:'Hyperscaler 평균 매출 YoY'+(m.nvda_dc_yoy_last!=null?' · NVDA DC +'+fmt(m.nvda_dc_yoy_last)+'% ($'+fmt(m.nvda_dc_last_bil)+'B)':'')},
    {label:'③ 레버리지 신호', st:ps['레버리지'],
     value:m.bbb_last!=null?fmt(m.bbb_last,0)+'bp':'—',
     delta:`BBB OAS · 90일 ${m.bbb_chg_90d_bp>=0?'+':''}${fmt(m.bbb_chg_90d_bp,0)}bp · 네오클라우드 낙폭 ${fmt(m.neocloud_dd_avg)}%`},
  ];
  document.getElementById('tiles').innerHTML=tiles.map(t=>
    `<div class="tile"><div class="label">${t.label} ${badge(t.st)}</div>
     <div class="value">${t.value}</div><div class="delta">${t.delta}</div></div>`).join('');

  // ① 공급 — 모델별 임대중 중앙값 (공통 날짜축 정렬, H100 가용 호가는 참고선)
  const g=DATA.supply.gpu_rental||{};
  const gDates=[...new Set([].concat(...Object.keys(g).map(mo=>g[mo].dates)))].sort();
  const gAlign=(mo,key)=>{const idx={};g[mo].dates.forEach((d,i)=>idx[d]=g[mo][key][i]);
    return gDates.map(d=>idx[d]??null);};
  const gSeries=[];
  Object.keys(g).forEach(mo=>{
    if((g[mo].rented||[]).some(v=>v!=null))
      gSeries.push({name:mo+' 임대중',dates:gDates,values:gAlign(mo,'rented')});
  });
  if(g['H100 SXM']&&(g['H100 SXM'].avail||[]).some(v=>v!=null))
    gSeries.push({name:'H100 가용 호가',dates:gDates,values:gAlign('H100 SXM','avail')});
  lineChart('ch-gpu',gSeries,{labels:gDates,unit:'$'});
  const reit=DATA.supply.dc_reit_indexed||{};
  lineChart('ch-reit', Object.keys(reit).map(k=>({name:k,dates:reit[k].dates,values:reit[k].values})));
  const vac=DATA.supply.vacancy||[];
  if(vac.length){
    lineChart('ch-vac',[{name:'공실률',dates:vac.map(v=>v.date),values:vac.map(v=>v.vacancy_pct)}],
      {labels:vac.map(v=>v.date),unit:'%',h:180});
  } else {
    document.getElementById('ch-vac').innerHTML=
      '<div class="empty">manual_data.json 의 datacenter_vacancy 에 CBRE 공실률을 입력하세요</div>';
  }

  // ② 수요
  const ry=DATA.demand.revenue_yoy||{};
  const rSeries=Object.keys(ry).filter(t=>ry[t].yoy.length).map(t=>({name:t,dates:ry[t].quarters,values:ry[t].yoy}));
  // 공통 분기축으로 정렬
  if(rSeries.length){
    const qs=[...new Set(rSeries.flatMap(s=>s.dates))].sort();
    rSeries.forEach(s=>{s.values=qs.map(q=>{const i=s.dates.indexOf(q);return i<0?null:s.values[i];});s.dates=qs;});
    lineChart('ch-rev',rSeries,{labels:qs,unit:'%'});
  } else lineChart('ch-rev',[]);
  const cy=DATA.demand.cloud_yoy||{};
  const cqs=Object.keys(cy).sort();
  if(cqs.length){
    const names=[...new Set(cqs.flatMap(q=>Object.keys(cy[q])))];
    lineChart('ch-cloud',names.map(nm=>({name:nm,dates:cqs,values:cqs.map(q=>cy[q][nm]??null)})),
      {labels:cqs,unit:'%'});
  } else {
    document.getElementById('ch-cloud').innerHTML=
      '<div class="empty">manual_data.json 의 cloud_segment_yoy 에 분기 실적을 입력하세요</div>';
  }
  const nv=DATA.demand.nvda_dc||{};
  if(nv.labels&&nv.labels.length){
    const nvSeries=[{name:'YoY',dates:nv.labels,values:nv.yoy}];
    if(nv.qoq&&nv.qoq.some(v=>v!=null)) nvSeries.push({name:'QoQ',dates:nv.labels,values:nv.qoq});
    lineChart('ch-nvda',nvSeries,{labels:nv.labels,unit:'%'});
  } else {
    document.getElementById('ch-nvda').innerHTML=
      '<div class="empty">manual_data.json 의 nvda_dc_revenue_bil 에 분기 매출($B)을 입력하세요</div>';
  }
  const mu=DATA.demand.mu_yoy||{};
  if(mu.quarters&&mu.quarters.length){
    lineChart('ch-mu',[{name:'MU',dates:mu.quarters,values:mu.yoy}],{labels:mu.quarters,unit:'%',h:190});
  } else {
    document.getElementById('ch-mu').innerHTML='<div class="empty">수집 대기</div>';
  }
  const cap=DATA.demand.capex_total||{};
  if(cap.labels&&cap.labels.length){
    const capSeries=[{name:'컨센서스'+(cap.vintage?` (${cap.vintage.slice(5)})`:''),dates:cap.labels,values:cap.values}];
    if(cap.prev_consensus&&cap.prev_consensus.some(v=>v!=null))
      capSeries.push({name:'직전 컨센서스'+(cap.prev_vintage?` (${cap.prev_vintage.slice(5)})`:''),dates:cap.labels,values:cap.prev_consensus});
    if(cap.actual&&cap.actual.some(v=>v!=null))
      capSeries.push({name:'실적·런레이트(SEC)',dates:cap.labels,values:cap.actual});
    lineChart('ch-capex',capSeries,{labels:cap.labels,unit:'B',h:190});
  } else {
    document.getElementById('ch-capex').innerHTML=
      '<div class="empty">manual_data.json 의 hyperscaler_capex_total_bil 에 연간 capex($B)를 입력하세요</div>';
  }

  // ③ 레버리지
  const sp=DATA.leverage.spreads||{};
  lineChart('ch-spread',[
    sp.AA_OAS?{name:'AA OAS',dates:sp.AA_OAS.dates,values:sp.AA_OAS.values}:null,
    sp.BBB_OAS?{name:'BBB OAS',dates:sp.BBB_OAS.dates,values:sp.BBB_OAS.values}:null,
  ].filter(Boolean),{unit:'bp'});
  lineChart('ch-hy',
    sp.HY_OAS?[{name:'HY OAS',dates:sp.HY_OAS.dates,values:sp.HY_OAS.values}]:[],
    {unit:'bp',h:180});
  lineChart('ch-ccc',
    sp.CCC_OAS?[{name:'CCC OAS',dates:sp.CCC_OAS.dates,values:sp.CCC_OAS.values}]:[],
    {unit:'bp',h:180});
  const neo=DATA.leverage.neocloud_indexed||{};
  lineChart('ch-neo',Object.keys(neo).map(k=>({name:k,dates:neo[k].dates,values:neo[k].values})));
  const dd=DATA.leverage.drawdowns||{};
  hbarChart('ch-dd',Object.keys(dd).map(k=>({label:k,value:dd[k]})).sort((a,b)=>a.value-b.value));
  const bdc=DATA.leverage.private_credit_indexed||{};
  lineChart('ch-bdc',Object.keys(bdc).map(k=>({name:k,dates:bdc[k].dates,values:bdc[k].values})));
  const cwc=DATA.leverage.crwv_capex||{};
  if(cwc.labels&&cwc.labels.length){
    lineChart('ch-crwv',[{name:'CRWV capex',dates:cwc.labels,values:cwc.values}],{labels:cwc.labels,unit:'B',h:190});
  } else {
    document.getElementById('ch-crwv').innerHTML='<div class="empty">SEC 수집 대기</div>';
  }
  const cds=DATA.leverage.cds||{}, cchg=cds.ytd_chg||{}, clvl=cds.level||{};
  if(Object.keys(clvl).length||Object.keys(cchg).length){
    document.getElementById('cdsNote').textContent=
      `${cds.date||''} 기준 · ${cds.source||''} — 유료 데이터라 언론 인용 스냅샷 수동 기록, 괄호=연초 대비(bp)`;
    // 레벨이 있으면 레벨 막대(+YTD 괄호), 없으면 YTD 변화 막대로 폴백
    const items=Object.keys(clvl).length
      ?Object.keys(clvl).map(k=>({label:k+(cchg[k]!=null?` (+${cchg[k]})`:''),value:clvl[k]}))
      :Object.keys(cchg).map(k=>({label:k,value:cchg[k]}));
    hbarChart('ch-cds',items.sort((a,b)=>b.value-a.value),'bp');
  } else {
    document.getElementById('ch-cds').innerHTML=
      '<div class="empty">manual_data.json 의 bigtech_cds_5y_bp 입력 대기</div>';
  }
  const rpo=DATA.leverage.orcl_rpo||{};
  if(rpo.labels&&rpo.labels.length){
    lineChart('ch-rpo',[{name:'ORCL RPO',dates:rpo.labels,values:rpo.values}],{labels:rpo.labels,unit:'B',h:180});
  } else {
    document.getElementById('ch-rpo').innerHTML='<div class="empty">SEC 수집 대기</div>';
  }

  // 경계 신호 표
  const rows=(DATA.alerts||[]).map(a=>
    `<tr><td>${a.pillar}</td><td style="text-align:left">${a.name}</td>
     <td>${a.known?a.value:'—'}</td><td>${a.threshold}</td>
     <td style="text-align:left">${a.known?badge(a.status):'<span class="status">입력 대기</span>'}</td></tr>`).join('');
  document.getElementById('alertTable').innerHTML=
    `<table><tr><th>축</th><th style="text-align:left">지표</th><th>현재</th><th>경계 기준</th><th style="text-align:left">상태</th></tr>${rows}</table>`;
}
renderAll();
let rsz;window.addEventListener('resize',()=>{clearTimeout(rsz);rsz=setTimeout(renderAll,200);});
if(window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',renderAll);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
