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
import json
import datetime
from collections import OrderedDict

import pytz
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

URL = "https://comp.wisereport.co.kr/wiseReport/summary/ReportSummary.aspx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ----------------------------------------------------------------------------
# 1. 크롤링 (노트북 cell-3 의 Selenium 로직)
# ----------------------------------------------------------------------------
def scrape_wisereport_selenium():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(URL)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )
        html = driver.page_source
    finally:
        driver.quit()

    tables = pd.read_html(io.StringIO(html))
    if len(tables) <= 2:
        raise RuntimeError("데이터 테이블을 찾을 수 없습니다.")

    df = tables[2]
    df = df.iloc[5:, [0, 2, 3, 4, 5, 6]].reset_index(drop=True)
    df.columns = ["기업명", "투자의견", "목표주가", "전일수정주가", "제목", "요약"]

    remove_keywords = ["변동없음", "Copyright", "FnGuide"]
    pattern = "|".join(remove_keywords)
    df = df[~df["기업명"].astype(str).str.contains(pattern, case=False, na=False)]
    df = df.dropna(subset=["기업명", "제목"], how="all").reset_index(drop=True)

    kst = pytz.timezone("Asia/Seoul")
    df["수집일자"] = datetime.datetime.now(kst).strftime("%Y-%m-%d")
    return df


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
    final_df = scrape_wisereport_selenium()
    if final_df is None or final_df.empty:
        raise RuntimeError("크롤링 결과가 비어있습니다.")
    print(f"수집 완료: {len(final_df)} 행")
    print_top5(final_df)
    save_to_drive(final_df)


if __name__ == "__main__":
    main()
