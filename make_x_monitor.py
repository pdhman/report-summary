# -*- coding: utf-8 -*-
"""
X(트위터) 모니터링 리포트 게시기.

입력: x-monitor/reports/*.md  (X 리스트 수집·요약 일일 리포트)
출력: reports/x_YYYYMMDD.html  (날짜별 페이지, 디자인 통일)
      reports/x.html           (허브: 날짜 바 + 본문 전환)

make_summary.card_x() 에서 호출된다. x-monitor 폴더가 없는 환경(예: 과거
체크아웃)에서는 조용히 건너뛰어 다른 카드에 영향을 주지 않는다.
"""
import os
import re
import glob
import markdown as md
import site_nav
from make_brief import _SHARED_STYLE

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE, "x-monitor", "reports")
OUT_DIR = os.path.join(BASE, "reports")


def _files():
    return sorted(f for f in glob.glob(os.path.join(SRC_DIR, "????-??-??.md")))


def _wrap(title, body):
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title></head><body>{body}"
            f"{site_nav.nav_html('x')}{_SHARED_STYLE}{site_nav.NAV_CSS}</body></html>")


def build():
    files = _files()
    if not files:
        print("[X모니터] 리포트 없음 — 건너뜀")
        return
    for f in files:
        ymd = os.path.basename(f)[:10].replace("-", "")
        text = open(f, encoding="utf-8").read().strip()
        if not text:
            continue
        # 문서 첫 h1은 페이지 헤더와 중복되므로 제거
        text = re.sub(r"^#\s.*\n", "", text, count=1)
        body_html = md.markdown(text, extensions=["extra", "sane_lists"])
        pretty = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        page = f"""<div class="wrap">
  <header>
    <div class="eyebrow">데일리 · X 모니터링</div>
    <h1>X 모니터링</h1>
    <div class="date">{pretty} <span class="gen">(관심 계정 리스트 수집)</span></div>
  </header>
  <article class="prose">{body_html}</article>
  <footer><p class="muted">본 페이지는 X(트위터) 공개 게시물을 수집·요약한 참고 자료이며 투자 권유가 아닙니다.</p></footer>
</div>"""
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f"x_{ymd}.html"), "w", encoding="utf-8") as fh:
            fh.write(_wrap(f"X 모니터링 {pretty}", page))

    site_nav.build_hub(
        os.path.join(OUT_DIR, "x.html"), "X 모니터링", "x",
        "x_????????.html", r"x_(\d{8})\.html$",
        fallback_style=_SHARED_STYLE,
    )
    print(f"[X모니터] 생성 완료: {len(files)}건")


def latest_card():
    """요약 카드용 데이터: 최신 리포트의 날짜·건수·총평."""
    files = _files()
    if not files:
        return None
    path = files[-1]
    date = os.path.basename(path)[:10]
    text = open(path, encoding="utf-8").read()
    m_cnt = re.search(r"\*\*수집 건수\*\*:\s*(\d+)건", text)
    m_sum = re.search(r"\*\*총평\*\*:\s*(.+)", text)
    return {
        "date": date,
        "count": int(m_cnt.group(1)) if m_cnt else None,
        "summary": m_sum.group(1).strip() if m_sum else None,
    }


if __name__ == "__main__":
    build()
