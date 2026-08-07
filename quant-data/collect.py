# -*- coding: utf-8 -*-
"""
전종목 퀀트데이터 주간 수집기 — 실적·컨센서스(E) 중심.

소스 (KRX 로그인 불필요):
  - FinanceDataReader StockListing('KRX')      : 시세·시가총액·거래대금 스냅샷
  - FinanceDataReader StockListing('KRX-DESC') : 업종
  - FinanceDataReader DataReader(일봉, 네이버)  : 기간수익률·RS·52주고저·MA200·변동성
    (cache/ohlcv.parquet 에 증분 캐싱 — sepa/data_cache.py 와 같은 방식)
  - 네이버 모바일 증권 API (m.stock.naver.com)  : 연간 실적 + 컨센서스(E)
    매출액·영업이익·당기순이익·지배주주순이익·ROE·부채비율·EPS·BPS·DPS
    (cache/finance/<code>.json 에 TTL 캐싱)

출력: output/퀀트데이터_YYYYMMDD.csv (+ 퀀트데이터_latest.csv 복사본, UTF-8 BOM)

주의:
  - 일봉은 네이버 소스라 수정주가가 아닐 수 있음(액면분할 직후 종목의
    기간수익률은 왜곡 가능 — 드물지만 해석 시 유의).
  - 컨센서스(E)는 애널리스트 커버리지가 있는 종목만 채워진다(그 외 공란).

실행:  python collect.py            # 전체 (첫 실행은 일봉 백필로 10~20분)
       python collect.py --no-prices    # 일봉 캐시 갱신 생략
       python collect.py --no-finance   # 재무/컨센서스 수집 생략
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import FinanceDataReader as fdr

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, "cache")
FIN_CACHE_DIR = os.path.join(CACHE_DIR, "finance")
OHLCV_PATH = os.path.join(CACHE_DIR, "ohlcv.parquet")
OUT_DIR = os.path.join(BASE, "output")
LOG_DIR = os.path.join(BASE, "logs")

LOOKBACK_DAYS = 420          # 1년 수익률 + 52주 고저 계산에 충분한 달력일수
PRICE_WORKERS = 4            # FDR 동시 요청 수 (sepa 와 동일하게 보수적으로)
PRICE_SLEEP = 0.1
FIN_WORKERS = 6              # 네이버 재무 API 동시 요청 수
FIN_TTL_DAYS = 5             # 재무 캐시 유효기간 (주 1회 실행이면 매번 갱신됨)
FIN_FAIL_TTL_DAYS = 1        # 조회 실패 캐시 유효기간

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json"}

log = logging.getLogger("quant-data")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"collect_{dt.datetime.now():%Y%m%d_%H%M%S}.log")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    return path


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ 유니버스
def build_universe() -> pd.DataFrame:
    """코스피·코스닥 전종목 스냅샷 (시세·시총·거래대금·업종)."""
    krx = fdr.StockListing("KRX")
    uni = krx[krx["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    uni["Code"] = uni["Code"].astype(str).str.zfill(6)

    try:
        # KRX-DESC 의 Sector 는 코스닥 소속부(KOSPI 는 공란) — 업종은 Industry 컬럼
        desc = fdr.StockListing("KRX-DESC")
        desc["Code"] = desc["Code"].astype(str).str.zfill(6)
        desc = desc.drop_duplicates(subset="Code")
        desc = desc[["Code", "Industry"]].rename(columns={"Industry": "Sector"})
        uni = uni.merge(desc, on="Code", how="left")
    except Exception as ex:
        log.warning("업종(KRX-DESC) 조회 실패 — 업종 공란으로 진행: %s", ex)
        uni["Sector"] = None

    log.info("유니버스: %d종목 (KOSPI %d / KOSDAQ %d)", len(uni),
             (uni["Market"] == "KOSPI").sum(), (uni["Market"] == "KOSDAQ").sum())
    return uni


# ------------------------------------------------------------------ 일봉 캐시
def _fetch_close(code: str, start: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            time.sleep(PRICE_SLEEP)
            df = fdr.DataReader(code, start)
            if df is None or df.empty:
                return None
            out = df.reset_index()[["Date", "Close"]]
            out.columns = ["date", "close"]
            out.insert(1, "code", code)
            return out
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5 * (attempt + 1))


def update_price_cache(codes: list[str]) -> pd.DataFrame:
    """일봉 종가를 수신해 parquet 캐시를 최신화하고 전체를 반환.

    전 구간을 매번 다시 받는다(증분 아님). 액면분할·액면병합·감자가 있으면
    네이버가 과거 시세를 소급 조정하는데, 새 날짜만 덧붙이면 옛 행은 조정 전
    기준으로 남아 가짜 급등이 만들어진다. 실제로 2026-08 비큐AI(5:1 병합)가
    1주수익률 +750%, 52주고점대비 0% 로 잡혀 RS 상위를 차지한 사례가 있었다.
    종목당 HTTP 요청 수는 증분이든 전체든 1건으로 같아 소요 시간 차이도 거의 없다.
    """
    full_start = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    ref = fdr.DataReader("005930", (dt.date.today() - dt.timedelta(days=14)).isoformat())
    latest = pd.Timestamp(ref.index[-1])

    cached = None
    if os.path.exists(OHLCV_PATH):
        cached = pd.read_parquet(OHLCV_PATH)
        cached["date"] = pd.to_datetime(cached["date"])
        cached = cached[cached["code"].isin(set(codes))]

    # 수신 실패 종목만 캐시본을 유지하고, 성공한 종목은 새로 받은 값으로 덮어쓴다
    plan: dict[str, str] = {c: full_start for c in codes}

    log.info("일봉 수신 %d종목 (기준일 %s) ...", len(plan), latest.date())
    frames, done, fail = [], 0, 0
    with ThreadPoolExecutor(max_workers=PRICE_WORKERS) as pool:
        futs = {pool.submit(_fetch_close, c, s): c for c, s in plan.items()}
        for fut in as_completed(futs):
            df = fut.result()
            if df is not None:
                frames.append(df)
            else:
                fail += 1
            done += 1
            if done % 300 == 0:
                log.info("  일봉 진행 %d/%d", done, len(plan))

    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not new.empty:
        new["date"] = pd.to_datetime(new["date"])
        # 수신 성공한 종목은 캐시본을 통째로 버린다 — 조정 전 기준의 옛 행이
        # 한 줄이라도 남으면 그 지점에서 가짜 급등/급락이 생긴다
        if cached is not None:
            cached = cached[~cached["code"].isin(set(new["code"]))]
    merged = pd.concat([cached, new], ignore_index=True) if cached is not None else new
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged[merged["date"] >= pd.Timestamp(full_start)]
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)

    os.makedirs(CACHE_DIR, exist_ok=True)
    merged.to_parquet(OHLCV_PATH, index=False)
    log.info("일봉 캐시 저장: %d종목 / %s행 (수신실패 %d)",
             merged["code"].nunique(), f"{len(merged):,}", fail)
    return merged


# ------------------------------------------------------------------ 모멘텀 팩터
PRICE_LIMIT = 0.32          # 국내 일일 등락 한도 30% + 여유

def adjust_corporate_actions(wide: pd.DataFrame) -> pd.DataFrame:
    """감자·액면병합·액면분할로 생긴 미조정 갭을 소급 보정한다.

    국내 주식은 하루 등락이 ±30% 로 제한되므로 그 밖의 점프는 가격 자체가
    재편된 것(감자·병합 등)이다. 네이버 일봉은 이런 이벤트를 소급 조정하지
    않는 경우가 있어 보정 없이 쓰면 가짜 급등이 수익률·RS 를 오염시킨다
    (2026-05 에이프로젠 13.9배, 참엔지니어링 5.0배 등 실제 사례).

    이벤트 시점의 배수를 그 이전 가격에 곱해 시계열을 연속으로 만든다.
    마지막 행에는 이후 이벤트가 없으므로 현재가는 그대로 유지된다.
    """
    w = wide.ffill()                       # 거래정지 등 중간 결측 건너뛰기
    ratio = w / w.shift(1)
    ev = (ratio > 1 + PRICE_LIMIT) | (ratio < 1 - PRICE_LIMIT)
    if not ev.to_numpy().any():
        return wide
    f = ratio.where(ev, 1.0).fillna(1.0)
    # factor[i] = i 이후 모든 이벤트 배수의 곱
    factor = f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    n = int(ev.to_numpy().sum())
    log.info("미조정 코퍼레이트액션 보정: %d건 / %d종목",
             n, int(ev.any().sum()))
    return wide * factor


def momentum_factors(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """기간수익률·RS 백분위·52주 고저 대비·MA200 대비·변동성."""
    wide = ohlcv.pivot_table(index="date", columns="code", values="close").sort_index()
    wide = adjust_corporate_actions(wide)
    last = wide.ffill().iloc[-1]

    out = pd.DataFrame({"코드": last.index, "주가": last.values}).set_index("코드")
    windows = {"1주": 5, "1개월": 21, "3개월": 63, "6개월": 126, "1년": 252}
    for name, k in windows.items():
        if len(wide) <= k:
            out[f"{name}수익률(%)"] = None
            continue
        base = wide.shift(k).ffill().iloc[-1]
        out[f"{name}수익률(%)"] = ((last / base - 1) * 100).round(2)

    # RS: 기간수익률의 시장 내 백분위 (0~100, 퀀트킹과 같은 방식)
    for name in ("1개월", "3개월", "6개월", "1년"):
        col = f"{name}수익률(%)"
        if col in out and out[col].notna().any():
            out[f"RS_{name}"] = (out[col].rank(pct=True) * 100).round(1)
        else:
            out[f"RS_{name}"] = None

    w52 = wide.tail(252)
    out["52주고점대비(%)"] = ((last / w52.max() - 1) * 100).round(2)
    out["52주저점대비(%)"] = ((last / w52.min() - 1) * 100).round(2)
    ma200 = wide.tail(200).mean()
    out["MA200대비(%)"] = ((last / ma200 - 1) * 100).round(2)
    ret = wide.tail(61).pct_change()
    out["변동성60일(%)"] = (ret.std() * (252 ** 0.5) * 100).round(1)
    return out.drop(columns=["주가"]).reset_index()


# ------------------------------------------------------------------ 섹터·업종·테마
GROUPS_CACHE = os.path.join(CACHE_DIR, "groups.json")
GROUPS_TTL_DAYS = 5

# 네이버 업종(WICS 산업 79개) → 섹터(WICS 중분류 스타일 28개) 정적 매핑.
# 새 업종이 생기면 '기타' 로 들어가고 로그에 경고가 남는다.
SECTOR_MAP = {
    "에너지장비및서비스": "에너지", "석유와가스": "에너지",
    "화학": "소재", "건축자재": "소재", "포장재": "소재", "철강": "소재",
    "비철금속": "소재", "종이와목재": "소재",
    "우주항공과국방": "자본재", "건축제품": "자본재", "건설": "자본재",
    "전기장비": "자본재", "복합기업": "자본재", "기계": "자본재",
    "조선": "자본재", "무역회사와판매업체": "자본재",
    "상업서비스와공급품": "상업서비스",
    "항공화물운송과물류": "운송", "항공사": "운송", "해운사": "운송",
    "도로와철도운송": "운송", "운송인프라": "운송",
    "자동차": "자동차와부품", "자동차부품": "자동차와부품",
    "가구": "내구소비재와의류", "가정용기기와용품": "내구소비재와의류",
    "레저용장비와제품": "내구소비재와의류", "섬유,의류,신발,호화품": "내구소비재와의류",
    "문구류": "내구소비재와의류",
    "호텔,레스토랑,레저": "호텔레스토랑레저", "다각화된소비자서비스": "호텔레스토랑레저",
    "교육서비스": "호텔레스토랑레저",
    "전문소매": "소매(유통)", "판매업체": "소매(유통)",
    "인터넷과카탈로그소매": "소매(유통)", "백화점과일반상점": "소매(유통)",
    "식품과기본식료품소매": "식품소매",
    "식품": "식품음료담배", "음료": "식품음료담배", "담배": "식품음료담배",
    "가정용품": "생활용품과화장품", "화장품": "생활용품과화장품",
    "건강관리장비와용품": "건강관리", "건강관리업체및서비스": "건강관리",
    "건강관리기술": "건강관리", "생명과학도구및서비스": "건강관리",
    "제약": "제약과생물공학", "생물공학": "제약과생물공학",
    "은행": "은행", "증권": "증권",
    "기타금융": "다각화된금융", "카드": "다각화된금융", "창업투자": "다각화된금융",
    "생명보험": "보험", "손해보험": "보험",
    "부동산": "부동산",
    "소프트웨어": "소프트웨어와서비스", "IT서비스": "소프트웨어와서비스",
    "통신장비": "기술하드웨어와장비", "컴퓨터와주변기기": "기술하드웨어와장비",
    "전자장비와기기": "기술하드웨어와장비", "사무용전자제품": "기술하드웨어와장비",
    "핸드셋": "기술하드웨어와장비",
    "반도체와반도체장비": "반도체와반도체장비",
    "전자제품": "전자와전기제품", "전기제품": "전자와전기제품",
    "디스플레이패널": "디스플레이", "디스플레이장비및부품": "디스플레이",
    "무선통신서비스": "전기통신서비스", "다각화된통신서비스": "전기통신서비스",
    "광고": "미디어와엔터테인먼트", "방송과엔터테인먼트": "미디어와엔터테인먼트",
    "출판": "미디어와엔터테인먼트", "게임엔터테인먼트": "미디어와엔터테인먼트",
    "양방향미디어와서비스": "미디어와엔터테인먼트",
    "전기유틸리티": "유틸리티", "가스유틸리티": "유틸리티", "복합유틸리티": "유틸리티",
    "기타": "기타",
}


def _api_json(url):
    r = requests.get(url, headers=UA, timeout=12)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _list_groups(kind: str) -> list[dict]:
    """업종/테마 그룹 목록 (no, name)."""
    out, page = [], 1
    while True:
        j = _api_json(f"https://m.stock.naver.com/api/stocks/{kind}?page={page}&pageSize=100")
        groups = (j or {}).get("groups") or []
        out += groups
        if len(groups) < 100:
            break
        page += 1
    return out


def _group_members(kind: str, no) -> list[str]:
    """그룹 소속 종목코드 목록."""
    codes, page = [], 1
    while True:
        j = _api_json(f"https://m.stock.naver.com/api/stocks/{kind}/{no}?page={page}&pageSize=100")
        stocks = (j or {}).get("stocks") or []
        codes += [s["itemCode"] for s in stocks if s.get("itemCode")]
        if len(stocks) < 100:
            break
        page += 1
    return codes


def fetch_group_map() -> pd.DataFrame:
    """코드 → 섹터·업종(네이버)·테마 매핑 (groups.json 캐시, TTL 5일)."""
    if os.path.exists(GROUPS_CACHE):
        age_days = (time.time() - os.path.getmtime(GROUPS_CACHE)) / 86400
        if age_days < GROUPS_TTL_DAYS:
            try:
                with open(GROUPS_CACHE, encoding="utf-8") as f:
                    data = json.load(f)
                log.info("섹터·업종·테마: 캐시 사용 (%.1f일 전)", age_days)
                return pd.DataFrame(data)
            except Exception:
                pass

    industry: dict[str, str] = {}
    themes: dict[str, set[str]] = {}
    try:
        ind_groups = _list_groups("industry")
        thm_groups = _list_groups("theme")
        log.info("섹터·업종·테마 수집: 업종 %d개 / 테마 %d개 그룹 조회 중 ...",
                 len(ind_groups), len(thm_groups))

        # 그룹 API 는 등락률순 실시간 정렬이라 페이지를 넘기는 사이 종목이
        # 밀려나 빠질 수 있다 — 2회 수집해 합집합으로 보정한다
        for _ in range(2):
            with ThreadPoolExecutor(max_workers=FIN_WORKERS) as pool:
                futs = {pool.submit(_group_members, "industry", g["no"]): ("i", g["name"])
                        for g in ind_groups}
                futs.update({pool.submit(_group_members, "theme", g["no"]): ("t", g["name"])
                             for g in thm_groups})
                for fut in as_completed(futs):
                    kind, name = futs[fut]
                    for code in fut.result():
                        if kind == "i":
                            industry.setdefault(code, name)
                        else:
                            themes.setdefault(code, set()).add(name)
    except Exception as ex:
        log.warning("섹터·업종·테마 수집 실패 — 해당 열 공란으로 진행: %s", ex)
        return pd.DataFrame({"코드": []})

    unmapped = {n for n in industry.values() if n not in SECTOR_MAP}
    if unmapped:
        log.warning("SECTOR_MAP 에 없는 업종(섹터='기타' 처리): %s", ", ".join(unmapped))

    codes = sorted(set(industry) | set(themes))
    data = {"코드": codes,
            "섹터": [SECTOR_MAP.get(industry.get(c), "기타") if c in industry else None
                    for c in codes],
            "업종N": [industry.get(c) for c in codes],
            "테마": ["; ".join(sorted(themes[c])) if c in themes else None for c in codes]}
    with open(GROUPS_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    log.info("섹터·업종·테마 확보: 업종 %d종목 / 테마 %d종목", len(industry), len(themes))
    return pd.DataFrame(data)


# ------------------------------------------------------------------ 재무·컨센서스
FIN_ROWS = {"매출액": "매출액(억)", "영업이익": "영업이익(억)",
            "당기순이익": "당기순이익(억)", "지배주주순이익": "지배주주순이익(억)",
            "영업이익률": "영업이익률(%)", "순이익률": "순이익률(%)",
            "ROE": "ROE(%)", "부채비율": "부채비율(%)",
            "EPS": "EPS(원)", "BPS": "BPS(원)", "주당배당금": "DPS(원)"}
EST_ROWS = {"매출액": "매출액(E,억)", "영업이익": "영업이익(E,억)",
            "당기순이익": "당기순이익(E,억)", "ROE": "ROE(E,%)",
            "EPS": "EPS(E,원)", "주당배당금": "DPS(E,원)"}


def _fetch_finance(code: str) -> dict | None:
    """네이버 연간 재무 API → {컬럼명: 값} (FY0 실적 + 첫 컨센서스 열)."""
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    for attempt in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code != 200:
                return None
            info = (r.json() or {}).get("financeInfo") or {}
            titles = info.get("trTitleList") or []
            rows = info.get("rowList") or []
            if not titles or not rows:
                return None

            actual = [t for t in titles if t.get("isConsensus") != "Y"]
            est = [t for t in titles if t.get("isConsensus") == "Y"]
            fy0 = actual[-1] if actual else None
            fy1 = est[0] if est else None

            table = {r_.get("title", ""): r_.get("columns", {}) for r_ in rows}
            out = {}
            if fy0:
                out["결산기"] = fy0["title"].rstrip(".")
                for src, dst in FIN_ROWS.items():
                    out[dst] = _num((table.get(src, {}).get(fy0["key"]) or {}).get("value"))
            if fy1:
                out["컨센서스기준"] = fy1["title"].rstrip(".") + "(E)"
                for src, dst in EST_ROWS.items():
                    out[dst] = _num((table.get(src, {}).get(fy1["key"]) or {}).get("value"))
            return out or None
        except Exception:
            if attempt == 1:
                return None
            time.sleep(1.0)


def fetch_finance_all(codes: list[str], ttl_days: float = FIN_TTL_DAYS) -> pd.DataFrame:
    """전 종목 재무·컨센서스 수집 (JSON 파일 캐시, 실패는 짧게 캐시)."""
    os.makedirs(FIN_CACHE_DIR, exist_ok=True)
    now = time.time()
    results: dict[str, dict] = {}
    todo = []
    for code in codes:
        path = os.path.join(FIN_CACHE_DIR, f"{code}.json")
        if os.path.exists(path):
            age_days = (now - os.path.getmtime(path)) / 86400
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None
            failed = not data
            if age_days < (FIN_FAIL_TTL_DAYS if failed else ttl_days):
                if data:
                    results[code] = data
                continue
        todo.append(code)

    log.info("재무/컨센서스 수집: 캐시 %d / 신규조회 %d", len(results), len(todo))
    done = 0
    with ThreadPoolExecutor(max_workers=FIN_WORKERS) as pool:
        futs = {pool.submit(_fetch_finance, c): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            data = fut.result()
            with open(os.path.join(FIN_CACHE_DIR, f"{code}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(data or {}, f, ensure_ascii=False)
            if data:
                results[code] = data
            done += 1
            if done % 300 == 0:
                log.info("  재무 진행 %d/%d", done, len(todo))

    if not results:
        log.warning("재무 데이터 확보 실패 — 재무 컬럼 없이 진행")
        return pd.DataFrame({"코드": []})
    df = pd.DataFrame.from_dict(results, orient="index")
    df.index.name = "코드"
    est_cols = [c for c in EST_ROWS.values() if c in df]
    n_est = df[est_cols].notna().any(axis=1).sum() if est_cols else 0
    log.info("재무 확보 %d종목 (컨센서스 값 있는 종목 %d)", len(df), n_est)
    return df.reset_index()


# ------------------------------------------------------------------ 파생 팩터
def _safe_div(a, b, positive_only=True):
    """a/b — b 가 없거나 (positive_only 면) 0 이하이면 NaN."""
    b = b.where(b > 0) if positive_only else b.where(b != 0)
    return a / b


def _yoy(now, prev):
    return ((now - prev) / prev.abs() * 100).round(1)


def derive_factors(df: pd.DataFrame) -> pd.DataFrame:
    for col in list(FIN_ROWS.values()) + list(EST_ROWS.values()):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 컨센서스 열은 있는데 값이 전부 빈 종목(미커버)은 기준 라벨도 지운다
    no_est = df[list(EST_ROWS.values())].isna().all(axis=1)
    df.loc[no_est, "컨센서스기준"] = None
    price, cap = df["주가"], df["시가총액(억)"]
    df["PER"] = _safe_div(price, df["EPS(원)"]).round(2)
    df["PER(E)"] = _safe_div(price, df["EPS(E,원)"]).round(2)
    df["PBR"] = _safe_div(price, df["BPS(원)"]).round(2)
    df["PSR"] = _safe_div(cap, df["매출액(억)"]).round(2)
    df["POR"] = _safe_div(cap, df["영업이익(억)"]).round(2)
    df["배당수익률(%)"] = _safe_div(df["DPS(원)"], price).mul(100).round(2)
    df["배당수익률(E,%)"] = _safe_div(df["DPS(E,원)"], price).mul(100).round(2)

    df["매출YoY(E,%)"] = _yoy(df["매출액(E,억)"], df["매출액(억)"])
    df["영업이익YoY(E,%)"] = _yoy(df["영업이익(E,억)"], df["영업이익(억)"])
    df["순이익YoY(E,%)"] = _yoy(df["당기순이익(E,억)"], df["당기순이익(억)"])

    eps_growth = _yoy(df["EPS(E,원)"], df["EPS(원)"])
    df["PEG(E)"] = _safe_div(df["PER(E)"], eps_growth).round(2)
    return df


FINAL_COLS = [
    "코드", "회사명", "시장", "섹터", "업종", "주가", "1일수익률(%)",
    "시가총액(억)", "거래대금(억)",
    "1주수익률(%)", "1개월수익률(%)", "3개월수익률(%)", "6개월수익률(%)", "1년수익률(%)",
    "RS_1개월", "RS_3개월", "RS_6개월", "RS_1년",
    "52주고점대비(%)", "52주저점대비(%)", "MA200대비(%)", "변동성60일(%)",
    "결산기", "매출액(억)", "영업이익(억)", "당기순이익(억)", "지배주주순이익(억)",
    "영업이익률(%)", "순이익률(%)", "ROE(%)", "부채비율(%)",
    "EPS(원)", "BPS(원)", "DPS(원)",
    "컨센서스기준", "매출액(E,억)", "영업이익(E,억)", "당기순이익(E,억)",
    "EPS(E,원)", "ROE(E,%)", "DPS(E,원)",
    "매출YoY(E,%)", "영업이익YoY(E,%)", "순이익YoY(E,%)",
    "PER", "PER(E)", "PBR", "PSR", "POR",
    "배당수익률(%)", "배당수익률(E,%)", "PEG(E)", "테마",
]


def _write_csv(df: pd.DataFrame, path: str) -> str:
    """CSV 저장 — 파일이 엑셀 등에서 열려 있으면 _new 이름으로 대신 저장."""
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        alt = path[:-4] + "_new.csv"
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        log.warning("%s 가 열려 있어 %s 로 저장했습니다",
                    os.path.basename(path), os.path.basename(alt))
        return alt


# ------------------------------------------------------------------ 메인
def main():
    ap = argparse.ArgumentParser(description="전종목 퀀트데이터 수집")
    ap.add_argument("--no-prices", action="store_true", help="일봉 캐시 갱신 생략")
    ap.add_argument("--no-finance", action="store_true", help="재무/컨센서스 수집 생략")
    args = ap.parse_args()

    log_path = _setup_logging()
    t0 = time.time()

    uni = build_universe()
    base = pd.DataFrame({
        "코드": uni["Code"],
        "회사명": uni["Name"],
        "시장": uni["Market"],
        "업종": uni["Sector"],
        "주가": uni["Close"],
        "1일수익률(%)": pd.to_numeric(uni["ChagesRatio"], errors="coerce").round(2),
        "시가총액(억)": (uni["Marcap"] / 1e8).round(0),
        "거래대금(억)": (uni["Amount"] / 1e8).round(1),
    })

    codes = base["코드"].tolist()

    # 섹터·업종(네이버)·테마 — 네이버 업종이 있으면 표준산업분류 대신 사용
    groups = fetch_group_map()
    if not groups.empty:
        base = base.merge(groups, on="코드", how="left")
        base["업종"] = base["업종N"].fillna(base["업종"])
        base = base.drop(columns=["업종N"])
    else:
        base[["섹터", "테마"]] = None

    if args.no_prices and os.path.exists(OHLCV_PATH):
        ohlcv = pd.read_parquet(OHLCV_PATH)
        ohlcv["date"] = pd.to_datetime(ohlcv["date"])
    else:
        ohlcv = update_price_cache(codes)
    mom = momentum_factors(ohlcv)
    base = base.merge(mom, on="코드", how="left")

    if not args.no_finance:
        # 우선주(코드 끝자리 0 아님)는 재무가 보통주와 중복이라 조회 생략
        common = [c for c in codes if c.endswith("0")]
        fin = fetch_finance_all(common)
        base = base.merge(fin, on="코드", how="left")
    for col in list(FIN_ROWS.values()) + list(EST_ROWS.values()) + ["결산기", "컨센서스기준"]:
        if col not in base:
            base[col] = None

    base = derive_factors(base)
    base = base.reindex(columns=FINAL_COLS)
    base = base.sort_values("시가총액(억)", ascending=False).reset_index(drop=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    out_path = _write_csv(base, os.path.join(OUT_DIR, f"퀀트데이터_{stamp}.csv"))
    _write_csv(base, os.path.join(OUT_DIR, "퀀트데이터_latest.csv"))

    log.info("완료: %d종목 × %d컬럼 → %s (%.1f분, 로그 %s)",
             len(base), len(base.columns), out_path, (time.time() - t0) / 60,
             os.path.basename(log_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
