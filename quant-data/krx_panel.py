# -*- coding: utf-8 -*-
"""
생존편향 없는 월말 패널 수집기 — KRX Open API (2010-01 ~).

매월 마지막 거래일의 코스피·코스닥 **그 시점 상장 전종목** 시세·시총·거래대금을 받아
하나의 패널로 쌓는다. 나중에 상장폐지된 종목도 존재했던 달에는 그대로 들어 있으므로
백테스트 유니버스로 쓰면 생존편향이 사라진다. (네이버·FDR 은 현재 상장 종목만 준다.)

출력 (quant-data/output/krx_panel/):
  panel_monthly.parquet   월말 패널. 열:
      date, code, name, market(KOSPI/KOSDAQ), sect(소속부), close, open, high, low,
      volume, value(거래대금 원), mktcap(원), shares, is_pref(우선주),
      ret_1m(당월 수익률, 전월말 대비), fwd_ret_1m(다음 달 수익률 — 백테스트용),
      last_month(이 달이 마지막 등장이면 True = 다음 달 전에 상폐/합병/이전)
  panel_monthly.csv       같은 내용 (UTF-8 BOM, 엑셀용)
  listing_events.csv      종목별 첫 등장·마지막 등장·마지막 종가·개월 수
  index_monthly.csv       코스피·코스닥·코스피200·코스닥150 월말 종가 (벤치마크)

주의:
  - 스팩(이름에 '스팩')은 수집 단계에서 제외한다. 우선주는 남기고 is_pref 로 표시만 한다.
  - 수정주가가 아니다. 월간 수익률(ret_1m)은 액면분할·병합 달에 튄다 → |ret|>±80% 인
    행은 split_flag=True 로 표시만 하고 값은 두었다(소비자가 판단).
  - 상폐 사유는 알 수 없다(합병·자진상폐·부도 구분 불가). last_month 행의 fwd_ret_1m 은
    NaN — 보수적으로 쓰려면 -100% 또는 마지막 종가 청산으로 처리할 것.
  - KRX 는 하루 단위 조회뿐. 월말 거래일은 말일부터 거꾸로 내려가며 첫 응답일을 쓴다.
    휴장일 응답(빈 목록)도 디스크 캐시되므로 재실행 비용은 신규 달만큼이다.

실행:  python krx_panel.py            # 2010-01 ~ 최근 월말까지 증분 수집·재구성 (캐시된 달은 즉시)
       python krx_panel.py --start 2015-01
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import krx_api  # noqa: E402

OUT_DIR = os.path.join(BASE, "output", "krx_panel")
PANEL_PQ = os.path.join(OUT_DIR, "panel_monthly.parquet")
PANEL_CSV = os.path.join(OUT_DIR, "panel_monthly.csv")
EVENTS_CSV = os.path.join(OUT_DIR, "listing_events.csv")
INDEX_CSV = os.path.join(OUT_DIR, "index_monthly.csv")
FIRST_MONTH = "2010-01"                  # KRX Open API 커버리지 시작(2009 이하 빈 응답)
INDEX_NAMES = ["코스피", "코스닥", "코스피 200", "코스닥 150"]
SPLIT_ABS = 0.8                          # 월간 |수익률| 이 이 값을 넘으면 코퍼레이트액션 의심

log = logging.getLogger("krx_panel")


def _setup_logging():
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    path = os.path.join(BASE, "logs", f"krx_panel_{dt.datetime.now():%Y%m%d_%H%M%S}.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    logging.getLogger("krx").setLevel(logging.WARNING)


# ------------------------------------------------------------------ 월말 거래일
def month_ends(start: str, end: dt.date) -> list[dt.date]:
    """start('YYYY-MM') 부터 end 가 속한 달까지 각 달의 마지막 달력일(거래일은 아래서 탐색)."""
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while (y, m) <= (end.year, end.month):
        nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append(nxt - dt.timedelta(days=1))
        y, m = nxt.year, nxt.month
    return out


def last_trading_day_of(month_last: dt.date) -> dt.date | None:
    """말일부터 최대 10일 거슬러 코스피 지수 응답이 있는 첫 날 (휴장일 캐시됨)."""
    d = month_last
    for _ in range(10):
        if d.weekday() < 5 and krx_api.fetch("kospi", d):
            return d
        d -= dt.timedelta(days=1)
    return None


# ------------------------------------------------------------------ 한 달 스냅샷
def snapshot(d: dt.date) -> pd.DataFrame:
    rows = []
    for svc, mkt in (("stock", "KOSPI"), ("ksq", "KOSDAQ")):
        for r in krx_api.fetch(svc, d):
            close = krx_api.num(r.get("TDD_CLSPRC"))
            if close is None:
                continue
            code = str(r.get("ISU_CD", "")).zfill(6)
            name = str(r.get("ISU_NM", "")).strip()
            if "스팩" in name.replace(" ", ""):      # 스팩 제외 (2026-09-03 사용자 요청)
                continue
            rows.append({
                "date": pd.Timestamp(d), "code": code, "name": name,
                "market": r.get("MKT_NM") or mkt, "sect": r.get("SECT_TP_NM") or "",
                "close": close,
                "open": krx_api.num(r.get("TDD_OPNPRC")), "high": krx_api.num(r.get("TDD_HGPRC")),
                "low": krx_api.num(r.get("TDD_LWPRC")),
                "volume": krx_api.num(r.get("ACC_TRDVOL")), "value": krx_api.num(r.get("ACC_TRDVAL")),
                "mktcap": krx_api.num(r.get("MKTCAP")), "shares": krx_api.num(r.get("LIST_SHRS")),
                # 우선주: 표준코드 6번째 자리가 0 이 아니면 우선주(005935 삼성전자우)
                "is_pref": code[-1] != "0",
            })
    return pd.DataFrame(rows)


def index_snapshot(d: dt.date) -> dict:
    out = {"date": pd.Timestamp(d)}
    for nm in INDEX_NAMES:
        try:
            r = krx_api.index_row(nm, d)
            out[nm] = r["close"] if r else np.nan
        except Exception as e:
            log.warning("%s %s 지수 실패: %s", d, nm, e)
            out[nm] = np.nan
    return out


# ------------------------------------------------------------------ 패널 조립
def assemble(months: list[pd.DataFrame]) -> pd.DataFrame:
    p = pd.concat(months, ignore_index=True)
    p = p.drop_duplicates(subset=["date", "code"], keep="last").sort_values(["code", "date"])
    p = p.reset_index(drop=True)
    g = p.groupby("code", sort=False)
    p["ret_1m"] = g["close"].pct_change()
    nxt = g["close"].shift(-1)
    p["fwd_ret_1m"] = nxt / p["close"] - 1
    # 연속성 검사: 다음 행이 '바로 다음 달'이 아니면 (재상장·장기 거래정지) 수익률 무효
    dates_sorted = np.sort(p["date"].unique())
    pos = {d: i for i, d in enumerate(dates_sorted)}
    idx = p["date"].map(pos)
    prev_idx, next_idx = g_shift(p, idx, 1), g_shift(p, idx, -1)
    p.loc[(idx - prev_idx) != 1, "ret_1m"] = np.nan
    p.loc[(next_idx - idx) != 1, "fwd_ret_1m"] = np.nan
    p["last_month"] = next_idx.isna() & (idx < len(dates_sorted) - 1)
    p["split_flag"] = p["ret_1m"].abs() > SPLIT_ABS
    return p.sort_values(["date", "code"]).reset_index(drop=True)


def g_shift(p: pd.DataFrame, s: pd.Series, n: int) -> pd.Series:
    return s.groupby(p["code"]).shift(n)


def listing_events(p: pd.DataFrame, latest: pd.Timestamp) -> pd.DataFrame:
    g = p.groupby("code")
    ev = pd.DataFrame({
        "name": g["name"].last(), "market": g["market"].last(),
        "first_seen": g["date"].min(), "last_seen": g["date"].max(),
        "months": g.size(), "last_close": g["close"].last(),
        "last_mktcap": g["mktcap"].last(),
    })
    ev["status"] = np.where(ev["last_seen"] == latest, "listed", "gone")
    ev["is_pref"] = g["is_pref"].last()
    return ev.sort_values(["status", "last_seen", "code"]).reset_index()


# ------------------------------------------------------------------ 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=FIRST_MONTH, help="YYYY-MM (기본 2010-01)")
    args = ap.parse_args()
    _setup_logging()
    t0 = time.time()

    today = dt.date.today()
    # 이번 달은 아직 끝나지 않았으므로 지난달까지
    end = (today.replace(day=1) - dt.timedelta(days=1))
    targets = month_ends(args.start, end)
    log.info("대상 %d개월 (%s ~ %s)", len(targets), targets[0].strftime("%Y-%m"), end.strftime("%Y-%m"))

    months, idx_rows, n_calls = [], [], 0
    for i, ml in enumerate(targets, 1):
        d = last_trading_day_of(ml)
        if d is None:
            log.warning("%s 거래일 없음(응답 없음) — 건너뜀", ml.strftime("%Y-%m"))
            continue
        snap = snapshot(d)
        if snap.empty:
            log.warning("%s 종목 응답 없음", d)
            continue
        months.append(snap)
        idx_rows.append(index_snapshot(d))
        if i % 12 == 0 or i == len(targets):
            log.info("  %s 까지 %d개월, 이번 달 %d종목 (%.0f초)", d, len(months), len(snap), time.time() - t0)

    if not months:
        log.error("수집된 달이 없음")
        return 1
    panel = assemble(months)
    latest = panel["date"].max()
    events = listing_events(panel, latest)
    index_df = pd.DataFrame(idx_rows).sort_values("date")

    os.makedirs(OUT_DIR, exist_ok=True)
    panel.to_parquet(PANEL_PQ, index=False)
    panel.to_csv(PANEL_CSV, index=False, encoding="utf-8-sig")
    events.to_csv(EVENTS_CSV, index=False, encoding="utf-8-sig")
    index_df.to_csv(INDEX_CSV, index=False, encoding="utf-8-sig")

    gone = events[events["status"] == "gone"]
    log.info("패널 %s행 × %d개월, 종목 %d개 (현재 상장 %d · 사라진 종목 %d)",
             f"{len(panel):,}", panel["date"].nunique(), events["code"].nunique(),
             (events["status"] == "listed").sum(), len(gone))
    log.info("사라진 종목 연도별: %s",
             gone["last_seen"].dt.year.value_counts().sort_index().to_dict())
    log.info("저장: %s (%.1f분)", OUT_DIR, (time.time() - t0) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
