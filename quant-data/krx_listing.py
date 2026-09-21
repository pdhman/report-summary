# -*- coding: utf-8 -*-
"""
FinanceDataReader.StockListing 대체/폴백 — KRX Open API 기반 전종목 스냅샷.

배경(2026-09-09): FDR 0.9.202 의 StockListing('KRX')는 자체 서버가 아니라 GitHub
캐시 저장소(FinanceData/fdr_krx_data_cache)의 날짜별 CSV 를 읽는다. 그 캐시가
2026-09-07 파일을 끝으로 갱신이 멈춰 09-08 부터 404 → 오늘의 주도주(주도섹터 필터링.py)
유니버스가 비어 0종목, 금요일 QuantDataWeekly(collect.py)도 같은 경로.

제공:
  stock_listing(date=None)  → FDR 'KRX' 스키마 DataFrame.
      date 미지정: 네이버 당일 스냅샷(m.stock.naver.com/api/stocks/marketValue, 주식만)
      → 실패 시 KRX Open API(당일 없으면 직전 거래일). date 지정: KRX 만.
      Code, ISU_CD, Name, Market, Dept, Close, ChangeCode, Changes, ChagesRatio,
      Open, High, Low, Volume, Amount, Marcap, Stocks, MarketId
      (KOSPI + KOSDAQ. KONEX 는 KRX 서비스 미승인이라 없음. 'KOSDAQ GLOBAL' 구분 없음)
  stock_listing_desc()      → FDR 'KRX-DESC' 스키마: Code, Name, Market, Sector, Industry
      (quant-data/output/퀀트데이터_latest.csv 의 섹터·업종 — 주 1회 갱신)
  install()                 → fdr.StockListing 을 감싼다. 'KRX'/'KOSPI'/'KOSDAQ' 는
      네이버 당일 스냅샷 → KRX → (마지막) fdr 원본 순. FDR 캐시는 장중 스냅샷이라
      거래대금이 불완전할 수 있어 신뢰하지 않는다. 'KRX-DESC' 는 fdr → 퀀트데이터.

날짜: 기본은 오늘. KRX 는 당일 시세를 장 마감 뒤 게시하므로 아직 없으면 직전
거래일로 내려간다(어느 날짜를 썼는지 로그). 유니버스 1차 필터(시총·거래대금)에는
전일 값으로도 충분하다.

사용:  import sys; sys.path.insert(0, ".../quant-data"); import krx_listing; krx_listing.install()
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import krx_api  # noqa: E402

UNI_CSV = os.path.join(BASE, "output", "퀀트데이터_latest.csv")
log = logging.getLogger("krx_listing")


def _rows_for(date: dt.date) -> tuple[dt.date | None, list[tuple[str, dict]]]:
    """date 부터 최대 7일 거슬러 데이터가 있는 첫 날의 (날짜, [(시장, 행)])."""
    d = date
    for _ in range(7):
        if d.weekday() < 5:
            out = []
            for svc, mkt in (("stock", "KOSPI"), ("ksq", "KOSDAQ")):
                try:
                    out += [(mkt, r) for r in krx_api.fetch(svc, d)]
                except krx_api.KrxNotApproved as e:
                    log.warning("%s", e)
            if out:
                return d, out
        d -= dt.timedelta(days=1)
    return None, []


NAVER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}
_NAVER_LIST = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"


def _naver_snapshot() -> pd.DataFrame:
    """네이버 당일 전종목 스냅샷 (시총순 목록, 페이지당 100 최대). 거래대금 백만원·시총 억원 → 원.

    KRX Open API 는 당일 시세를 장 마감 뒤에야 주므로 15:35 스크리너에는 이쪽이 맞다.
    ETF·ETN 등은 stockEndType != 'stock' 으로 제외 (FDR 'KRX' 목록도 주식만).
    """
    import requests
    def num(v):
        s = str(v).replace(",", "").strip() if v is not None else ""
        if s in ("", "-", "N/A"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    # 시총순 '실시간 정렬' 목록을 100개씩 페이지로 받으므로, 요청 사이에 순위가 바뀌면
    # 페이지 경계의 종목이 두 번 잡히고(중복) 자리를 맞바꾼 종목은 빠진다(누락).
    # 2026-09-18 실사고: 036170 중복행이 퀀트데이터에 실려 온도계 빌드가 죽음.
    # → 한 번 훑는 동안 중복이 보이면 다시 훑어 합집합을 만들고, 코드 기준으로 유일화한다.
    by_code, latest = {}, None
    for attempt in range(3):
        recs, lt = _naver_one_pass(num)
        latest = max(latest or lt, lt) if lt else latest
        seen, dups = set(), 0
        for rec in recs:
            if rec["Code"] in seen:
                dups += 1
                continue
            seen.add(rec["Code"])
            by_code.setdefault(rec["Code"], rec)
        if dups == 0:
            break
        log.warning("네이버 목록 페이지 경계 이동 감지(중복 %d건) — 누락 보정을 위해 재수집 %d/2", dups, attempt + 1)
    recs = list(by_code.values())
    if len(recs) < 1500:
        raise RuntimeError(f"네이버 스냅샷 종목 수 비정상: {len(recs)}")
    df = pd.DataFrame(recs).sort_values("Marcap", ascending=False).reset_index(drop=True)
    df.attrs["date"] = latest
    df.attrs["source"] = "naver"
    return df


def _naver_one_pass(num) -> tuple[list[dict], str | None]:
    """네이버 시총순 목록을 KOSPI·KOSDAQ 전 페이지 한 번 훑는다 (중복 제거는 호출부)."""
    import requests
    recs, latest = [], None
    for market in ("KOSPI", "KOSDAQ"):
        page = 1
        while True:
            r = requests.get(_NAVER_LIST.format(market=market, page=page), headers=NAVER_UA, timeout=15)
            r.raise_for_status()
            body = r.json()
            items = body.get("stocks") or []
            for it in items:
                if it.get("stockEndType") != "stock":
                    continue
                close = num(it.get("closePrice"))
                mcap = num(it.get("marketValue"))
                if close is None or not mcap:
                    continue
                chg = num(it.get("compareToPreviousClosePrice")) or 0.0
                code = (it.get("compareToPreviousPrice") or {}).get("code")
                if code in ("4", "5"):                      # 4 하한, 5 하락
                    chg = -abs(chg)
                ts = str(it.get("localTradedAt", ""))[:10]
                latest = max(latest or ts, ts)
                recs.append({
                    "Code": str(it["itemCode"]).zfill(6), "ISU_CD": None, "Name": it.get("stockName"),
                    "Market": market, "Dept": None, "Close": close,
                    "ChangeCode": 1 if chg > 0 else 2 if chg < 0 else 3, "Changes": chg,
                    "ChagesRatio": num(it.get("fluctuationsRatio")),
                    "Open": None, "High": None, "Low": None,
                    "Volume": num(it.get("accumulatedTradingVolume")) or 0.0,
                    # 단위: accumulatedTradingValue 백만원, marketValue 억원 (Hangeul 필드로 검증)
                    "Amount": (num(it.get("accumulatedTradingValue")) or 0.0) * 1e6,
                    "Marcap": mcap * 1e8, "Stocks": round(mcap * 1e8 / close) if close else 0.0,
                    "MarketId": "STK" if market == "KOSPI" else "KSQ",
                })
            total = int(body.get("totalCount") or 0)
            if len(items) < 100 or page * 100 >= total:
                break
            page += 1
    return recs, latest


def stock_listing(date: dt.date | str | None = None) -> pd.DataFrame:
    """전종목 목록(FDR 'KRX' 스키마). 날짜 미지정이면 네이버 당일 스냅샷 → KRX 순."""
    if date is None:
        try:
            df = _naver_snapshot()
            log.info("종목 목록: 네이버 당일 스냅샷 %s, %d종목", df.attrs["date"], len(df))
            return df
        except Exception as e:
            log.warning("네이버 스냅샷 실패(%s) → KRX Open API", str(e)[:80])
    want = krx_api.pd_date(date) if date else dt.date.today()
    used, rows = _rows_for(want)
    if not rows:
        raise RuntimeError(f"KRX Open API 에 {want} 이전 7일치 종목 데이터가 없음")
    if used != want:
        log.info("KRX 종목 목록: %s 데이터 없음 → %s 사용", want, used)
    n = krx_api.num
    recs = []
    for mkt, r in rows:
        close = n(r.get("TDD_CLSPRC"))
        if close is None:
            continue
        chg = n(r.get("CMPPREVDD_PRC")) or 0.0
        recs.append({
            "Code": str(r.get("ISU_CD", "")).zfill(6), "ISU_CD": None,
            "Name": str(r.get("ISU_NM", "")).strip(), "Market": r.get("MKT_NM") or mkt,
            "Dept": r.get("SECT_TP_NM") or None, "Close": close,
            "ChangeCode": 1 if chg > 0 else 2 if chg < 0 else 3, "Changes": chg,
            "ChagesRatio": n(r.get("FLUC_RT")), "Open": n(r.get("TDD_OPNPRC")),
            "High": n(r.get("TDD_HGPRC")), "Low": n(r.get("TDD_LWPRC")),
            "Volume": n(r.get("ACC_TRDVOL")) or 0.0, "Amount": n(r.get("ACC_TRDVAL")) or 0.0,
            "Marcap": n(r.get("MKTCAP")) or 0.0, "Stocks": n(r.get("LIST_SHRS")) or 0.0,
            "MarketId": "STK" if (r.get("MKT_NM") or mkt) == "KOSPI" else "KSQ",
        })
    df = pd.DataFrame(recs).sort_values("Marcap", ascending=False).reset_index(drop=True)
    df.attrs["date"] = used
    df.attrs["source"] = "krx"
    return df


def stock_listing_desc() -> pd.DataFrame:
    u = pd.read_csv(UNI_CSV, dtype={"코드": str}, usecols=["코드", "회사명", "시장", "섹터", "업종"])
    u["코드"] = u["코드"].str.zfill(6)
    return u.rename(columns={"코드": "Code", "회사명": "Name", "시장": "Market",
                             "섹터": "Sector", "업종": "Industry"})


def install():
    """fdr.StockListing 을 감싼다: 원본이 실패하거나 비면 KRX Open API 로 폴백."""
    import FinanceDataReader as fdr
    if getattr(fdr, "_krx_listing_installed", False):
        return
    orig = fdr.StockListing

    def patched(market, *a, **k):
        m = str(market).upper()
        # 시세 목록은 FDR 보다 네이버 당일 스냅샷을 먼저 쓴다 (2026-09-16 실사고):
        # 되살아난 FDR 캐시가 장중 이른 시각 스냅샷이라 거래대금이 반토막(삼성전자
        # 12,275억 vs 실제 28,234억) → 1차 필터 103→53종목, 9/11·9/15·9/16 선정 0~1개.
        # FDR 은 네이버·KRX 모두 실패했을 때만.
        if m in ("KRX", "KOSPI", "KOSDAQ"):
            try:
                df = stock_listing()
                return df if m == "KRX" else df[df["Market"] == m].reset_index(drop=True)
            except Exception as e:
                log.warning("네이버/KRX 종목 목록 실패(%s) → fdr 원본 시도", str(e)[:60])
                return orig(market, *a, **k)
        try:
            df = orig(market, *a, **k)
            if df is not None and not df.empty:
                return df
            raise RuntimeError("빈 응답")
        except Exception as e:
            if m == "KRX-DESC":
                log.warning("fdr.StockListing(KRX-DESC) 실패(%s) → 퀀트데이터 섹터·업종 폴백", str(e)[:60])
                return stock_listing_desc()
            raise

    fdr.StockListing = patched
    fdr._krx_listing_installed = True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = stock_listing()
    print(f"목록({df.attrs['source']}) {df.attrs['date']}: {len(df)}종목", df["Market"].value_counts().to_dict())
    print("1차 필터(시총 3000억↑·거래대금 300억↑):", int(((df.Marcap >= 3e11) & (df.Amount >= 3e10)).sum()), "종목")
    print(df.head(3).to_string())
    print("KRX-DESC:", stock_listing_desc().shape)
