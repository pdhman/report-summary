# -*- coding: utf-8 -*-
"""
OpenDART(금융감독원 전자공시) 클라이언트 — 종목탐색 펀더멘털 필터용.

종목코드로 최신 보고서의 주요 재무 지표를 가져온다:
  매출액 YoY 성장률(%) · 영업이익(흑자 여부) · 영업이익률(%) · 부채비율(%)

- API 키: config.yaml 의 dart.api_key 또는 환경변수 DART_API_KEY
  (발급: https://opendart.fss.or.kr — 무료, 일 20,000건)
- 키가 없거나 조회 실패 시 None 을 돌려주고 파이프라인은 그대로 진행된다.
- 고유번호(corp_code) 매핑과 재무 조회 결과는 .dart_cache/ 에 캐시해
  API 호출을 최소화한다(선정 종목은 하루 몇 건 수준).
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, ".dart_cache")
API = "https://opendart.fss.or.kr/api"

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서
_REPRT = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}


def _api_key():
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        return key
    try:
        import yaml
        with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return str((cfg.get("dart") or {}).get("api_key") or "").strip()
    except Exception:
        return ""


def _get(path, **params):
    params["crtfc_key"] = _api_key()
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()


# ---------------------------------------------------------------- corp_code
def _corp_map():
    """종목코드(6자리) → corp_code 매핑. 하루 1회만 갱신."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "corp_map.json")
    if os.path.exists(cache):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(cache)
        if age < 86400:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
    raw = _get("corpCode.xml")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    mapping = {}
    for el in ET.fromstring(xml).iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        if stock:
            mapping[stock] = el.findtext("corp_code").strip()
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    return mapping


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 재무 조회
def _fetch_accounts(corp_code, year, reprt):
    """주요계정(fnlttSinglAcnt): 연결(CFS) 우선, 없으면 개별(OFS)."""
    raw = _get("fnlttSinglAcnt.json", corp_code=corp_code,
               bsns_year=str(year), reprt_code=reprt)
    data = json.loads(raw)
    if data.get("status") != "000":
        return None
    rows = data.get("list") or []
    for fs in ("CFS", "OFS"):
        acc = {}
        for r in rows:
            if r.get("fs_div") != fs:
                continue
            name = r.get("account_nm", "").strip()
            acc[name] = {"now": _num(r.get("thstrm_amount")),
                         "prev": _num(r.get("frmtrm_amount"))}
        if acc.get("매출액", {}).get("now") is not None or acc.get("영업이익", {}).get("now") is not None:
            return acc
    return None


def _report_candidates():
    """현재 날짜 기준, 나와 있을 가능성이 높은 순서의 (연도, 보고서) 목록."""
    now = datetime.date.today()
    y = now.year
    cands = []
    if now.month >= 9:                      # 반기(8/14 마감) 이후
        cands.append((y, _REPRT["H1"]))
    if now.month >= 12:
        cands.append((y, _REPRT["Q3"]))
    if now.month >= 6:                      # 1분기(5/15 마감) 이후
        cands.append((y, _REPRT["Q1"]))
    cands += [(y - 1, _REPRT["FY"]), (y - 1, _REPRT["Q3"]), (y - 1, _REPRT["H1"])]
    return cands


def fundamentals(stock_code):
    """종목코드 → 펀더멘털 dict (없으면 None).

    반환: {"period": "2026.1Q", "sales_yoy": %, "op_profit": 원,
           "op_margin": %, "debt_ratio": %}
    """
    if not _api_key():
        return None
    stock_code = str(stock_code).zfill(6)

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"fund_{stock_code}.json")
    if os.path.exists(cache):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(cache)
        if age < 3 * 86400:                 # 3일 캐시
            with open(cache, encoding="utf-8") as f:
                return json.load(f) or None

    result = None
    try:
        corp = _corp_map().get(stock_code)
        if corp:
            label = {"11013": "1Q", "11012": "2Q", "11014": "3Q", "11011": "FY"}
            for year, reprt in _report_candidates():
                acc = _fetch_accounts(corp, year, reprt)
                if not acc:
                    continue
                sales = acc.get("매출액", {})
                op = acc.get("영업이익", {})
                debt = acc.get("부채총계", {}).get("now")
                equity = acc.get("자본총계", {}).get("now")
                sales_yoy = None
                if sales.get("now") and sales.get("prev"):
                    sales_yoy = (sales["now"] - sales["prev"]) / abs(sales["prev"]) * 100
                op_margin = None
                if op.get("now") is not None and sales.get("now"):
                    op_margin = op["now"] / abs(sales["now"]) * 100
                debt_ratio = None
                if debt is not None and equity:
                    debt_ratio = debt / equity * 100
                result = {"period": f"{year}.{label[reprt]}",
                          "sales_yoy": sales_yoy,
                          "op_profit": op.get("now"),
                          "op_margin": op_margin,
                          "debt_ratio": debt_ratio}
                break
    except Exception as e:
        print(f"[DART] {stock_code} 조회 실패: {e}")

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return result


def passes_filter(f):
    """기본 펀더멘털 판정: 영업이익 흑자이고 부채비율 300% 미만이면 통과.

    데이터가 없으면 None(판정 불가) — 제외하지 않고 '-' 로 표시한다.
    """
    if not f:
        return None
    op, debt = f.get("op_profit"), f.get("debt_ratio")
    if op is None:
        return None
    if op <= 0:
        return False
    if debt is not None and debt >= 300:
        return False
    return True


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    print(json.dumps(fundamentals(code), ensure_ascii=False, indent=2))
