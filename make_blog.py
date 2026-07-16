# -*- coding: utf-8 -*-
"""
'투자전략' 섹션 생성기.

입력: blog/<YYYY-MM-DD>.html  (scrape_blog.py 가 만든 본문 조각: header + article.prose)
출력: reports/blog_YYYYMMDD.html  (날짜별 페이지)
      reports/index.html          (투자전략 허브: 날짜 바 + 최신 본문)

본문의 이미지는 reports/blogimg/ 를 가리킨다(scrape_blog 가 저장).
"""
import os
import re
import glob
import site_nav
from make_brief import _SHARED_STYLE   # .prose 등 공용 타이포그래피 재사용

BASE = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR = os.path.join(BASE, "blog")
OUT_DIR = os.path.join(BASE, "reports")

_EXTRA = """<style>
  .bhead { border-bottom:1px solid var(--line); padding-bottom:18px; margin-bottom:22px; }
  .bhead h1 { font-size:23px; font-weight:800; line-height:1.4; margin:6px 0 6px; }
  .bhead .date { color:var(--muted); font-size:14px; }
  .bhead .date a { color:var(--accent); text-decoration:none; }
  .prose { font-size:15.5px; }
  .prose p { margin:14px 0; line-height:1.8; }
  .prose strong { font-weight:700; }
  .prose mark { background:rgba(254,240,138,.5); color:inherit; font-weight:600;
    padding:2px 5px; border-radius:4px; box-decoration-break:clone; -webkit-box-decoration-break:clone; }
  .prose .sp { height:12px; margin:0; padding:0; }
  .prose .bimg { text-align:center; margin:20px 0; }
  .prose .bimg img { max-width:100%; height:auto; border-radius:8px; border:1px solid var(--line); }
</style>"""

_STYLE = _SHARED_STYLE + _EXTRA


def build():
    if not os.path.isdir(BLOG_DIR):
        print("[투자전략] blog 폴더 없음 — 건너뜀")
        return
    made = 0
    for f in sorted(glob.glob(os.path.join(BLOG_DIR, "*.html"))):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\.html$", os.path.basename(f))
        if not m:
            continue
        ymd = m.group(1) + m.group(2) + m.group(3)
        with open(f, encoding="utf-8") as fh:
            frag = fh.read().strip()
        if not frag:
            continue
        tm = re.search(r"<h1>(.*?)</h1>", frag, re.S)
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else "투자전략"
        body = f'<div class="wrap">{frag}</div>\n{site_nav.nav_html("strategy")}'
        full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title></head><body>{body}"
                f"{_STYLE}{site_nav.NAV_CSS}</body></html>")
        with open(os.path.join(OUT_DIR, f"blog_{ymd}.html"), "w", encoding="utf-8") as fh:
            fh.write(full)
        made += 1

    # 투자전략 허브(index.html): 날짜 바 + 최신 본문
    site_nav.build_hub(
        os.path.join(OUT_DIR, "index.html"), "투자전략 · 일간 주도섹터 리포트", "strategy",
        "blog_*.html", r"blog_(\d{8})\.html$", fallback_style=_STYLE,
    )
    print(f"[투자전략] 페이지 {made}개 + index.html 허브 생성")


if __name__ == "__main__":
    build()
