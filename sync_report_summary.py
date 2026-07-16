# -*- coding: utf-8 -*-
"""
구글 드라이브의 리포트서머리.xlsx 를 로컬로 내려받아 최신화한 뒤,
인사이트 HTML(make_insights) 과 아카이브 목록(index) 을 다시 생성한다.

매일 오전 10시(KST) Windows 작업 스케줄러가 run_insights.ps1 을 통해 호출한다.

인증: 서비스 계정 키(gdrive_sa.json) — 대상 시트가 이 계정과 '보기' 권한으로 공유돼 있어야 한다.
파일: 드라이브에 실제 .xlsx 로 저장돼 있으므로 get_media 로 그대로 다운로드한다.
"""
import io
import os
import sys
import datetime
import traceback

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

BASE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(BASE, "gdrive_sa.json")
OUT_XLSX = os.path.join(BASE, "리포트서머리.xlsx")
LOG_DIR = os.path.join(BASE, "logs")

# 대상 파일 ID: 환경변수(GitHub Actions 시크릿) 우선, 없으면 아래 기본값(로컬)
DEFAULT_FILE_ID = "1YN1G1k1eAQdGxpk3N0dWYLbHkCFBypcv"


class _Tee:
    """stdout/stderr 를 로그 파일과 콘솔에 동시에 기록(콘솔 없으면 파일만)."""
    def __init__(self, logfile, console):
        self._log = logfile
        self._console = console

    def write(self, data):
        self._log.write(data)
        try:
            if self._console:
                self._console.write(data)
        except Exception:
            pass

    def flush(self):
        try:
            self._log.flush()
        except Exception:
            pass
        try:
            if self._console:
                self._console.flush()
        except Exception:
            pass

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _credentials():
    """서비스 계정 자격증명 로드.
    우선순위: 환경변수 GDRIVE_SA_KEY(JSON 본문, GitHub Actions) → 로컬 gdrive_sa.json 파일.
    """
    import json
    raw = os.environ.get("GDRIVE_SA_KEY")
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if os.path.exists(KEY):
        return service_account.Credentials.from_service_account_file(KEY, scopes=SCOPES)
    raise FileNotFoundError(
        f"서비스 계정 키를 찾을 수 없습니다 (환경변수 GDRIVE_SA_KEY 또는 {KEY})")


def download_from_drive():
    file_id = os.environ.get("GDRIVE_FILE_ID") or DEFAULT_FILE_ID
    creds = _credentials()
    svc = build("drive", "v3", credentials=creds)

    meta = svc.files().get(fileId=file_id, fields="name,modifiedTime",
                           supportsAllDrives=True).execute()
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id,
                                                        supportsAllDrives=True))
    done = False
    while not done:
        _, done = dl.next_chunk()

    # 원자적 저장(임시 파일 → 교체)로 도중 실패 시 기존 파일 보존
    tmp = OUT_XLSX + ".tmp"
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, OUT_XLSX)
    print(f"[동기화] '{meta.get('name')}' 다운로드 완료 "
          f"(드라이브 갱신: {meta.get('modifiedTime')}, {len(buf.getvalue()):,} bytes)")


def run():
    print(f"===== 동기화 시작 {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====")
    download_from_drive()

    # 프로젝트 폴더 기준 상대경로를 쓰는 모듈들이므로 CWD 를 맞춘다.
    os.chdir(BASE)
    import make_insights
    make_insights.build()

    # 시황 브리핑(briefs/*.md → reports/brief_*.html) 생성
    try:
        import make_brief
        make_brief.build()
    except Exception as e:
        print(f"[경고] 시황 브리핑 생성 건너뜀: {e}")

    # 아카이브 목록(index.html)의 배너/목록 갱신
    try:
        import make_report
        make_report.build_index()
    except Exception as e:
        print(f"[경고] index 갱신 건너뜀: {e}")

    print("===== 동기화 완료 =====")


def main():
    """자체 UTF-8 로그 파일을 열고 run() 을 감싼다. 실패 시 종료코드 1."""
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"insights_{stamp}.log")
    logf = open(log_path, "w", encoding="utf-8")
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(logf, orig_out)
    sys.stderr = _Tee(logf, orig_err)
    code = 0
    try:
        run()
    except Exception:
        print("[오류] 동기화 실패:")
        traceback.print_exc()
        code = 1
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err
        logf.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
