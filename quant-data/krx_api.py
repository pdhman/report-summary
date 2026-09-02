# -*- coding: utf-8 -*-
"""
KRX 정보데이터시스템 Open API 공용 클라이언트 (openapi.krx.co.kr, 2026-09-02 승인).

승인 서비스 → 엔드포인트 (전부 GET, 헤더 AUTH_KEY, 파라미터 basDd=YYYYMMDD):
  파생상품지수 시세정보      idx/drvprod_dd_trd   → VKOSPI(코스피200 변동성지수)
  옵션 일별매매정보(주식옵션外) drv/opt_bydd_trd     → 풋콜비율(코스피200옵션 거래량)
  KOSPI 시리즈 일별시세정보  idx/kospi_dd_trd     → 코스피 지수 종가 (네이버 폴백용)
  KRX 시리즈 일별시세정보    idx/krx_dd_trd
  유가증권 일별매매정보      sto/stk_bydd_trd     → 전종목 종가·시총 (네이버 폴백용)
  ETF 일별매매정보           etp/etf_bydd_trd

특징:
  - 하루 단위 조회만 된다(기간 조회 없음). 응답 {"OutBlock_1":[...]}, 휴장일은 빈 목록.
  - 확정 데이터라 (서비스, 날짜) 단위로 디스크 캐시한다 → 백필 재실행 시 재호출 없음.
  - 키는 클로드코드/secrets.json 의 krx_api_key (gitignore). 비어 있으면 KrxKeyMissing.
  - 숫자는 "1,234.56" 문자열, 결측은 "-" 로 온다 → num() 으로 변환.

사용:  from krx_api import vkospi, putcall_ratio, kospi_close
       vkospi("20260901") → 44.03 / None(휴장)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(BASE)
SECRETS = os.path.join(PROJ, "secrets.json")
CACHE_DIR = os.path.join(BASE, "cache", "krx")
API = "https://data-dbg.krx.co.kr/svc/apis/"

SERVICES = {
    "drvidx": "idx/drvprod_dd_trd",
    "option": "drv/opt_bydd_trd",
    "kospi": "idx/kospi_dd_trd",
    "krx": "idx/krx_dd_trd",
    "stock": "sto/stk_bydd_trd",
    "etf": "etp/etf_bydd_trd",
}

log = logging.getLogger("krx")


class KrxKeyMissing(RuntimeError):
    pass


def _key() -> str:
    try:
        with open(SECRETS, encoding="utf-8") as f:
            k = (json.load(f).get("krx_api_key") or "").strip()
    except FileNotFoundError:
        k = ""
    if not k:
        raise KrxKeyMissing("secrets.json 에 krx_api_key 가 없습니다 (마이페이지 → API 인증키)")
    return k


def num(v):
    """'1,234.5' → 1234.5, '-'/'' → None."""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _ymd(d) -> str:
    if isinstance(d, (dt.date, dt.datetime)):
        return f"{d:%Y%m%d}"
    return str(d).replace("-", "")[:8]


def fetch(service: str, bas_dd, use_cache: bool = True, retries: int = 3) -> list[dict]:
    """서비스 한 날짜 조회. 휴장일은 []."""
    ymd = _ymd(bas_dd)
    path = os.path.join(CACHE_DIR, service, f"{ymd}.json")
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    key = _key()
    url = API + SERVICES[service]
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params={"basDd": ymd}, headers={"AUTH_KEY": key}, timeout=20)
            if r.status_code == 401:
                raise KrxKeyMissing(f"KRX 인증 실패: {r.text[:80]}")
            r.raise_for_status()
            rows = r.json().get("OutBlock_1", [])
            break
        except KrxKeyMissing:
            raise
        except Exception as e:                      # 네트워크·5xx → 재시도
            last = e
            time.sleep(1.5 * (i + 1))
    else:
        raise RuntimeError(f"KRX {service} {ymd} 조회 실패: {last}")

    # 오늘 날짜는 장중 미확정일 수 있어(빈 목록) 캐시하지 않는다
    if use_cache and ymd < _ymd(dt.date.today()):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
    return rows


# ------------------------------------------------------------------ 지표 헬퍼
def vkospi(bas_dd) -> float | None:
    """코스피200 변동성지수 종가. 파생상품지수 시세정보 IDX_NM '코스피 200 변동성지수'.

    '목표변동성 24%'·'변동성매칭 양매도' 등 유사 이름이 6개나 있어 정확 일치로 찾는다.
    """
    for row in fetch("drvidx", bas_dd):
        if str(row.get("IDX_NM", "")).replace(" ", "") == "코스피200변동성지수":
            return num(row.get("CLSPRC_IDX"))
    return None


def putcall_ratio(bas_dd, by: str = "volume") -> float | None:
    """코스피200옵션 풋/콜 비율. by='volume'(거래량) 또는 'oi'(미결제약정).

    옵션 일별매매정보는 미니·위클리·코스닥150 옵션까지 한 목록에 오므로
    PROD_NM 이 정확히 '코스피200 옵션'(공백 유무 무관)인 행만 쓴다.
    """
    col = "ACC_TRDVOL" if by == "volume" else "ACC_OPNINT_QTY"
    call = put = 0.0
    for row in fetch("option", bas_dd):
        prod = str(row.get("PROD_NM", "")).replace(" ", "")
        if prod != "코스피200옵션":
            continue
        v = num(row.get(col)) or 0.0
        side = str(row.get("RGHT_TP_NM", "")).upper()
        if side.startswith("C") or side == "콜":
            call += v
        elif side.startswith("P") or side == "풋":
            put += v
    if call <= 0:
        return None
    return round(put / call, 3)


def kospi_close(bas_dd, name: str = "코스피") -> float | None:
    """KOSPI 시리즈 지수 종가 (기본 '코스피' = KOSPI 종합)."""
    for row in fetch("kospi", bas_dd):
        if str(row.get("IDX_NM", "")).strip() == name:
            return num(row.get("CLSPRC_IDX"))
    return None


def series(fn, start, end=None, **kw) -> dict:
    """start~end 달력일을 돌며 fn(날짜) 값을 모은다(휴장·None 제외). 백필용."""
    start = pd_date(start)
    end = pd_date(end) if end else dt.date.today()
    out, d = {}, start
    while d <= end:
        if d.weekday() < 5:
            try:
                v = fn(d, **kw)
            except KrxKeyMissing:
                raise
            except Exception as e:
                log.warning("KRX %s %s: %s", getattr(fn, "__name__", fn), d, e)
                v = None
            if v is not None:
                out[d] = v
        d += dt.timedelta(days=1)
    return out


def pd_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    s = str(x).replace("-", "")[:8]
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


if __name__ == "__main__":                          # 연결 점검: python krx_api.py [YYYYMMDD]
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = sys.argv[1] if len(sys.argv) > 1 else _ymd(dt.date.today() - dt.timedelta(days=1))
    for svc in SERVICES:
        rows = fetch(svc, d, use_cache=False)
        print(f"{svc:7s} {len(rows):5d}행  필드: {list(rows[0].keys())[:12] if rows else '-'}")
    print("VKOSPI:", vkospi(d))
    print("풋콜(거래량):", putcall_ratio(d), " 풋콜(미결제):", putcall_ratio(d, by="oi"))
    print("코스피 종가:", kospi_close(d))
