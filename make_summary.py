# -*- coding: utf-8 -*-
"""
첫 페이지(index.html) '오늘의 요약' 대시보드 생성기.

기존 산출물을 재조립해 카드 4장을 만든다 (새 크롤링 없음):
  1) 시황       briefs/<최신>.md 의 '운용 전략 제언' 첫 문단
  2) 상승여력   리포트서머리.xlsx 최신 시트의 목표주가 괴리율 TOP 3
  3) 주도주     종목탐색_TOP30.xlsx 최신 날짜 요약(선정/신규/최장 연속)
  4) 주도섹터   blog/<최신>.html 의 #주도섹터 / #조정섹터 한 줄

데이터 갱신 시각이 카드마다 달라서, 카드 우측에 기준 날짜를 표기한다.
각 파이프라인(make_report·make_blog·make_brief·sync)의 마지막 단계에서 호출된다.
카드 하나가 실패해도 나머지는 그대로 렌더링한다.
"""
import os
import re
import glob
import html as _html
import datetime

import site_nav

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "reports")


def esc(s):
    return _html.escape(str(s), quote=False)


def _clean_name(s):
    """'기업명 (012345)' → '기업명'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(s)).strip()


# ---------------------------------------------------------------- 카드 데이터
def card_brief():
    """시황: 최신 브리핑의 '운용 전략 제언' 첫 문단 요약."""
    files = sorted(f for f in glob.glob(os.path.join(BASE, "briefs", "*.md"))
                   if not os.path.basename(f).startswith("_"))
    if not files:
        return None
    path = files[-1]
    date = os.path.basename(path)[:10]
    text = open(path, encoding="utf-8").read()
    # '제언' 헤딩 이후의 첫 비어있지 않은 문단
    parts = re.split(r"^#{2,3}\s.*제언.*$", text, flags=re.M)
    excerpt = ""
    if len(parts) > 1:
        for para in re.split(r"\n\s*\n", parts[1]):
            p = para.strip()
            if p:
                excerpt = p
                break
    if not excerpt:                              # 제언 섹션이 없으면 본문 첫 문단
        for para in re.split(r"\n\s*\n", text):
            p = para.strip()
            if p and not p.startswith("#"):
                excerpt = p
                break
    if not excerpt:
        return None
    # 마크다운 표식 제거
    excerpt = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", excerpt)
    excerpt = re.sub(r"[*_`>#]", "", excerpt).strip()
    # 서두의 '○○○ 님,' 같은 호칭 제거 (요약 카드에는 불필요)
    excerpt = re.sub(r"^[가-힣A-Za-z]{2,6}\s*님[,，.]?\s*", "", excerpt)
    return {"date": date, "excerpt": excerpt}


def card_upside():
    """상승여력: 리포트서머리.xlsx 최신 시트 목표주가 괴리율 TOP 3."""
    import pandas as pd
    xlsx = os.path.join(BASE, "리포트서머리.xlsx")
    if not os.path.exists(xlsx):
        return None
    xl = pd.ExcelFile(xlsx)
    dated = sorted((m.group(1), s) for s in xl.sheet_names
                   if (m := re.match(r"dt_(\d{8})$", s)))
    if not dated:
        return None
    ymd, sheet = dated[-1]
    d = pd.read_excel(xl, sheet_name=sheet)
    for c in ("목표주가", "전일수정주가"):
        d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="coerce")
    d = d.dropna(subset=["목표주가", "전일수정주가"])
    d = d[d["전일수정주가"] > 0]
    if d.empty:
        return None
    d["upside"] = (d["목표주가"] - d["전일수정주가"]) / d["전일수정주가"] * 100
    top = d.sort_values("upside", ascending=False).head(3)
    rows = [(_clean_name(r["기업명"]), float(r["upside"])) for _, r in top.iterrows()]
    return {"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "rows": rows}


def card_screener():
    """주도주: 종목탐색 최신 날짜 요약."""
    import pandas as pd
    xlsx = os.path.join(BASE, "종목탐색_TOP30.xlsx")
    if not os.path.exists(xlsx):
        return None
    df = pd.read_excel(xlsx)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    latest = df["Date"].max()
    if pd.isna(latest):
        return None
    day = df[df["Date"] == latest].drop_duplicates(subset=["ticker"], keep="first").copy()
    day["amount"] = pd.to_numeric(day.get("amount"), errors="coerce")
    # 연속 등장
    all_dates = sorted(df["Date"].dropna().unique(), reverse=True)
    def streak(tk):
        ap = set(df[df["ticker"] == tk]["Date"])
        s = 0
        for dd in all_dates:
            if dd in ap:
                s += 1
            else:
                break
        return s
    day["streak"] = day["ticker"].map(streak)
    n = len(day)
    n_new = int((day["streak"] == 1).sum())
    top_amt = day.sort_values("amount", ascending=False).iloc[0]["name"] if n else None
    best = day.sort_values(["streak", "amount"], ascending=False).iloc[0] if n else None
    return {
        "date": latest.strftime("%Y-%m-%d"), "n": n, "n_new": n_new,
        "top_amt": top_amt,
        "best_name": (best["name"] if best is not None and int(best["streak"]) >= 2 else None),
        "best_streak": (int(best["streak"]) if best is not None else 0),
    }


def _post_title(frag):
    """블로그 조각의 <h1>에서 그날의 리포트 제목만 뽑는다.

    '일간 주도 섹터 리포트(반등이 온다면- …)_26.07.21' → '반등이 온다면- …'
    괄호 제목이 없는 날(예: '일간 주도 섹터 리포트_26.07.13')은 None.
    """
    m = re.search(r"<h1>(.*?)</h1>", frag, re.S)
    if not m:
        return None
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    t = re.sub(r"[_\s]*\d{2}[.\-]\d{2}[.\-]\d{2}\s*$", "", t)   # 끝의 _26.07.21
    t = re.sub(r"_\d+\s*$", "", t).strip()                       # 끝의 _2
    pm = re.search(r"\((.+)\)\s*$", t, re.S)
    if not pm:
        return None
    inner = pm.group(1).strip()
    return inner or None


def card_sectors():
    """주도섹터: 최신 블로그 조각에서 제목 + #주도섹터 / #조정섹터 다음 문단."""
    files = sorted(glob.glob(os.path.join(BASE, "blog", "????-??-??.html")))
    if not files:
        return None
    path = files[-1]
    date = os.path.basename(path)[:10]
    frag = open(path, encoding="utf-8").read()
    title = _post_title(frag)
    paras = [re.sub(r"<[^>]+>", "", p).strip()
             for p in re.findall(r"<p[^>]*>(.*?)</p>", frag, re.S)]
    paras = [p for p in paras if p]
    def after(tag):
        for i, p in enumerate(paras):
            if tag in p and i + 1 < len(paras):
                return paras[i + 1]
        return None
    lead, adjust = after("#주도섹터"), after("#조정섹터")
    if not title and not lead and not adjust:
        return None
    return {"date": date, "title": title, "lead": lead, "adjust": adjust}


# ---------------------------------------------------------------- 렌더링
def _card(href, icon, title, date, body):
    return f"""
    <a class="scard" href="{href}">
      <div class="sc-head"><span class="sc-icon">{icon}</span><span class="sc-title">{title}</span>
        <span class="sc-date">{esc(date)}</span></div>
      <div class="sc-body">{body}</div>
      <div class="sc-more">자세히 →</div>
    </a>"""


def build():
    cards = []

    c = None
    try:
        c = card_brief()
    except Exception as e:
        print(f"[요약] 시황 카드 실패: {e}")
    if c:
        cards.append(_card("briefs.html", "📰", "오늘의 시황", c["date"],
                           f'<p class="clamp">{esc(c["excerpt"])}</p>'))

    c = None
    try:
        c = card_upside()
    except Exception as e:
        print(f"[요약] 상승여력 카드 실패: {e}")
    if c:
        rows = "".join(
            f'<div class="krow"><span class="k-name">{esc(nm)}</span>'
            f'<span class="k-val up">+{v:.1f}%</span></div>'
            for nm, v in c["rows"])
        cards.append(_card("insights.html", "🚀", "상승여력 TOP 3", c["date"], rows))

    c = None
    try:
        c = card_screener()
    except Exception as e:
        print(f"[요약] 주도주 카드 실패: {e}")
    if c:
        if c["n"]:
            body = (f'<div class="krow"><span class="k-name">선정 종목</span>'
                    f'<span class="k-val">{c["n"]}개 · 신규 {c["n_new"]}</span></div>')
            if c["top_amt"]:
                body += (f'<div class="krow"><span class="k-name">거래대금 1위</span>'
                         f'<span class="k-val">{esc(c["top_amt"])}</span></div>')
            if c["best_name"]:
                body += (f'<div class="krow"><span class="k-name">최장 연속</span>'
                         f'<span class="k-val">{esc(c["best_name"])} · {c["best_streak"]}일</span></div>')
        else:
            body = '<p class="clamp">오늘은 조건을 통과한 종목이 없습니다.</p>'
        cards.append(_card("screener.html", "🔎", "오늘의 주도주", c["date"], body))

    c = None
    try:
        c = card_sectors()
    except Exception as e:
        print(f"[요약] 주도섹터 카드 실패: {e}")
    if c:
        body = ""
        if c["title"]:
            body += f'<div class="sc-lead">{esc(c["title"])}</div>'
        if c["lead"]:
            body += (f'<div class="krow"><span class="k-name">주도</span>'
                     f'<span class="k-val">{esc(c["lead"])}</span></div>')
        if c["adjust"]:
            body += (f'<div class="krow"><span class="k-name">조정</span>'
                     f'<span class="k-val">{esc(c["adjust"])}</span></div>')
        cards.append(_card("strategy.html", "📝", "주도섹터 리포트", c["date"], body))

    import pytz
    today = datetime.datetime.now(pytz.timezone("Asia/Seoul"))   # 러너(UTC)에서도 KST 표기
    body_html = f"""<div class="wrap">
  <header>
    <div class="eyebrow">데일리 대시보드</div>
    <h1>오늘의 요약</h1>
    <div class="date">{today:%Y-%m-%d} <span class="gen">(갱신 {today:%H:%M})</span></div>
  </header>
  <div class="cards">{''.join(cards) if cards else '<p class="muted">표시할 데이터가 없습니다.</p>'}</div>
  <footer><p class="muted">본 페이지의 모든 정보는 자동 수집·생성된 참고 자료입니다. 투자 판단과 그 결과에 대한 책임은 이용자 본인에게 있습니다.</p></footer>
</div>
{site_nav.nav_html("home")}

<style>
  :root {{
    --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280;
    --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad;
      --line:#252b33; --accent:#748ffc; --up:#ff6b6b; }}
  }}
  :root[data-theme="dark"] {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad; --line:#252b33; --accent:#748ffc; --up:#ff6b6b; }}
  :root[data-theme="light"] {{ --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280; --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif; line-height:1.55; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:32px 20px 60px; }}
  header {{ padding-bottom:18px; margin-bottom:18px; }}
  .eyebrow {{ color:var(--accent); font-weight:600; font-size:13px; letter-spacing:.02em; }}
  h1 {{ margin:6px 0 4px; font-size:28px; letter-spacing:-.01em; }}
  .date {{ color:var(--muted); font-size:15px; font-weight:500; }}
  .gen {{ font-size:12px; }}
  .cards {{ display:grid; grid-template-columns:1fr; gap:14px; }}
  @media (min-width:720px) {{ .cards {{ grid-template-columns:1fr 1fr; }} }}
  .scard {{ display:block; background:var(--panel); border:1px solid var(--line); border-radius:16px;
    padding:18px 20px 14px; text-decoration:none; color:var(--ink);
    transition:border-color .15s, transform .1s; }}
  .scard:hover {{ border-color:var(--accent); }}
  .sc-head {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
  .sc-icon {{ font-size:17px; }}
  .sc-title {{ font-weight:700; font-size:15px; flex:1 1 auto; }}
  .sc-date {{ color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }}
  .sc-body {{ min-height:40px; }}
  .clamp {{ margin:0; font-size:13.5px; color:var(--ink); display:-webkit-box;
    -webkit-line-clamp:4; -webkit-box-orient:vertical; overflow:hidden; }}
  .sc-lead {{ font-size:14px; font-weight:700; line-height:1.45; margin-bottom:10px;
    padding-bottom:9px; border-bottom:1px solid var(--line); }}
  .krow {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px;
    padding:5px 0; font-size:13.5px; border-bottom:1px dashed color-mix(in srgb,var(--line) 70%,transparent); }}
  .krow:last-child {{ border-bottom:none; }}
  .k-name {{ color:var(--muted); flex:0 0 auto; }}
  .k-val {{ font-weight:600; text-align:right; }}
  .k-val.up {{ color:var(--up); font-variant-numeric:tabular-nums; }}
  .sc-more {{ margin-top:10px; color:var(--accent); font-size:13px; font-weight:600; }}
  footer {{ margin-top:26px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
</style>""" + site_nav.NAV_CSS

    os.makedirs(OUT_DIR, exist_ok=True)
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>오늘의 요약</title></head><body>{body_html}</body></html>")
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[요약] index.html 생성 (카드 {len(cards)}개)")


if __name__ == "__main__":
    build()
