# -*- coding: utf-8 -*-
"""
네이버 블로그(pdhman)의 '일간 주도 섹터 리포트'를 사이트로 가져오는 스크래퍼.

- PostTitleListAsync API 로 글 목록을 열거 → 제목에 '주도'+'섹터' 포함 & 최근 N일치만.
- 각 글: 모바일 페이지(m.blog.naver.com)에서 se-main-container 의 본문을
  '순서대로'(텍스트/이미지 섞인 채) 추출.
- 이미지는 reports/blogimg/ 에 다운로드하고 로컬 경로로 치환.
- 결과 본문 조각을 blog/<YYYY-MM-DD>.html 로 저장(헤더/스타일 없는 fragment).
  → make_blog.py 가 이를 읽어 페이지/허브로 렌더한다.

한 번 받은 글은 다시 받지 않는다(파일 있으면 건너뜀). --force 로 강제 재수집.
"""
import os
import re
import sys
import html
import time
import urllib.request
import urllib.parse

BLOG_ID = "pdhman"
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "blog")            # 본문 조각
IMG_DIR = os.path.join(BASE, "reports", "blogimg")  # 이미지(사이트에서 서빙)
CUTOFF = "2026-06-16"                            # 이 날짜 이후만 (최근 한달치)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def _get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://blog.naver.com/"})
    data = urllib.request.urlopen(req, timeout=25).read()
    return data if binary else data.decode("utf-8", "replace")


def list_posts():
    """(logNo, title, date) 목록 — 최근 한달치 주도섹터 리포트만, 최신순."""
    seen, out = set(), []
    for page in range(1, 12):
        raw = _get(f"https://blog.naver.com/PostTitleListAsync.naver?blogId={BLOG_ID}"
                   f"&currentPage={page}&categoryNo=0&countPerPage=30")
        rows = re.findall(r'"logNo":"(\d+)".*?"title":"(.*?)"', raw)
        if not rows:
            break
        for logno, title in rows:
            if logno in seen:
                continue
            seen.add(logno)
            title = html.unescape(urllib.parse.unquote(title.replace("+", " ")))
            if "주도" in title and "섹터" in title:
                m = re.search(r"(\d{2})[.\-](\d{2})[.\-](\d{2})", title) or re.search(r"_(\d{2})(\d{2})(\d{2})", title)
                date = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
                if date and date >= CUTOFF:
                    out.append((logno, title, date))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def _clean_title(title):
    # "일간 주도 섹터 리포트(부제)_26.07.16" → 부제 위주로 정리
    t = re.sub(r"_\d{2}[.\-]?\d{2}[.\-]?\d{2}\s*$", "", title).strip()
    return t


def _download_image(url, date, idx):
    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    ext = ".jpg"
    mm = re.search(r"\.(jpg|jpeg|png|gif|webp)", url, re.I)
    if mm:
        ext = "." + mm.group(1).lower()
    name = f"{date}_{idx}{ext}"
    path = os.path.join(IMG_DIR, name)
    if not os.path.exists(path):
        try:
            data = _get(url, binary=True)
            os.makedirs(IMG_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            print(f"    [이미지 실패] {url[:70]} — {e}")
            return None
    return f"blogimg/{name}"


def _render_para(inner):
    """문단 inner HTML → 굵게(<strong>)·형광펜(<mark>)만 보존한 안전한 HTML."""
    spans = re.findall(r"<span([^>]*)>(.*?)</span>", inner, re.S)
    if not spans:
        t = html.unescape(re.sub(r"<[^>]+>", "", inner)).replace("​", "").strip()
        return html.escape(t)
    out = []
    for attrs, content in spans:
        c = re.sub(r"<(?:b|strong)(?:\s[^>]*)?>", "\x01B\x01", content, flags=re.I)
        c = re.sub(r"</(?:b|strong)>", "\x01b\x01", c, flags=re.I)
        c = re.sub(r"<[^>]+>", "", c)                 # 남은 태그 제거
        c = html.unescape(c).replace("​", "")
        if not c.strip():
            continue
        c = html.escape(c).replace("\x01B\x01", "<strong>").replace("\x01b\x01", "</strong>")
        mm = re.search(r"background-color\s*:\s*(#[0-9a-fA-F]{3,8}|[a-zA-Z]+)", attrs)
        if mm and mm.group(1).lower().lstrip("#") not in ("transparent", "white", "fff", "ffffff"):
            c = f"<mark>{c}</mark>"
        out.append(c)
    return "".join(out)


def _extract_body(post_html, date):
    """se-main-container 의 se-component 들을 순서대로 → HTML 조각(텍스트/이미지)."""
    s = post_html.find('<div class="se-main-container">')
    if s == -1:
        return ""
    end = post_html.find("<!-- SE_DOC_FOOTER", s)
    container = post_html[s:end if end != -1 else s + 60000]

    parts = []
    img_idx = 0
    # se-component 단위로 순서 보존 분할 ('se-component se-...' 만; se-component-content 제외)
    comps = re.split(r'(<div class="se-component se-[^"]*")', container)
    # comps: [pre, '<div class="se-component se-text...', chunk, '<div class="se-component se-image...', chunk, ...]
    i = 1
    while i < len(comps):
        cls = comps[i]
        chunk = comps[i + 1] if i + 1 < len(comps) else ""
        if "se-image" in cls:
            # 이미지 URL: data-linkdata 의 "src" 우선, 없으면 <img> 태그
            mm = re.search(r'"src"\s*:\s*"([^"]+)"', chunk)
            if not mm:
                mm = re.search(r'<img[^>]+(?:data-lazy-src|data-src|src)="([^"]+)"', chunk)
            url = html.unescape(mm.group(1)) if mm else None
            if url and url.startswith("http"):
                img_idx += 1
                local = _download_image(url, date, img_idx)
                if local:
                    parts.append(f'<p class="bimg"><img src="{local}" loading="lazy" alt=""></p>')
        elif "se-text" in cls:
            for p in re.findall(r'<p class="se-text-paragraph[^"]*"[^>]*>(.*?)</p>', chunk, re.S):
                rendered = _render_para(p)
                if rendered.strip():
                    parts.append(f"<p>{rendered}</p>")
                else:
                    parts.append('<p class="sp"></p>')  # 빈 줄
        i += 2
    # 연속 빈 줄 정리
    out, prev_blank = [], False
    for p in parts:
        blank = 'class="sp"' in p
        if blank and prev_blank:
            continue
        out.append(p)
        prev_blank = blank
    return "\n".join(out)


def scrape_one(logno, title, date, force=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{date}.html")
    if os.path.exists(out_path) and not force:
        return "skip"
    post = _get(f"https://m.blog.naver.com/{BLOG_ID}/{logno}")
    body = _extract_body(post, date)
    if not body.strip():
        print(f"    [본문 비어있음] {date} {logno}")
        return "empty"
    subtitle = _clean_title(title)
    frag = (f'<header class="bhead"><div class="eyebrow">일간 주도섹터 리포트</div>'
            f'<h1>{html.escape(subtitle)}</h1>'
            f'<div class="date">{date} · <a href="https://blog.naver.com/{BLOG_ID}/{logno}" '
            f'target="_blank" rel="noopener">네이버 블로그 원문 →</a></div></header>\n'
            f'<article class="prose">{body}</article>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(frag)
    return "ok"


def main():
    force = "--force" in sys.argv
    posts = list_posts()
    print(f"대상 글: {len(posts)}개 (>= {CUTOFF})")
    stats = {"ok": 0, "skip": 0, "empty": 0}
    for logno, title, date in posts:
        try:
            r = scrape_one(logno, title, date, force=force)
        except Exception as e:
            print(f"  [실패] {date} {logno}: {e}")
            r = "empty"
        stats[r] = stats.get(r, 0) + 1
        print(f"  {date} · {r} · {title[:38]}")
        time.sleep(0.4)
    print(f"완료: 신규 {stats['ok']} / 건너뜀 {stats['skip']} / 빈본문 {stats['empty']}")


if __name__ == "__main__":
    main()
