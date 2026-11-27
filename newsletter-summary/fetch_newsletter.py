# -*- coding: utf-8 -*-
"""네이버 메일(IMAP)에서 지정한 발신자의 뉴스레터 중 아직 요약을 보내지 않은 것을 꺼내 온다.

흐름: IMAP 접속(읽기 전용) -> 최근 N일 메일의 헤더만 조회 -> 발신자 필터 + 이미 보낸 것 제외
      -> 본문(HTML)에서 글자만 추출 -> raw/<id>.txt 저장 + 목록 출력

요약은 이 스크립트가 하지 않는다. Claude 예약 작업(newsletter-summary)이 raw/<id>.txt 를 읽고
out/<id>.md 를 쓴 뒤 send_newsletter.py 로 텔레그램에 보낸다(README.md 참고).

로그인 정보는 같은 폴더의 naver_login.json(git 제외)에서 읽는다.
  {"naver_id": "아이디", "naver_app_password": "애플리케이션 비밀번호", "senders": ["보낸사람@주소"]}

사용:  python -X utf8 newsletter-summary/fetch_newsletter.py            # 새 뉴스레터 꺼내기
       python -X utf8 newsletter-summary/fetch_newsletter.py --check    # 로그인·발신자 확인만(저장 안 함)
       python -X utf8 newsletter-summary/fetch_newsletter.py --days 14  # 훑는 기간 바꾸기(기본 7일)

종료 코드: 0 정상(새 메일이 없어도 0) / 1 실패(로그인 불가 등)
"""
from __future__ import annotations

import argparse
import email
import email.policy
import hashlib
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGIN_FILE = HERE / "naver_login.json"
SENT_FILE = HERE / "sent.json"          # 요약을 보낸 메일 기록(중복 방지)
RAW_DIR = HERE / "raw"                  # 메일 본문 텍스트(git 제외)
OUT_DIR = HERE / "out"                  # 요약 md(git 제외)

IMAP_HOST = "imap.naver.com"
IMAP_PORT = 993
DEFAULT_SENDERS = ["ai-korea-community@ghost.io"]
MAX_BODY_CHARS = 40000                  # 요약 재료로 넘길 본문 상한
KEEP_DAYS = 60                          # raw/·out/ 파일 보관 기간
KST = timezone(timedelta(hours=9))

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------- 설정·기록
def load_login() -> dict:
    cfg = {}
    if LOGIN_FILE.exists():
        cfg = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
    user = (os.getenv("NAVER_ID") or cfg.get("naver_id") or "").strip()
    password = (os.getenv("NAVER_APP_PASSWORD") or cfg.get("naver_app_password") or "").strip()
    senders = [s.strip().lower() for s in (cfg.get("senders") or DEFAULT_SENDERS) if s.strip()]
    if not user or not password:
        raise SystemExit(
            f"네이버 로그인 정보가 비어 있습니다. {LOGIN_FILE} 의 naver_id 와 "
            "naver_app_password 를 채워 주세요(README.md '처음 한 번 준비')."
        )
    # 주소 전체(aaa@naver.com)를 적어도 아이디만 쓰도록 맞춘다
    user = re.sub(r"@naver\.com$", "", user, flags=re.I)
    return {"user": user, "password": password, "senders": senders}


def load_sent() -> dict:
    if SENT_FILE.exists():
        try:
            return json.loads(SENT_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass          # 손상됐으면 빈 기록으로 시작(최악이라도 한 번 더 보낼 뿐)
    return {}


# ---------------------------------------------------------------- 메일 읽기
def imap_date(days_ago: int) -> str:
    """IMAP SEARCH용 날짜(예: 25-Nov-2026). 로캘과 무관하게 영문 월 이름을 쓴다."""
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def mail_date(msg: EmailMessage) -> datetime | None:
    try:
        d = parsedate_to_datetime(str(msg.get("Date", "")))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(KST)
    except Exception:
        return None


def mail_id(msg: EmailMessage) -> str:
    """파일 이름·중복 판별에 쓰는 id. 예: 2026-11-27_3fa9c1d2 (받은 날짜 + Message-ID 해시)."""
    mid = (msg.get("Message-ID") or "").strip()
    raw = mid or f"{msg.get('Subject', '')}|{msg.get('Date', '')}"
    d = mail_date(msg)
    day = f"{d:%Y-%m-%d}" if d else "0000-00-00"
    return f"{day}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"


def sender_of(msg: EmailMessage) -> str:
    return parseaddr(str(msg.get("From", "")))[1].lower()


def extract(msg: EmailMessage) -> tuple[str, str]:
    """(본문 텍스트, 웹에서 보기 링크). HTML 이 없으면 text/plain 을 쓴다."""
    from bs4 import BeautifulSoup

    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return "", ""
    content = part.get_content()
    link = ""
    if part.get_content_type() == "text/html":
        soup = BeautifulSoup(content, "html.parser")
        for a in soup.find_all("a", href=True):
            label = a.get_text(" ", strip=True).lower()
            if re.search(r"view (in|online)|브라우저에서|웹에서 보기", label) and a["href"].startswith("http"):
                link = a["href"]
                break
        for tag in soup(["script", "style", "head", "title"]):
            tag.decompose()
        content = soup.get_text("\n")
    lines = [re.sub(r"[ \t ­͏​‌‍ ﻿]+", " ", ln).strip() for ln in content.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text[:MAX_BODY_CHARS], link


def fetch(login: dict, days: int, skip_ids: set[str], check_only: bool = False):
    """(받은편지함에서 훑은 통수, 발신자 일치 통수, [새 메일 (id, 메시지)])"""
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(login["user"], login["password"])
        # readonly=True: 읽음 표시 등 메일함 상태를 전혀 바꾸지 않는다.
        imap.select("INBOX", readonly=True)

        typ, data = imap.search(None, "SINCE", imap_date(days))
        if typ != "OK" or not data or not data[0]:
            return 0, 0, []
        nums = data[0].split()

        # 1단계: 헤더만 받아 발신자를 직접 비교한다(서버 검색 조건에 의존하지 않음).
        typ, data = imap.fetch(b",".join(nums).decode(),
                               "(BODY.PEEK[HEADER.FIELDS (FROM MESSAGE-ID SUBJECT DATE)])")
        matched, targets = 0, []
        for item in data:
            if not isinstance(item, tuple):
                continue
            num = item[0].split()[0].decode()
            hdr = email.message_from_bytes(item[1], policy=email.policy.default)
            if sender_of(hdr) not in login["senders"]:
                continue
            matched += 1
            if mail_id(hdr) not in skip_ids:
                targets.append(num)
        if check_only:
            return len(nums), matched, [("", None)] * len(targets)

        # 2단계: 대상 메일만 전체 본문을 받는다.
        mails = []
        for num in targets:
            typ, data = imap.fetch(num, "(BODY.PEEK[])")
            raw = next(i[1] for i in data if isinstance(i, tuple))
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            mails.append((mail_id(msg), msg))
        return len(nums), matched, mails
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def prune_old() -> None:
    limit = time.time() - KEEP_DAYS * 86400
    for d in (RAW_DIR, OUT_DIR):
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and f.stat().st_mtime < limit:
                    f.unlink()


# ---------------------------------------------------------------- 실행
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="며칠 전 메일까지 훑을지(기본 7)")
    ap.add_argument("--check", action="store_true", help="로그인·발신자 확인만 하고 저장하지 않는다")
    args = ap.parse_args()

    login = load_login()
    sent = load_sent()
    try:
        scanned, matched, mails = fetch(login, args.days, set(sent), check_only=args.check)
    except imaplib.IMAP4.error as exc:
        print(f"네이버 메일 로그인/조회 실패: {exc}", file=sys.stderr)
        print("확인할 것: ① 네이버 메일 환경설정 > POP3/IMAP 설정에서 'IMAP/SMTP 사용함' "
              "② naver_login.json 의 애플리케이션 비밀번호(2단계 인증 화면에서 발급한 값) "
              "③ 아이디 오타", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"네이버 메일 서버 접속 실패(네트워크): {exc!r}", file=sys.stderr)
        return 1

    who = ", ".join(login["senders"])
    print(f"최근 {args.days}일 받은편지함 {scanned}통 중 발신자({who}) 일치 {matched}통, "
          f"아직 안 보낸 것 {len(mails)}통")
    if args.check:
        print("로그인 정상 — 확인만 하고 끝냅니다(저장 안 함).")
        return 0
    if not mails:
        print("새 뉴스레터가 없습니다.")
        return 0

    RAW_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    prune_old()
    failed = 0
    rows = []
    for mid, msg in mails:
        subject = str(msg.get("Subject", "(제목 없음)")).strip()
        try:
            body, link = extract(msg)
            if not body:
                raise ValueError("본문을 추출하지 못했습니다.")
            d = mail_date(msg)
            head = [f"id: {mid}", f"제목: {subject}",
                    f"받은 시각: {d:%Y-%m-%d %H:%M} KST" if d else "받은 시각: 알 수 없음",
                    f"보낸 사람: {sender_of(msg)}", f"원문 링크: {link or '없음'}",
                    "=" * 40]
            path = RAW_DIR / f"{mid}.txt"
            path.write_text("\n".join(head) + "\n" + body + "\n", encoding="utf-8")
            rows.append((d or datetime.min.replace(tzinfo=KST), mid, subject, len(body), path))
        except Exception as exc:      # 한 통이 실패해도 나머지는 계속
            failed += 1
            print(f"실패: {subject} -> {exc!r}", file=sys.stderr)

    print("\n새 뉴스레터(오래된 것부터):")
    for d, mid, subject, n, path in sorted(rows):
        print(f"- {mid} | {subject} | 본문 {n:,}자 | {path.relative_to(HERE.parent).as_posix()}")
    return 1 if failed and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
