# -*- coding: utf-8 -*-
"""
증권사 공개 텔레그램 채널 4개의 '오늘 글'을 모아 마감시황 요약의 재료로 만든다.

공개 채널은 웹 미리보기(t.me/s/<채널>)를 로그인 없이 HTML 로 준다. 한 페이지에 최근 글
20개쯤이 오고, ?before=<글번호> 로 그 이전 20개를 더 받을 수 있어 하루치(30~40개)를
전부 모을 수 있다.

  python market-close/fetch_channels.py                 # 가장 최근 마감일(KST) 글 수집 + 요약용 다이제스트 출력
  python market-close/fetch_channels.py --date 2026-10-21
  python market-close/fetch_channels.py --wait 9        # 신한 '마감 시황'이 아직 없으면 최대 9분 기다렸다 재확인
  python market-close/fetch_channels.py --full          # 모든 글 전문 출력(기본은 핵심 글만 전문, 나머지는 앞 500자)

기본 날짜: 지금이 15:40(KST) 이후면 오늘, 그 전이면 직전 평일(마감 글이 아직 없으므로).

저장: market-close/raw/YYYY-MM-DD.json (git 제외 — 원문은 각 증권사 저작물이라 공개 저장소에
올리지 않는다. 요약은 내 말로 다시 쓰고 개인 대화방으로만 보낸다.)

종료 코드: 0 정상 / 2 신한 '마감 시황' 글이 아직 없음(요약은 가능하나 기다릴 가치가 있음) /
          1 모든 채널 수집 실패.
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "raw"

# 채널 → (표시 이름, 역할 메모). 역할은 요약자가 어느 글을 어떻게 쓸지 참고용.
CHANNELS = {
    "shinhanresearch": ("신한 리서치", "국내 주식 마감 시황(16:30~16:45)·마켓레이더(11시)·Daily Market Digest(아침)·종목/산업 리포트"),
    "siglab":          ("시그널랩", "마감 리뷰(16:10 전후, 지수·수급·업종)·실적속보·업종지수 흐름·뉴스 클리핑"),
    "Meritz_strategy": ("메리츠 투자전략", "투자전략팀 노트(장중·마감 후 시장 해석)"),
    "skitteam":        ("SK증권 IT", "반도체·IT 뉴스, 메모리 P/E 업데이트, 해외 AI 기업 실적 코멘트"),
}
# '핵심 글' 판정 — 제목(앞 80자)에 이 말이 들어가면 전문을 다이제스트에 싣는다.
KEY_TITLES = {
    "shinhanresearch": ("마감 시황", "마켓레이더", "Daily Market Digest"),
    "siglab":          ("마감 리뷰", "업종지수 흐름", "빈집"),
    "Meritz_strategy": ("",),            # 메리츠는 글이 적어 전부 핵심으로 본다
    "skitteam":        ("P/E 업데이트", "주요 뉴스", "수출액"),
}
CLOSE_KEY = ("shinhanresearch", "마감 시황")   # 이 글이 있어야 '마감시황 요약'이 완성된다
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _text(fragment):
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_page(page, handle):
    posts = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        m_link = re.search(r'data-post="' + re.escape(handle) + r'/(\d+)"', block)
        m_time = re.search(r'<time datetime="([^"]+)"', block)
        if not (m_link and m_time):
            continue                     # 서비스 메시지 등
        # 답글 인용(js-message_reply_text)이 아닌 본문(js-message_text)만 고른다
        m_txt = re.search(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', block, re.S)
        when = dt.datetime.fromisoformat(m_time.group(1).replace("Z", "+00:00")).astimezone(KST)
        posts.append({
            "id": int(m_link.group(1)),
            "when": when.isoformat(timespec="minutes"),
            "text": _text(m_txt.group(1)) if m_txt else "",
            "media": bool(re.search(r"tgme_widget_message_(photo|video|document)", block)),
            "link": f"https://t.me/{handle}/{m_link.group(1)}",
        })
    posts.sort(key=lambda p: p["id"])
    return posts


def fetch(url, xhr=False):
    """xhr=True 는 과거 글 페이지(?before=). 일반 GET 은 ?before 를 무시하고 최신 페이지를 다시 주고,
    텔레그램 위젯이 쓰는 XMLHttpRequest 헤더를 붙이면 HTML 조각을 JSON 문자열로 준다(2026-10-21 실측)."""
    headers = dict(UA, **({"X-Requested-With": "XMLHttpRequest"} if xhr else {}))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if xhr else body


def fetch_day(handle, day, max_pages=6):
    """해당 날짜(KST)의 글 전부. 첫 페이지(최근 20개)에서 시작해 날짜가 더 이른 글이 보일 때까지 과거로 넘긴다."""
    collected, before = {}, None
    for _ in range(max_pages):
        url = f"https://t.me/s/{handle}" + (f"?before={before}" if before else "")
        posts = parse_page(fetch(url, xhr=before is not None), handle)
        if not posts:
            break
        for p in posts:
            collected[p["id"]] = p
        oldest = min(posts, key=lambda p: p["id"])
        if dt.datetime.fromisoformat(oldest["when"]).date() < day:
            break
        before = oldest["id"]
        time.sleep(0.6)
    return [p for p in sorted(collected.values(), key=lambda p: p["id"])
            if dt.datetime.fromisoformat(p["when"]).date() == day]


def is_key(handle, text):
    head = text[:80]
    return any(k in head for k in KEY_TITLES[handle])


def default_day(now):
    d = now.date()
    if now.time() < dt.time(15, 40):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def has_close_post(data):
    handle, key = CLOSE_KEY
    return any(key in p["text"][:80] for p in data.get(handle, {}).get("posts", []))


def collect(day):
    data, failed = {}, []
    for handle, (name, role) in CHANNELS.items():
        try:
            posts = fetch_day(handle, day)
        except Exception as e:            # 한 채널이 막혀도 나머지는 살린다
            failed.append(f"{name}: {e}")
            posts = []
        data[handle] = {"name": name, "role": role, "posts": posts}
    return data, failed


def digest(day, data, failed, full=False, cut=500):
    out = [f"# 증권사 텔레그램 {day:%Y-%m-%d (%a)} 수집 — "
           + ", ".join(f"{v['name']} {len(v['posts'])}건" for v in data.values())]
    if failed:
        out.append("⚠️ 수집 실패: " + " / ".join(failed))
    if not has_close_post(data):
        out.append("⚠️ 신한 '국내 주식 마감 시황' 글이 아직 없음 — 지수·수급은 시그널랩 마감 리뷰로 대신한다")
    for handle, v in data.items():
        out += ["", f"## [{v['name']}] {len(v['posts'])}건 · {v['role']}"]
        for p in v["posts"]:
            key = is_key(handle, p["text"])
            t = dt.datetime.fromisoformat(p["when"]).strftime("%H:%M")
            body = p["text"] or ("(사진/파일만)" if p["media"] else "(본문 없음)")
            if not (full or key) and len(body) > cut:
                body = body[:cut].rstrip() + f" …(+{len(p['text']) - cut}자)"
            tag = "★" if key else "·"
            out += [f"{tag} {t} {p['link']}", body, ""]
    return "\n".join(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (KST). 기본: 가장 최근 마감일")
    ap.add_argument("--wait", type=int, default=0, metavar="MIN",
                    help="신한 '마감 시황'이 없으면 90초 간격으로 최대 MIN분 재확인")
    ap.add_argument("--full", action="store_true", help="모든 글 전문 출력")
    ap.add_argument("--quiet", action="store_true", help="다이제스트 출력 없이 저장만")
    a = ap.parse_args()
    now = dt.datetime.now(KST)
    day = dt.date.fromisoformat(a.date) if a.date else default_day(now)

    deadline = time.monotonic() + a.wait * 60
    while True:
        data, failed = collect(day)
        if has_close_post(data) or time.monotonic() >= deadline:
            break
        print(f"[{dt.datetime.now(KST):%H:%M}] 신한 마감 시황 아직 없음 — 90초 뒤 재확인", file=sys.stderr)
        time.sleep(90)

    if all(not v["posts"] for v in data.values()) and failed:
        print("모든 채널 수집 실패: " + " / ".join(failed))
        return 1

    RAW_DIR.mkdir(exist_ok=True)
    out_path = RAW_DIR / f"{day:%Y-%m-%d}.json"
    out_path.write_text(json.dumps({"date": day.isoformat(), "fetched_at": now.isoformat(timespec="minutes"),
                                    "failed": failed, "channels": data}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    if not a.quiet:
        print(digest(day, data, failed, full=a.full))
        print(f"\n(저장: {out_path})")
    return 0 if has_close_post(data) else 2


if __name__ == "__main__":
    sys.exit(main())
