# -*- coding: utf-8 -*-
"""
시황 브리핑 생성기.

입력: briefs/*.md  (Gemini 등에서 받은 시황 정리글을 마크다운으로 붙여넣은 파일)
      · 파일명에 날짜(YYYY-MM-DD 또는 YYYYMMDD)를 포함시킨다. 예) briefs/2026-07-16.md
출력: reports/brief_YYYYMMDD.html  (날짜별 시황 페이지, 디자인 통일)
      reports/briefs.html          (시황 아카이브 목록)

run_screener / sync_report_summary 파이프라인에서 build_index 앞에 호출된다.
"""
import os
import re
import glob
import datetime
import markdown as md
import site_nav

BASE = os.path.dirname(os.path.abspath(__file__))
BRIEF_DIR = os.path.join(BASE, "briefs")
OUT_DIR = os.path.join(BASE, "reports")

_SHARED_STYLE = """
<style>
  :root { --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280;
    --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131; --down:#1971c2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad;
      --line:#252b33; --accent:#748ffc; --up:#ff6b6b; --down:#4dabf7; } }
  :root[data-theme="dark"] { --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad; --line:#252b33; --accent:#748ffc; --up:#ff6b6b; --down:#4dabf7; }
  :root[data-theme="light"] { --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280; --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131; --down:#1971c2; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif; line-height:1.6; }
  .wrap { max-width:820px; margin:0 auto; padding:32px 20px 60px; }
  header { border-bottom:1px solid var(--line); padding-bottom:20px; margin-bottom:24px; }
  .eyebrow { color:var(--accent); font-weight:600; font-size:13px; letter-spacing:.02em; }
  h1 { margin:6px 0 4px; font-size:28px; letter-spacing:-.01em; }
  .date { color:var(--muted); font-size:15px; font-weight:500; }
  .gen { font-size:12px; }
  .topnav { margin-top:10px; font-size:13px; }
  .topnav a { color:var(--accent); text-decoration:none; font-weight:600; }
  .prose { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; }
  .prose h1 { font-size:22px; margin:18px 0 10px; }
  .prose h2 { font-size:18px; margin:22px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--line); }
  .prose h3 { font-size:15px; margin:18px 0 8px; color:var(--accent); }
  .prose p { margin:10px 0; }
  .prose ul, .prose ol { margin:10px 0; padding-left:22px; }
  .prose li { margin:4px 0; }
  .prose strong { font-weight:700; }
  .prose blockquote { margin:12px 0; padding:8px 14px; border-left:3px solid var(--accent);
    background:color-mix(in srgb,var(--accent) 7%,transparent); color:var(--muted); border-radius:0 8px 8px 0; }
  .prose code { background:color-mix(in srgb,var(--line) 60%,transparent); padding:1px 5px; border-radius:5px; font-size:.92em; }
  .prose table { border-collapse:collapse; width:100%; margin:12px 0; font-size:14px; }
  .prose th, .prose td { border:1px solid var(--line); padding:8px 10px; text-align:left; }
  .prose thead th { background:color-mix(in srgb,var(--line) 40%,transparent); }
  .prose a { color:var(--accent); }
  .prose hr { border:none; border-top:1px solid var(--line); margin:18px 0; }
  .list { list-style:none; margin:0; padding:0; }
  .list li { margin-bottom:10px; }
  .list a { display:flex; align-items:center; gap:14px; text-decoration:none; color:var(--ink);
    background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; transition:border-color .15s; }
  .list a:hover { border-color:var(--accent); }
  .d { font-weight:700; font-size:16px; font-variant-numeric:tabular-nums; }
  .go { margin-left:auto; color:var(--accent); font-weight:600; font-size:14px; }
  footer { margin-top:30px; }
  .muted { color:var(--muted); font-size:12px; }
</style>
"""


def _parse_date(name):
    m = re.search(r"(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})", name)
    return "".join(m.groups()) if m else None


# 개인 호칭 등 게시 전 제거할 문구(정확 일치, 콤마/공백 변형 포함)
_STRIP_PHRASES = ["박동현 님, ", "박동현 님,", "박동현 님 ", "박동현 님"]


def _clean(text):
    """게시 전 자동 정리:
    1) '데일리 브리핑' 제목 줄 이전의 서두(인삿말·머리말)를 제거.
       (해당 문구가 없으면 원문 유지 — 안전 장치)
    2) 개인 호칭('박동현 님') 제거.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "데일리 브리핑" in line:
            lines = lines[i:]
            break
    text = "\n".join(lines)
    for p in _STRIP_PHRASES:
        text = text.replace(p, "")
    return text.strip()


def _wrap(title, body):
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title></head><body>{body}"
            f"{site_nav.nav_html('brief')}{_SHARED_STYLE}{site_nav.NAV_CSS}</body></html>")


def build():
    if not os.path.isdir(BRIEF_DIR):
        print("[시황] briefs 폴더 없음 — 건너뜀")
        return
    files = [f for f in glob.glob(os.path.join(BRIEF_DIR, "*.md"))
             if not os.path.basename(f).startswith("_")]  # _TEMPLATE.md 등 제외
    briefs = []
    for f in files:
        ymd = _parse_date(os.path.basename(f))
        if not ymd:
            print(f"[시황] 날짜 인식 실패 — 건너뜀: {os.path.basename(f)}")
            continue
        with open(f, encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            continue
        text = _clean(text)
        body_html = md.markdown(text, extensions=["extra", "sane_lists", "nl2br"])
        pretty = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        page = f"""<div class="wrap">
  <header>
    <div class="eyebrow">데일리 · 시황 브리핑</div>
    <h1>시황 브리핑</h1>
    <div class="date">{pretty} <span class="gen">(Gemini 정리)</span></div>
    <nav class="topnav"><a href="index.html">← 아카이브 목록</a> · <a href="briefs.html">지난 시황 →</a></nav>
  </header>
  <article class="prose">{body_html}</article>
  <footer><p class="muted">본 시황은 외부 생성(Gemini) 정리글을 게시한 참고 자료이며 투자 권유가 아닙니다.</p></footer>
</div>"""
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f"brief_{ymd}.html"), "w", encoding="utf-8") as fh:
            fh.write(_wrap(f"시황 브리핑 {pretty}", page))
        briefs.append(ymd)

    briefs = sorted(set(briefs), reverse=True)
    if not briefs:
        print("[시황] 생성할 브리핑 없음")
        return

    # 시황 아카이브 목록(briefs.html)
    items = []
    for ymd in briefs:
        pretty = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        items.append(f'<li><a href="brief_{ymd}.html"><span class="d">{pretty}</span>'
                     f'<span class="go">시황 보기 →</span></a></li>')
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = f"""<div class="wrap">
  <header>
    <div class="eyebrow">시황 브리핑 · 아카이브</div>
    <h1>지난 시황 목록</h1>
    <div class="date">총 {len(briefs)}건 · 최종 갱신 {gen}</div>
    <nav class="topnav"><a href="index.html">← 리포트 아카이브</a></nav>
  </header>
  <ul class="list">{''.join(items)}</ul>
  <footer><p class="muted">가장 최근 날짜가 맨 위에 표시됩니다. 자동 생성됨.</p></footer>
</div>"""
    with open(os.path.join(OUT_DIR, "briefs.html"), "w", encoding="utf-8") as fh:
        fh.write(_wrap("시황 브리핑 아카이브", body))
    print(f"[시황] 생성 완료: {len(briefs)}건 (최신 {briefs[0]})")


if __name__ == "__main__":
    build()
