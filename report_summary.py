"""
리포트서머리 자동 크롤링 스크립트 (GitHub Actions 용)

- WiseReport 리포트 서머리를 크롤링해서
- 구글 드라이브의 '리포트서머리.xlsx'에 날짜별 시트로 누적 저장한다.

노트북(리포트서머리 크롤링.ipynb)의 Selenium 크롤링 + 분석 로직을 그대로 옮기되,
Colab 전용 코드(google.colab, apt-get 등)는 제거하고 서비스 계정으로 드라이브에 저장한다.

필요한 환경변수 (GitHub Secrets):
  - GDRIVE_SA_KEY : 구글 서비스 계정 JSON 키 전체 내용
  - GDRIVE_FILE_ID: 구글 드라이브의 '리포트서머리.xlsx' 파일 ID
"""

import io
import os
import re
import json
import time
import datetime
from collections import OrderedDict

import pytz
import pandas as pd

import requests as _rq

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# 2026-08-25 부터 네이버 금융 리서치가 수집원이다. 원래 소스였던 WiseReport
# 리포트서머리(comp.wisereport.co.kr)는 이날 서비스가 종료됐다("해당 리포트
# 서비스는 제공이 종료되었습니다"). 본사이트(wisereport.co.kr)는 로그인이
# 필요해 무인 수집에 부적합하고, 네이버 리서치는 공개 페이지에 종목·제목·
# 증권사·목표가·투자의견·본문이 모두 있어 기존 스키마를 그대로 채운다.
# 2026-08-28: finance.naver.com 이 9/10 종료 예고되어 신형 공식 API 로 전환.
# 목록·상세 모두 JSON 이라 HTML 파싱이 사라졌고, 상세의 priceAtWriteDate 가
# 전일수정주가를 대신해 종가 API 호출도 필요 없다.
LIST_API = "https://m.stock.naver.com/api/research/company?pageSize=60&page={page}"
DETAIL_API = "https://m.stock.naver.com/api/research/company/{rid}"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
MAX_PAGES = 12          # 하루 발행량(수십 건)을 넉넉히 덮는다


# ----------------------------------------------------------------------------
# 1. 크롤링 (네이버 금융 리서치: 오늘 발행된 종목 리포트 전부)
# ----------------------------------------------------------------------------
def _num_or_none(v):
    try:
        f = float(str(v).replace(",", ""))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _read_detail(rid):
    """리서치 상세 API → (목표가, 투자의견, 요약, 작성일주가). 실패 필드는 None/''."""
    r = _rq.get(DETAIL_API.format(rid=rid), headers=UA, timeout=15)
    rc = r.json().get("researchContent") or {}
    summary = re.sub(r"<[^>]+>", " ", str(rc.get("content") or ""))
    summary = re.sub(r"\s+", " ", summary).strip()[:600]
    return (_num_or_none(rc.get("goalPrice")),
            str(rc.get("opinion") or "").strip(),
            summary,
            _num_or_none(rc.get("priceAtWriteDate")))


def _prev_close(code):
    """전일수정주가 — 네이버 종목 API 종가."""
    try:
        r = _rq.get(f"https://m.stock.naver.com/api/stock/{code}/basic",
                    headers=UA, timeout=10)
        return float(r.json()["closePrice"].replace(",", ""))
    except Exception:
        return None


def scrape_naver_research():
    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst)
    today_iso = today.strftime("%Y-%m-%d")        # 목록의 writeDate 형식

    rows, stop = [], False
    for page in range(1, MAX_PAGES + 1):
        r = _rq.get(LIST_API.format(page=page), headers=UA, timeout=15)
        found = r.json()
        if not isinstance(found, list) or not found:
            break
        for it in found:
            if str(it.get("writeDate")) != today_iso:   # 목록은 최신순 — 과거면 끝
                stop = True
                break
            code = str(it.get("itemCode") or "").zfill(6)
            if not code.strip("0"):
                continue
            rows.append((code, str(it.get("itemName") or "").strip(),
                         it.get("researchId"),
                         str(it.get("title") or "").strip(),
                         str(it.get("brokerName") or "").strip()))
        if stop:
            break
        time.sleep(0.4)

    if not rows:
        # 휴장일이거나 아직 발행 전 — main() 의 '10행 미만' 가드가 처리한다
        return pd.DataFrame(columns=["기업명", "투자의견", "목표주가",
                                     "전일수정주가", "제목", "요약", "수집일자"])

    out, close_cache = [], {}
    for code, name, rid, title, broker in rows:
        target, opinion, summary, at_write = _read_detail(rid)
        prev = at_write                            # 상세의 작성일 주가를 우선 사용
        if prev is None:
            if code not in close_cache:
                close_cache[code] = _prev_close(code)
                time.sleep(0.2)
            prev = close_cache[code]
        out.append({
            "기업명": f"{name} ({code})",
            "투자의견": opinion,
            "목표주가": target,
            "전일수정주가": prev,
            "제목": title,
            "요약": f"[{broker}] {summary}" if summary else f"[{broker}]",
        })
        time.sleep(0.3)

    df = pd.DataFrame(out)
    df["수집일자"] = today.strftime("%Y-%m-%d")
    return df


# ----------------------------------------------------------------------------
# 1b. 한경 컨센서스 병합 (2026-08-25 추가)
# ----------------------------------------------------------------------------
# 네이버 리서치는 네이버에 리포트를 제공하는 증권사만 실려 하루 20~35건에
# 그친다(WiseReport 시절 평균 94건). 한경 컨센서스(공개)를 병합해 커버리지를
# 복구한다 — 목록에 종목·제목·적정가격·투자의견·제공출처·핵심 불릿이 모두 있다.
HK_URL = ("https://consensus.hankyung.com/analysis/list?skinType=business"
          "&sdate={d}&edate={d}&now_page={page}")
HK_MAX_PAGES = 8

_HK_ROW_RE = re.compile(
    r'<td class="first txt_number">(\d{4}-\d{2}-\d{2})</td>\s*'
    r'<td class="text_l">\s*<a href="/analysis/downpdf\?report_idx=\d+"[^>]*>'
    r'([^<(]+)\((\d{6})\)\s*([^<]*)</a>.*?'
    r'(?:<ul>(.*?)</ul>.*?)?'
    r'<td class="text_r txt_number">([\d,]+)</td>\s*'
    r'<td>\s*([^<]*?)\s*</td>\s*'                 # 투자의견
    r'<td>[^<]*</td>\s*'                          # 작성자
    r'<td>\s*([^<]*?)\s*</td>', re.S)


def scrape_hankyung(today_iso):
    rows = []
    for page in range(1, HK_MAX_PAGES + 1):
        r = _rq.get(HK_URL.format(d=today_iso, page=page), headers=UA, timeout=15)
        found = _HK_ROW_RE.findall(r.text)
        if not found:
            break
        for date, name, code, title, bullets, target, opinion, src in found:
            if date != today_iso:
                continue
            summary = ""
            if bullets:
                items = re.findall(r"<li>(.*?)</li>", bullets, re.S)
                summary = " ▶ ".join(re.sub(r"<[^>]+>", "", b).strip()
                                     for b in items if b.strip())[:600]
            opinion = opinion.strip()
            if opinion in ("투자의견없음", "없음", "-"):
                opinion = ""
            tnum = target.replace(",", "")
            rows.append({
                "기업명": f"{name.strip()} ({code})",
                "투자의견": opinion,
                "목표주가": float(tnum) if tnum.isdigit() and int(tnum) > 0 else None,
                "제목": title.strip(),
                "요약": (f"[{src.strip()}] {summary}" if summary
                         else f"[{src.strip()}]"),
                "_code": code,
                "_src": src.strip(),
            })
        if len(found) < 20:      # 마지막 페이지
            break
        time.sleep(0.4)
    return rows


def merge_hankyung(df, today):
    """네이버 결과(df)에 한경 컨센서스의 미포함 리포트를 더한다.

    중복 판정은 (종목코드, 증권사) — 같은 증권사의 같은 날 같은 종목 리포트는
    같은 건으로 본다. 네이버 쪽이 요약이 길어 우선한다. 한경 수집이 실패해도
    네이버 단독으로 계속한다.
    """
    try:
        hk = scrape_hankyung(today.strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"한경 컨센서스 수집 실패(네이버 단독 진행): {e}")
        return df
    if not hk:
        return df

    seen = set()
    for _, r in df.iterrows():
        m = re.search(r"\((\d{6})\)", str(r["기업명"]))
        b = re.match(r"\[([^\]]+)\]", str(r["요약"]))
        if m:
            seen.add((m.group(1), b.group(1) if b else ""))
    added, close_cache, bad = [], {}, 0
    for row in hk:
        key = (row["_code"], row["_src"])
        if key in seen:
            continue
        # 한경 원본 매핑 오류 방어(2026-09-18 실사고): 발간 증권사를 대상 종목으로 넣은 행이
        # 있다. 예) '한화투자증권(003530) 다시 늘어나는 미수금 / 목표 50,000 / 한화투자증권'
        # — 목표가는 실제 대상 종목 값이라 상승여력이 +999% 로 튄다.
        if row["기업명"].split(" (")[0].replace(" ", "") == row["_src"].replace(" ", ""):
            bad += 1
            continue
        seen.add(key)
        code = row.pop("_code"); row.pop("_src")
        if code not in close_cache:
            close_cache[code] = _prev_close(code)
            time.sleep(0.2)
        row["전일수정주가"] = close_cache[code]
        row["수집일자"] = today.strftime("%Y-%m-%d")
        added.append(row)
    print(f"한경 컨센서스 병합: 수집 {len(hk)}건 중 신규 {len(added)}건 추가"
          + (f" (발간사=종목 매핑 오류 {bad}건 제외)" if bad else ""))
    if not added:
        return df
    return pd.concat([df, pd.DataFrame(added)], ignore_index=True)[df.columns]


# ----------------------------------------------------------------------------
# 1c. 뉴스 보충 (2026-11-23 추가)
# ----------------------------------------------------------------------------
# 한국투자·NH·KB·삼성 등 대형사는 네이버 리서치·한경 컨센서스에 리포트를 거의 올리지 않는다
# (10월 이후 한투 1건, NH·KB 0건). 하나증권 소형주 노트도 빠진다(11/18 디케이티).
# 그 대신 언론이 '목표가' 기사로 거의 다 보도한다 — 구글 뉴스 RSS 로 헤드라인을 모아
# (종목, 증권사, 목표가, 의견)을 뽑고, 기존 목록·최근 시트에 없는 것만 '뉴스 보충'으로 더한다.
# 정밀도 우선: 종목 1개·증권사 1개로 확정되는 헤드라인만 쓴다(뉴스핌 [리포트 브리핑]은 정형 파싱).
NEWS_RSS = "https://news.google.com/rss/search"
NEWS_QUERIES = ["리포트 브리핑", "모닝 리포트", "목표가", "목표주가", "목표가 상향", "목표가 하향",
                "한국투자증권 목표가", "NH투자증권 목표가", "KB증권 목표가", "삼성증권 목표가",
                "하나증권 목표가", "한투증권 목표가"]
BROKERS = {   # 정식 이름(기존 요약의 [증권사] 표기와 같게) → 헤드라인 표기
    "한국투자증권": ["한국투자증권", "한국투자證", "한투증권", "한투證", "한국투자"],
    "NH투자증권": ["NH투자증권", "NH證", "NH투자"],
    "KB증권": ["KB증권", "KB證"],
    "삼성증권": ["삼성증권", "삼성證"],
    "하나증권": ["하나증권", "하나證"],
    "신한투자증권": ["신한투자증권", "신한證", "신한투자"],
    "키움증권": ["키움증권", "키움證"],
    "대신증권": ["대신증권", "대신證"],
    "미래에셋증권": ["미래에셋증권", "미래에셋證", "미래에셋"],
    "한화투자증권": ["한화투자증권", "한화증권", "한화투자證"],
    "iM증권": ["iM증권", "iM證"],
    "IBK투자증권": ["IBK투자증권", "IBK證", "IBK투자"],
    "SK증권": ["SK증권", "SK證"],
    "DB증권": ["DB증권", "DB금융투자", "DB證"],
    "유진투자증권": ["유진투자증권", "유진證", "유진투자"],
    "유안타증권": ["유안타증권", "유안타證", "유안타"],
    "메리츠증권": ["메리츠증권", "메리츠證"],
    "교보증권": ["교보증권", "교보證"],
    "LS증권": ["LS증권", "LS證"],
    "다올투자증권": ["다올투자증권", "다올證", "다올투자"],
    "BNK투자증권": ["BNK투자증권", "BNK證"],
    "흥국증권": ["흥국증권", "흥국證"],
    "현대차증권": ["현대차증권", "현대차證"],
    "DS투자증권": ["DS투자증권", "DS證"],
    "상상인증권": ["상상인증권", "상상인證"],
    "신영증권": ["신영증권", "신영證"],
    "부국증권": ["부국증권", "부국證"],
    "케이프투자증권": ["케이프투자증권", "케이프證"],
    "한양증권": ["한양증권", "한양證"],
    "골드만삭스": ["골드만삭스"], "모건스탠리": ["모건스탠리"], "JP모건": ["JP모건"],
    "씨티그룹": ["씨티그룹", "씨티"], "맥쿼리": ["맥쿼리"], "UBS": ["UBS"], "CLSA": ["CLSA"],
    "노무라": ["노무라"], "HSBC": ["HSBC"], "BofA": ["뱅크오브아메리카", "BofA"], "제프리스": ["제프리스"],
}
_SHORT_SUFFIX = {"NH": "NH투자증권", "KB": "KB증권", "삼성": "삼성증권", "하나": "하나증권", "신한": "신한투자증권",
                 "키움": "키움증권", "대신": "대신증권", "미래에셋": "미래에셋증권", "한화": "한화투자증권",
                 "한투": "한국투자증권", "한국투자": "한국투자증권", "IBK": "IBK투자증권", "SK": "SK증권",
                 "유진": "유진투자증권", "유안타": "유안타증권", "메리츠": "메리츠증권", "교보": "교보증권",
                 "iM": "iM증권", "DB": "DB증권", "다올": "다올투자증권", "LS": "LS증권", "현대차": "현대차증권"}
_JOSA = set("은는이가을를의에도와과로,·…\"'”’)]} ")
# 뉴스핌이 업종(산업) 리포트를 종목별로 자동 분할한 글은 제목이 일반어이고 목표가가 엉뚱한 경우가 있다
# (11/23 '2027년 전망' OCI홀딩스 목표가 550,000 vs 주가 147,200). 종목 리포트가 아니므로 뺀다.
_GENERIC_TITLES = re.compile(r"^(\d{4}년\s*전망|실적 추이 및 전망|NDR 후기|\d{4}년 하반기 전망|산업 전망)$")
TP_RATIO_RANGE = (0.4, 2.5)   # 목표가/현재가가 이 범위 밖이면 오탐(단위·종목 혼동)으로 보고 뺀다
_NEWSPIM_RE = re.compile(r"\[리포트 브리핑\]\s*(.+?),\s*'(.+?)'\s*(목표가\s*([\d,]+)원|Not Rated)\s*-\s*(\S+?)\s*-")


def _krx_names():
    """docs/data/krx.js 의 [코드, 종목명, 시장] → {종목명: 코드}. 두 글자 미만 이름은 오탐이 많아 뺀다."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data", "krx.js")
    txt = open(path, encoding="utf-8").read()
    return {n: c for c, n, _m in re.findall(r'\["(\d{6})","([^"]+)","(K[SQ])"\]', txt) if len(n) >= 2}


def _parse_krw(s):
    """'4만원'·'3만2천원'·'2만5000원'·'25,000원'·'105만원' → 정수(원). 못 읽으면 None."""
    m = re.match(r"\s*([\d,]+(?:\.\d+)?)\s*만\s*(?:([\d,]+)\s*(천)?)?\s*원", s)
    if m:
        v = float(m.group(1).replace(",", "")) * 10000
        if m.group(2):
            v += float(m.group(2).replace(",", "")) * (1000 if m.group(3) else 1)
        return int(v)
    m = re.match(r"\s*([\d,]{3,})\s*원", s)
    return int(m.group(1).replace(",", "")) if m else None


def _target_in(title):
    m = re.search(r"목표(?:주)?가[^0-9]{0,12}([\d,.]+\s*만?\s*(?:[\d,]+\s*천?)?\s*원)", title)
    return _parse_krw(m.group(1)) if m else None


def _stocks_in(title, names):
    """헤드라인 안의 종목명(앞뒤가 글자 경계인 것). 다른 매치에 포함된 짧은 이름은 버린다."""
    found = []
    for n in names:
        i = title.find(n)
        while i >= 0:
            before = title[i - 1] if i > 0 else " "
            after = title[i + len(n)] if i + len(n) < len(title) else " "
            ok_b = not (before.isalnum())
            ok_a = (not after.isalnum()) or after in _JOSA
            if ok_b and ok_a:
                found.append((i, i + len(n), n))
                break
            i = title.find(n, i + 1)
    found = [f for f in found if not any(g != f and g[0] <= f[0] and f[1] <= g[1] for g in found)]
    return found


def _brokers_in(title, skip_spans):
    def masked(t):
        for a, b, _ in skip_spans:
            t = t[:a] + " " * (b - a) + t[b:]
        return t
    t = masked(title)
    hit = set()
    for canon, aliases in BROKERS.items():
        if any(al in t for al in aliases):
            hit.add(canon)
    m = re.search(r"[\"”’']\s*-\s*([A-Za-z가-힣]+)\s*(?:-|$)", t)      # 머니투데이식 "…목표가↓"-NH
    if m and m.group(1) in _SHORT_SUFFIX:
        hit.add(_SHORT_SUFFIX[m.group(1)])
    m = re.search(r"\[(NH투자증권|KB증권|삼성증권|하나증권|한국투자증권|[가-힣A-Za-z]+증권)\]", t)
    if m and m.group(1) in BROKERS:
        hit.add(m.group(1))
    return hit


def _prev_weekday_1600(now):
    d = now - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.replace(hour=16, minute=0, second=0, microsecond=0)


def scrape_news(now):
    """구글 뉴스 RSS 헤드라인 → [{code,name,broker,target,opinion,title,press,pub}] (직전 평일 16:00 이후)."""
    import email.utils
    import xml.etree.ElementTree as ET
    names = _krx_names()
    since = _prev_weekday_1600(now)
    seen_titles, out = set(), []
    for q in NEWS_QUERIES:
        items = None
        for attempt in range(3):     # 연속 호출 시 가끔 XML 이 아닌 응답(일시 제한) — 쉬었다 재시도
            try:
                r = _rq.get(NEWS_RSS, params={"q": f"{q} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                            headers=UA, timeout=20)
                items = ET.fromstring(r.content).findall(".//item")
                break
            except Exception as e:
                err = e
                time.sleep(3 * (attempt + 1))
        if items is None:
            print(f"뉴스 보충: '{q}' 조회 실패 {err}")
            continue
        for it in items:
            full = (it.findtext("title") or "").strip()
            if full in seen_titles:
                continue
            seen_titles.add(full)
            try:
                pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(now.tzinfo)
            except Exception:
                continue
            if pub < since or pub > now:
                continue
            press = (it.findtext("source") or "").strip()
            title = full[: -len(press) - 3].strip() if press and full.endswith(" - " + press) else full
            m = _NEWSPIM_RE.search(full)
            if m:                                  # 정형: [리포트 브리핑]종목, '제목' 목표가 N원 - 증권사 - 뉴스핌
                name = m.group(1).strip()
                broker = next((c for c, al in BROKERS.items() if m.group(5) in al or m.group(5) == c), m.group(5))
                if name in names and not _GENERIC_TITLES.match(m.group(2).strip()):
                    out.append({"code": names[name], "name": name, "broker": broker,
                                "target": int(m.group(4).replace(",", "")) if m.group(4) else None,
                                "opinion": "Not Rated" if m.group(3) == "Not Rated" else "",
                                "title": m.group(2).strip(), "press": press or "뉴스핌", "pub": pub})
                continue
            if "목표" not in title and "투자의견" not in title:
                continue
            stocks = _stocks_in(title, names)
            stocks = [s for s in stocks if s[2] not in BROKERS or len(stocks) == 1]
            brokers = _brokers_in(title, stocks)
            # 종목이 증권사 자체인 경우(예: 'NH투자증권 목표가…-iM')는 종목명 자리를 가리고 남은 증권사를 쓴다
            if len(stocks) != 1 or len(brokers) != 1:
                continue
            name = stocks[0][2]
            broker = next(iter(brokers))
            if broker == name:
                continue
            opinion = "매수" if re.search(r"매수|Buy|BUY", title) else ("중립" if "중립" in title else "")
            out.append({"code": names[name], "name": name, "broker": broker, "target": _target_in(title),
                        "opinion": opinion, "title": title, "press": press, "pub": pub})
        time.sleep(0.5)
    return out


def merge_news(df, today, existing):
    """네이버+한경 결과(df)에 뉴스 보충 리포트를 더한다. 오늘 목록·최근 5개 시트에 같은 (종목, 증권사)가 있으면 뺀다."""
    try:
        news = scrape_news(today)
    except Exception as e:
        print(f"뉴스 보충 실패(건너뜀): {e}")
        return df

    def keys_of(d):
        ks = set()
        for _, r in d.iterrows():
            m = re.search(r"\((\d{6})\)", str(r.get("기업명", "")))
            b = re.match(r"\[([^\]]+)\]", str(r.get("요약", "")))
            if m:
                ks.add((m.group(1), b.group(1) if b else ""))
        return ks
    def norm(t):
        return re.sub(r"\s+", "", str(t))
    seen = keys_of(df)
    for name in list(existing)[:5]:
        if name != f"dt_{today:%Y%m%d}":
            seen |= keys_of(existing[name])
    # 옛 리포트 재게재 방지: 과거 전 시트의 (종목코드, 제목) — 뉴스핌 제목은 리포트 제목 그대로다
    old_titles = set()
    for name, d in existing.items():
        if name == f"dt_{today:%Y%m%d}" or "제목" not in d:
            continue
        for _, r in d.iterrows():
            m = re.search(r"\((\d{6})\)", str(r.get("기업명", "")))
            if m:
                old_titles.add((m.group(1), norm(r.get("제목"))))
    # 같은 리포트를 다룬 기사 여러 개 → 뉴스핌(리포트 제목) > 목표가 있는 기사 > 먼저 나온 기사 순으로 하나만
    news = sorted(news, key=lambda x: (x["press"] != "뉴스핌", x["target"] is None, x["pub"]))
    added, close_cache = [], {}
    for n in news:
        key = (n["code"], n["broker"])
        if key in seen or (n["code"], norm(n["title"])) in old_titles:
            continue
        if n["code"] not in close_cache:
            close_cache[n["code"]] = _prev_close(n["code"])
            time.sleep(0.2)
        px = close_cache[n["code"]]
        if n["target"] and px and not (TP_RATIO_RANGE[0] <= n["target"] / px <= TP_RATIO_RANGE[1]):
            print(f"뉴스 보충 제외(목표가/주가 {n['target'] / px:.2f}배): {n['name']} {n['broker']} {n['title'][:30]}")
            continue
        seen.add(key)
        added.append({
            "기업명": f"{n['name']} ({n['code']})",
            "투자의견": n["opinion"],
            "목표주가": float(n["target"]) if n["target"] else None,
            "전일수정주가": close_cache[n["code"]],
            "제목": n["title"],
            "요약": f"[{n['broker']}] (뉴스 보충 · {n['press']} {n['pub']:%m/%d %H:%M}) {n['title']}",
            "수집일자": today.strftime("%Y-%m-%d"),
        })
    print(f"뉴스 보충: 헤드라인 {len(news)}건 해석 → 신규 {len(added)}건 추가")
    if not added:
        return df
    return pd.concat([df, pd.DataFrame(added)], ignore_index=True)[df.columns]


# ----------------------------------------------------------------------------
# 2. 상승여력 Top 5 (노트북 cell-4) - 로그 출력용
# ----------------------------------------------------------------------------
def print_top5(final_df):
    a = final_df.copy()
    a["목표주가_num"] = pd.to_numeric(a["목표주가"].astype(str).str.replace(",", ""), errors="coerce")
    a["전일수정주가_num"] = pd.to_numeric(a["전일수정주가"].astype(str).str.replace(",", ""), errors="coerce")
    a = a.dropna(subset=["목표주가_num", "전일수정주가_num"])
    a["상승여력(%)"] = ((a["목표주가_num"] - a["전일수정주가_num"]) / a["전일수정주가_num"]) * 100
    top5 = a.sort_values("상승여력(%)", ascending=False).head(5)
    print("🚀 목표주가 상승여력(%) Top 5 종목:")
    for _, r in top5.iterrows():
        print(f"  - {r['기업명']}: {round(r['상승여력(%)'], 2)}%  ({r['제목']})")


# ----------------------------------------------------------------------------
# 3. 구글 드라이브 저장 (노트북 cell-6 을 서비스 계정 방식으로 변환)
# ----------------------------------------------------------------------------
def get_drive_service():
    sa_info = json.loads(os.environ["GDRIVE_SA_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def download_existing_sheets(service, file_id):
    """기존 xlsx를 내려받아 {시트명: DataFrame} 으로 반환. 없으면 빈 dict."""
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        return pd.read_excel(fh, sheet_name=None)
    except Exception as e:
        print(f"기존 파일을 읽지 못했습니다(신규로 처리): {e}")
        return {}


def save_to_drive(final_df):
    service = get_drive_service()
    file_id = os.environ["GDRIVE_FILE_ID"]

    date_val = final_df["수집일자"].iloc[0].replace("-", "")
    sheet_name = f"dt_{date_val}"

    existing = download_existing_sheets(service, file_id)

    # 휴장일 가드 2: 직전 시트와 내용이 같으면 사이트가 갱신되지 않은 것(주말·공휴일)
    if existing:
        latest_name = next(iter(existing))
        prev = existing[latest_name].drop(columns=["수집일자"], errors="ignore").reset_index(drop=True)
        cur = final_df.drop(columns=["수집일자"], errors="ignore").reset_index(drop=True)
        if prev.astype(str).equals(cur.astype(str)):
            print(f"⏭️ {latest_name} 시트와 내용 동일 — 새 리포트 없음(휴장일), 저장 건너뜀")
            return

    # 뉴스 보충은 휴장일 가드(1·2)를 통과한 뒤에 — 뉴스는 휴일에도 나와 가드를 무력화할 수 있다
    kst = pytz.timezone("Asia/Seoul")
    final_df = merge_news(final_df, datetime.datetime.now(kst), existing)

    # 새 시트를 맨 앞에, 같은 이름 시트는 덮어쓰기 (노트북과 동일한 규칙)
    ordered = OrderedDict()
    ordered[sheet_name] = final_df
    for old_name, old_df in existing.items():
        if old_name != sheet_name:
            ordered[old_name] = old_df

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for s_name, s_df in ordered.items():
            s_df.to_excel(writer, sheet_name=s_name, index=False)
    out.seek(0)

    media = MediaIoBaseUpload(out, mimetype=XLSX_MIME, resumable=True)
    service.files().update(fileId=file_id, media_body=media).execute()
    print(f"✅ 구글 드라이브 업데이트 완료: {sheet_name} (총 {len(ordered)}개 시트)")


# ----------------------------------------------------------------------------
def main():
    # 주말(토·일 KST)은 휴장일이므로 크롤링·저장을 모두 건너뛴다
    kst = pytz.timezone("Asia/Seoul")
    today = datetime.datetime.now(kst)
    if today.weekday() >= 5:
        print(f"⏭️ {today:%Y-%m-%d}({'토일'[today.weekday() - 5]}) — 주말 휴장일, 실행 건너뜀")
        return

    final_df = scrape_naver_research()
    final_df = merge_hankyung(final_df, today)
    if final_df is None or final_df.empty:
        raise RuntimeError("크롤링 결과가 비어있습니다.")
    print(f"수집 완료: {len(final_df)} 행")

    # 휴장일 가드 1: 정상 거래일은 리포트가 수십 건 — 10행 미만이면 공휴일로 판단
    if len(final_df) < 10:
        print(f"⏭️ 수집 {len(final_df)}행뿐 — 휴장일(공휴일)로 판단, 저장 건너뜀")
        return

    print_top5(final_df)
    save_to_drive(final_df)


if __name__ == "__main__":
    main()
