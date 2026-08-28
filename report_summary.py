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
    added, close_cache = [], {}
    for row in hk:
        key = (row["_code"], row["_src"])
        if key in seen:
            continue
        seen.add(key)
        code = row.pop("_code"); row.pop("_src")
        if code not in close_cache:
            close_cache[code] = _prev_close(code)
            time.sleep(0.2)
        row["전일수정주가"] = close_cache[code]
        row["수집일자"] = today.strftime("%Y-%m-%d")
        added.append(row)
    print(f"한경 컨센서스 병합: 수집 {len(hk)}건 중 신규 {len(added)}건 추가")
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
