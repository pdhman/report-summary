#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 크립토 모니터 (crypto_monitor.py)
================================================================================
비트코인 핵심 지표를 무료 데이터로 수집해 단일 HTML 대시보드를 생성한다.

  [가격·모멘텀]  BTC 가격 + MA50/MA200, RSI(14), ETH/BTC 상대강도  — Binance
  [밸류에이션]   MVRV 비율                                        — CoinMetrics 커뮤니티 API
  [ETF 흐름]     미국 현물 ETF 일별/누적 순유입                    — Farside Investors
  [파생상품]     펀딩비(일평균), 미결제약정(OI)                    — Binance Futures

사용법
------
  pip install requests
  python crypto_monitor.py          # 수집 → crypto.html 생성

  * OI는 바이낸스가 최근 30일만 제공 → 실행할 때마다 oi_history.csv 에 누적 저장.
  * ETF 흐름도 etf_flow_history.csv 에 누적 캐시(사이트 장애 시 캐시로 렌더).
  * 청산량(liquidation)은 무료 공개 API가 없어(Coinglass 유료) 미포함.

출력: crypto.html (단일 파일, 모든 날짜는 UTC 기준)
================================================================================
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_HTML = os.path.join(BASE_DIR, "crypto_template.html")
OUTPUT_HTML = os.path.join(BASE_DIR, "crypto.html")
SUMMARY_JSON = os.path.join(BASE_DIR, "crypto_summary.json")  # 알파노트 홈 카드용
OI_CSV = os.path.join(BASE_DIR, "oi_history.csv")
ETF_CSV = os.path.join(BASE_DIR, "etf_flow_history.csv")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
LOOKBACK_DAYS = 730  # 가격·MVRV 히스토리 길이 (2년)


def log(msg):
    print(f"  {msg}")


def get_json(url, params=None, timeout=25):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────────────────────────────────────────
# 수집: Binance 현물 (가격 / RSI / ETH·BTC)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_klines(symbol, limit=LOOKBACK_DAYS):
    """일봉 종가. 반환: (dates[], closes[]) — 마지막 봉은 진행 중(오늘)."""
    data = get_json("https://api.binance.com/api/v3/klines",
                    {"symbol": symbol, "interval": "1d", "limit": min(limit, 1000)})
    dates = [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for k in data]
    closes = [float(k[4]) for k in data]
    return dates, closes


def fetch_ticker_24h(symbol):
    d = get_json("https://api.binance.com/api/v3/ticker/24hr", {"symbol": symbol})
    return float(d["lastPrice"]), float(d["priceChangePercent"])


def rsi_series(closes, period=14):
    """Wilder RSI. closes 길이와 같은 리스트(초기 구간 None)."""
    n = len(closes)
    if n <= period:
        return [None] * n
    out = [None] * n
    gains, losses = [], []
    for i in range(1, n):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    def to_rsi(g, l):
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)
    out[period] = to_rsi(avg_g, avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
        avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
        out[i] = to_rsi(avg_g, avg_l)
    return [round(v, 2) if v is not None else None for v in out]


def moving_avg(values, window):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        if i >= window - 1:
            out[i] = round(s / window, 2)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 수집: CoinMetrics MVRV
# ──────────────────────────────────────────────────────────────────────────────
def fetch_mvrv():
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    d = get_json("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                 {"assets": "btc", "metrics": "CapMVRVCur", "frequency": "1d",
                  "start_time": start, "page_size": 2000})
    dates, values = [], []
    for row in d.get("data", []):
        dates.append(row["time"][:10])
        values.append(round(float(row["CapMVRVCur"]), 4))
    return dates, values


# ──────────────────────────────────────────────────────────────────────────────
# 수집: Binance 선물 (펀딩비 / OI)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_funding():
    """8시간 펀딩비 → 일평균(%). 최근 1000건 ≈ 333일."""
    data = get_json("https://fapi.binance.com/fapi/v1/fundingRate",
                    {"symbol": "BTCUSDT", "limit": 1000})
    daily = {}
    for row in data:
        day = datetime.fromtimestamp(row["fundingTime"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        daily.setdefault(day, []).append(float(row["fundingRate"]) * 100.0)
    dates = sorted(daily)
    values = [round(sum(daily[d]) / len(daily[d]), 5) for d in dates]
    return dates, values


def fetch_oi():
    """일별 OI. 바이낸스는 최근 30일만 제공 → CSV에 누적."""
    hist = {}
    if os.path.exists(OI_CSV):
        with open(OI_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                hist[row["date"]] = (float(row["oi_btc"]), float(row["oi_usd_b"]))
    try:
        data = get_json("https://fapi.binance.com/futures/data/openInterestHist",
                        {"symbol": "BTCUSDT", "period": "1d", "limit": 500})
        for row in data:
            day = datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            hist[day] = (round(float(row["sumOpenInterest"]), 1),
                         round(float(row["sumOpenInterestValue"]) / 1e9, 3))
    except Exception as e:
        log(f"OI 수집 실패(캐시 사용): {e}")
    if hist:
        with open(OI_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "oi_btc", "oi_usd_b"])
            for d in sorted(hist):
                w.writerow([d, hist[d][0], hist[d][1]])
    dates = sorted(hist)
    return dates, [hist[d][1] for d in dates], [hist[d][0] for d in dates]


# ──────────────────────────────────────────────────────────────────────────────
# 수집: Farside ETF 순유입
# ──────────────────────────────────────────────────────────────────────────────
def _parse_farside_cell(txt):
    txt = re.sub(r"&[a-z]+;", "", txt).strip()
    if not txt or txt == "-":
        return None
    neg = txt.startswith("(") and txt.endswith(")")
    txt = txt.strip("()").replace(",", "")
    try:
        v = float(txt)
    except ValueError:
        return None
    return -v if neg else v


def _parse_farside_page(url):
    """date(ISO) → 일별 총 순유입($M). 표의 마지막 열(Total)만 사용.

    주의: Farside는 아직 집계되지 않은 날(당일)과 증시 휴장일도 행으로 게시하는데,
    개별 종목 칸은 모두 '-'인 반면 Total 칸만 '0.0'으로 찍힌다. 그대로 읽으면
    '순유입 0인 거래일'로 오인되어 최근일 타일·5일 합·차트에 가짜 0이 섞인다.
    → 개별 종목 칸 중 최소 하나가 숫자인 행만 실제 거래일로 인정한다.
    """
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S)
    out = {}
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) < 3:
            continue
        try:
            day = datetime.strptime(cells[0], "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        total = _parse_farside_cell(cells[-1])
        if total is None:
            continue
        funds = [_parse_farside_cell(c) for c in cells[1:-1]]
        if not any(v is not None for v in funds):
            continue  # 전 종목 '-' = 미집계·휴장일 → 데이터 없음
        out[day] = total
    return out


def fetch_etf_flows():
    hist = {}
    if os.path.exists(ETF_CSV):
        with open(ETF_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                hist[row["date"]] = float(row["flow_musd"])
    for url in ("https://farside.co.uk/bitcoin-etf-flow-all-data/",
                "https://farside.co.uk/btc/"):
        try:
            page = _parse_farside_page(url)
            hist.update(page)
            log(f"Farside {url.split('/')[-2]}: {len(page)}일")
        except Exception as e:
            log(f"Farside 수집 실패({url}): {e}")
    if hist:
        with open(ETF_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "flow_musd"])
            for d in sorted(hist):
                w.writerow([d, hist[d]])
    dates = sorted(hist)
    flows = [round(hist[d], 1) for d in dates]
    cum, run = [], 0.0
    for v in flows:
        run += v
        cum.append(round(run / 1000.0, 3))  # $B
    return dates, flows, cum


# ──────────────────────────────────────────────────────────────────────────────
# 신호 판정
# ──────────────────────────────────────────────────────────────────────────────
def build_signals(m):
    sig = []

    def add(name, value, criteria, status, label):
        sig.append({"name": name, "value": value, "criteria": criteria,
                    "status": status, "label": label})

    v = m.get("mvrv")
    if v is not None:
        st, lb = ("serious", "과열") if v >= 3.0 else \
                 ("warning", "주의") if v >= 2.0 else \
                 ("good", "저평가 구간") if v < 1.0 else ("good", "중립")
        add("MVRV 비율", f"{v:.2f}", "≥3.0 과열 · ≥2.0 주의 · <1.0 저평가", st, lb)

    v = m.get("rsi14")
    if v is not None:
        st, lb = ("warning", "과매수") if v >= 70 else \
                 ("warning", "과매도") if v <= 30 else ("good", "중립")
        add("RSI (14일)", f"{v:.1f}", "≥70 과매수 · ≤30 과매도", st, lb)

    v = m.get("price_vs_ma200")
    if v is not None:
        st, lb = ("good", "200일선 위") if v >= 0 else ("warning", "200일선 아래")
        add("가격 vs MA200", f"{v:+.1f}%", "이평선 하회 시 추세 훼손", st, lb)

    v = m.get("funding_avg7")
    if v is not None:
        st, lb = ("serious", "롱 과열") if v >= 0.05 else \
                 ("warning", "숏 우위") if v < 0 else ("good", "중립")
        add("펀딩비 (7일 평균)", f"{v:.4f}%", "≥0.05% 롱 과열 · <0 숏 우위", st, lb)

    v = m.get("etf_5d_sum")
    if v is not None:
        st, lb = ("serious", "대규모 유출") if v <= -1000 else \
                 ("warning", "유출 우위") if v < 0 else ("good", "유입 지속")
        add("ETF 5일 순유입", f"{v:+,.0f}M$", "5일 합 음수=유출 · ≤-1,000M 경계", st, lb)

    v = m.get("oi_chg_7d")
    if v is not None:
        st, lb = ("warning", "레버리지 급증") if v >= 15 else \
                 ("warning", "디레버리징") if v <= -15 else ("good", "중립")
        add("OI 7일 변화", f"{v:+.1f}%", "±15% 이상 급변 시 주의", st, lb)

    return sig


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────
def main():
    data = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M") + " (로컬)",
            "metrics": {}, "signals": []}
    m = data["metrics"]

    print("[1/5] Binance 현물 (BTC·ETH 일봉)")
    try:
        b_dates, b_close = fetch_klines("BTCUSDT")
        e_dates, e_close = fetch_klines("ETHUSDT")
        data["price"] = {"dates": b_dates, "btc": b_close,
                         "ma50": moving_avg(b_close, 50), "ma200": moving_avg(b_close, 200)}
        rsi = rsi_series(b_close)
        data["rsi"] = {"dates": b_dates, "values": rsi}
        eb_idx = {d: v for d, v in zip(e_dates, e_close)}
        eb_dates = [d for d in b_dates if d in eb_idx]
        bt_idx = {d: v for d, v in zip(b_dates, b_close)}
        data["ethbtc"] = {"dates": eb_dates,
                          "values": [round(eb_idx[d] / bt_idx[d], 5) for d in eb_dates]}
        last_px, chg24 = fetch_ticker_24h("BTCUSDT")
        m["btc_price"] = round(last_px, 0)
        m["btc_chg_24h"] = round(chg24, 2)
        m["rsi14"] = rsi[-1]
        ma200 = data["price"]["ma200"][-1]
        if ma200:
            m["price_vs_ma200"] = round((last_px / ma200 - 1) * 100, 1)
        m["ethbtc"] = data["ethbtc"]["values"][-1] if eb_dates else None
        log(f"BTC ${m['btc_price']:,.0f} ({m['btc_chg_24h']:+.2f}%) · RSI {m['rsi14']}")
    except Exception as e:
        log(f"실패: {e}")

    print("[2/5] CoinMetrics MVRV")
    try:
        mv_dates, mv_vals = fetch_mvrv()
        data["mvrv"] = {"dates": mv_dates, "values": mv_vals}
        m["mvrv"] = mv_vals[-1] if mv_vals else None
        if len(mv_vals) >= 31:
            m["mvrv_chg_30d"] = round(mv_vals[-1] - mv_vals[-31], 3)
        log(f"MVRV {m['mvrv']} ({len(mv_vals)}일)")
    except Exception as e:
        log(f"실패: {e}")

    print("[3/5] Binance 선물 펀딩비")
    try:
        f_dates, f_vals = fetch_funding()
        data["funding"] = {"dates": f_dates, "values": f_vals}
        m["funding_last"] = f_vals[-1] if f_vals else None
        if len(f_vals) >= 7:
            m["funding_avg7"] = round(sum(f_vals[-7:]) / 7, 5)
            m["funding_apr"] = round(m["funding_avg7"] * 3 * 365 / 100 * 100, 1)  # 연환산 %
        log(f"펀딩비(일평균) {m['funding_last']}% · 7일 {m.get('funding_avg7')}%")
    except Exception as e:
        log(f"실패: {e}")

    print("[4/5] Binance 미결제약정(OI)")
    try:
        oi_dates, oi_usd, oi_btc = fetch_oi()
        data["oi"] = {"dates": oi_dates, "usd_b": oi_usd}
        m["oi_usd_b"] = oi_usd[-1] if oi_usd else None
        if len(oi_usd) >= 8:
            m["oi_chg_7d"] = round((oi_usd[-1] / oi_usd[-8] - 1) * 100, 1)
        log(f"OI ${m['oi_usd_b']}B ({len(oi_dates)}일 누적)")
    except Exception as e:
        log(f"실패: {e}")

    print("[5/5] Farside ETF 순유입")
    try:
        etf_dates, etf_flow, etf_cum = fetch_etf_flows()
        data["etf"] = {"dates": etf_dates, "flow": etf_flow, "cum": etf_cum}
        if etf_flow:
            m["etf_last_flow"] = etf_flow[-1]
            m["etf_last_date"] = etf_dates[-1]
            m["etf_5d_sum"] = round(sum(etf_flow[-5:]), 1)
            m["etf_cum_total"] = etf_cum[-1]
        log(f"최근일 {m.get('etf_last_flow')}M$ · 누적 {m.get('etf_cum_total')}B$")
    except Exception as e:
        log(f"실패: {e}")

    data["signals"] = build_signals(m)

    with open(TEMPLATE_HTML, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    # 알파노트 홈 카드용 요약 (make_summary.card_crypto 가 읽는다).
    # 시장 건전성 카드가 market_summary.json 을 읽는 것과 같은 방식.
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "btc_price": m.get("btc_price"),
        "btc_chg_24h": m.get("btc_chg_24h"),
        "rsi14": m.get("rsi14"),
        "mvrv": m.get("mvrv"),
        "etf_last_date": m.get("etf_last_date"),
        "etf_last_flow": m.get("etf_last_flow"),
        "etf_5d_sum": m.get("etf_5d_sum"),
    }
    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"완료 → {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
