# -*- coding: utf-8 -*-
"""네이버 증권 시세 조회 (표준 라이브러리만 사용).

주 엔드포인트: polling.finance.naver.com — 여러 종목을 한 요청으로 배치 조회.
  · 응답 본문은 EUC-KR.
  · cv(전일대비)·cr(등락률)은 부호 없는 절대값이므로 절대 쓰지 않는다.
    등락률은 (nv - sv) / sv 로 직접 계산한다. (nv=현재가, sv=전일종가)
폴백: m.stock.naver.com/api/stock/{code}/basic — 종목별 개별 조회, UTF-8,
  가격이 "103,460" 같은 콤마 문자열로 온다.

자가 테스트:  python quote.py 069500 483650
"""
import json
import sys
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
POLLING_URL = "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{codes}"
BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
TIMEOUT = 15
RETRIES = 3


def _get(url, encoding):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode(encoding, errors="replace"))


def _fetch_polling(codes):
    """폴링 API 배치 조회. {code: quote} 반환, 실패 시 예외."""
    url = POLLING_URL.format(codes=",".join(codes))
    data = _get(url, "euc-kr")
    out = {}
    for area in data.get("result", {}).get("areas", []):
        for item in area.get("datas", []):
            code = str(item.get("cd", ""))
            nv, sv = item.get("nv"), item.get("sv")
            if not code or not nv or not sv:
                continue
            out[code] = {
                "price": int(nv),
                "prev_close": int(sv),
                "change_pct": round((nv - sv) / sv * 100, 2),
                "market_status": item.get("ms", ""),
            }
    return out


def _fetch_basic(code):
    """폴백: 종목별 basic API. quote dict 반환, 실패 시 예외."""
    d = _get(BASIC_URL.format(code=code), "utf-8")
    price = int(str(d["closePrice"]).replace(",", ""))
    diff = int(str(d["compareToPreviousClosePrice"]).replace(",", "").lstrip("+-"))
    name = (d.get("compareToPreviousPrice") or {}).get("name", "")
    if name == "FALLING":
        diff = -diff
    elif name not in ("RISING", "FALLING"):
        # UPPER_LIMIT/LOWER_LIMIT 등은 code로 판별 (1·2=상승, 4·5=하락)
        c = str((d.get("compareToPreviousPrice") or {}).get("code", ""))
        if c in ("4", "5"):
            diff = -diff
    prev = price - diff
    if prev <= 0:
        raise ValueError(f"{code}: 전일종가 계산 실패 (price={price}, diff={diff})")
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": round((price - prev) / prev * 100, 2),
        "market_status": d.get("marketStatus", ""),
    }


def fetch_quotes(codes, log=print):
    """종목코드 목록 → {code: {price, prev_close, change_pct, market_status}}.

    폴링 API를 3회 재시도하고, 그래도 실패하면 종목별 basic API로 폴백.
    개별 종목 실패는 건너뛰고 나머지를 반환한다.
    """
    codes = list(dict.fromkeys(codes))  # 중복 제거, 순서 유지
    if not codes:
        return {}

    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            quotes = _fetch_polling(codes)
            missing = [c for c in codes if c not in quotes]
            for c in missing:
                log(f"WARN: 폴링 응답에 {c} 없음 — basic API로 재시도")
                try:
                    quotes[c] = _fetch_basic(c)
                except Exception as e:  # noqa: BLE001
                    log(f"WARN: {c} basic 조회도 실패: {e}")
            return quotes
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"WARN: 폴링 API 실패 ({attempt}/{RETRIES}): {e}")
            time.sleep(2)

    log(f"WARN: 폴링 API 포기 ({last_err}) — 종목별 basic API 폴백")
    quotes = {}
    for c in codes:
        try:
            quotes[c] = _fetch_basic(c)
        except Exception as e:  # noqa: BLE001
            log(f"WARN: {c} basic 조회 실패: {e}")
    return quotes


if __name__ == "__main__":
    args = sys.argv[1:] or ["069500", "483650"]
    for cd, q in fetch_quotes(args).items():
        print(f"{cd}: 현재 {q['price']:,} / 전일 {q['prev_close']:,} "
              f"/ {q['change_pct']:+.2f}% / 장상태 {q['market_status']}")
