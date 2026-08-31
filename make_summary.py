# -*- coding: utf-8 -*-
"""
첫 페이지(index.html) '알파노트' 대시보드 생성기.

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
OUT_DIR = os.path.join(BASE, "docs")


def esc(s):
    return _html.escape(str(s), quote=False)


def _clean_name(s):
    """'기업명 (012345)' → '기업명'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(s)).strip()


def _strip_html(s):
    """HTML 조각 → 순수 텍스트. 엔티티(&#x27; 등)를 풀어 esc() 가 이중
    이스케이프(&amp;#x27;)하지 않게 한다."""
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


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


def _ensure_fresh_xlsx(xlsx):
    """리포트서머리.xlsx 가 오늘 것이 아니면 드라이브에서 내려받아 최신화.

    로컬(스크리너·블로그 래퍼)에서 요약을 만들 때 오래된 엑셀을 읽어
    '상승여력' 카드 날짜가 과거로 굳는 문제를 막는다. 실패해도 기존
    파일로 계속 진행한다(러너에서는 이미 최신이라 건너뜀).
    """
    try:
        import pytz
        now = datetime.datetime.now(pytz.timezone("Asia/Seoul"))
        # 크롤러가 09:23(1차)·16:43(2차)에 드라이브를 갱신하므로, 'mtime 이
        # 오늘'만으로는 부족하다 — 오늘 08:52 파일로 09:5x 에 만들면 어제
        # 데이터가 박제된다(2026-08-24 실사고). 마지막으로 지난 크롤 완료
        # 시각(09:35 / 16:55)을 컷오프로 쓴다.
        fresh_after = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for h, m in ((9, 35), (16, 55)):
            c = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now >= c:
                fresh_after = c
        if os.path.exists(xlsx):
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(xlsx),
                                                    pytz.timezone("Asia/Seoul"))
            if mtime >= fresh_after:
                return
        import sync_report_summary
        sync_report_summary.download_from_drive()
        print("[요약] 리포트서머리.xlsx 최신화 완료")
    except Exception as e:
        print(f"[요약] xlsx 최신화 건너뜀(기존 파일 사용): {e}")


def card_upside():
    """상승여력: 리포트서머리.xlsx 최신 시트 목표주가 괴리율 TOP 3."""
    import pandas as pd
    xlsx = os.path.join(BASE, "리포트서머리.xlsx")
    _ensure_fresh_xlsx(xlsx)
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
    # 한 종목에 증권사별 리포트가 여러 건 있어 상위권을 같은 종목이 중복 차지한다.
    # 종목(코드) 기준으로 괴리율이 가장 큰 한 건만 남겨 서로 다른 3종목이 나오게 한다.
    code = d["기업명"].astype(str).str.extract(r"\((\d{6})\)")[0]
    d = d.assign(_key=code.fillna(d["기업명"].astype(str).map(_clean_name)))
    d = (d.sort_values("upside", ascending=False)
           .drop_duplicates(subset=["_key"], keep="first"))
    top = d.head(3)
    rows = [(_clean_name(r["기업명"]), float(r["upside"])) for _, r in top.iterrows()]
    return {"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "rows": rows}


def card_screener():
    """주도주: 종목탐색 최신 날짜 요약.

    선정 종목이 0개인 날은 xlsx 에 저장되지 않으므로(스크리너 사양),
    실제 생성된 리포트 페이지(report_YYYYMMDD.html)의 최신 날짜를 함께 보고
    그날이 더 최신이면 '0종목' 상태로 표시한다.
    """
    import pandas as pd
    import make_report
    xlsx = os.path.join(BASE, make_report.xlsx_path())
    if not os.path.exists(xlsx):
        return None
    df = pd.read_excel(xlsx)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    latest = df["Date"].max()
    if pd.isna(latest):
        return None

    # 리포트 페이지 기준 최신일이 xlsx 보다 앞서면 그날은 선정 0종목
    pages = sorted(re.search(r"report_(\d{8})\.html$", os.path.basename(p)).group(1)
                   for p in glob.glob(os.path.join(OUT_DIR, "report_????????.html")))
    if pages and pages[-1] > latest.strftime("%Y%m%d"):
        ymd = pages[-1]
        return {"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "n": 0, "n_new": 0,
                "top_amt": None, "best_name": None, "best_streak": 0}
    day = df[df["Date"] == latest].drop_duplicates(subset=["ticker"], keep="first").copy()
    day["amount"] = pd.to_numeric(day.get("amount"), errors="coerce")
    # 연속 등장: 거래일 기준 (make_report 와 동일 로직)
    import make_report
    day["streak"] = day["ticker"].map(make_report.compute_streaks(df, latest))
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
    t = _strip_html(m.group(1))
    t = re.sub(r"[_\s]*\d{2}[.\-]\d{2}[.\-]\d{2}\s*$", "", t)   # 끝의 _26.07.21
    t = re.sub(r"_\d+\s*$", "", t).strip()                       # 끝의 _2
    pm = re.search(r"\((.+)\)\s*$", t, re.S)
    if not pm:
        return None
    inner = pm.group(1).strip()
    return inner or None


def card_leverage():
    """시장 레버리지: 신용융자·예탁금·반대매매비중 + 차트 페이지 재생성."""
    import sys
    mod_dir = os.path.join(BASE, "market_leverage_collector")
    if not os.path.exists(os.path.join(mod_dir, "data", "credit_balance.csv")):
        return None
    if mod_dir not in sys.path:
        sys.path.insert(0, mod_dir)
    import make_chart
    make_chart.build(out=os.path.join(OUT_DIR, "leverage.html"), nav_active="leverage")
    return make_chart.latest_stats()


def card_x():
    """X 모니터링: 최신 리포트 요약 + 게시 페이지(x_*.html/x.html) 재생성."""
    import make_x_monitor
    make_x_monitor.build()
    return make_x_monitor.latest_card()


def card_sectors():
    """주도섹터: 최신 블로그 조각에서 제목 + #주도섹터 / #조정섹터 다음 문단."""
    files = sorted(glob.glob(os.path.join(BASE, "blog", "????-??-??.html")))
    if not files:
        return None
    path = files[-1]
    date = os.path.basename(path)[:10]
    frag = open(path, encoding="utf-8").read()
    title = _post_title(frag)
    paras = [_strip_html(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", frag, re.S)]
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


def card_flows():
    """수급 동향: 최근 거래일 투자자별 순매수 + 대시보드 페이지(flow.html) 재생성.

    수급모니터링/ 의 CSV·dashboard_data.js 는 로컬 작업(수급동향_1740_수집)이
    매일 갱신·푸시한다. 여기서는 대시보드를 docs/ 경로로 복사만 한다.
    """
    import csv
    flow_dir = os.path.join(BASE, "수급모니터링")

    def last_row(name):
        path = os.path.join(flow_dir, "data", name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return rows[-1] if rows else None

    k = last_row("kospi.csv")
    if not k:
        return None

    # 대시보드를 사이트 경로로 복사: 데이터 참조 교체 + 하단 내비 주입 +
    # 테마 저장 키를 사이트 공용('theme')으로 통일
    html = open(os.path.join(flow_dir, "dashboard.html"), encoding="utf-8").read()
    html = html.replace('src="dashboard_data.js"', 'src="flow_data.js"')
    html = html.replace("flow-theme", "theme")
    # 내비 CSS 가 쓰는 사이트 변수(--panel 등)를 대시보드에 없으므로 채워 넣는다
    nav_shim = (
        "<style>\n"
        "  :root { --panel:#ffffff; --line:#e6e8eb; --accent:#3b5bdb; --muted:#6b7280; }\n"
        '  :root[data-theme="dark"] { --panel:#171b21; --line:#252b33; '
        "--accent:#748ffc; --muted:#9aa2ad; }\n"
        "</style>"
    )
    html = html.replace(
        "</body>", site_nav.nav_html("flow") + nav_shim + site_nav.NAV_CSS + "</body>")
    with open(os.path.join(OUT_DIR, "flow.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(flow_dir, "dashboard_data.js"), encoding="utf-8") as f:
        data_js = f.read()
    with open(os.path.join(OUT_DIR, "flow_data.js"), "w", encoding="utf-8") as f:
        f.write(data_js)

    fut = last_row("futures.csv")
    return {
        "date": k["date"],
        "indiv": int(k["individual"]),
        "forgn": int(k["foreign"]),
        "inst": int(k["inst_total"]),
        "fut_forgn": int(fut["foreign"]) if fut else None,
    }


def card_market():
    """시장 건전성: breadth_build.py(quant-data)가 만든 요약 JSON 읽기.

    market.html 은 정적 페이지고 market_data.js 를 시장건전성_1635_수집 작업이
    매일 갱신한다. 여기서는 홈 카드 내용만 만든다.
    """
    import json
    path = os.path.join(OUT_DIR, "data", "market_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def card_crypto():
    """크립토: crypto_monitor.py(매일 11:00)가 만든 요약 JSON 읽기.

    대시보드 자체는 crypto-monitor/crypto.html 로 만들어지므로 여기서는
    사이트 경로로 복사만 한다(수급 동향 flow.html 과 같은 방식).
    """
    import json
    cdir = os.path.join(BASE, "crypto-monitor")
    path = os.path.join(cdir, "crypto_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        c = json.load(f)

    src = os.path.join(cdir, "crypto.html")
    if os.path.exists(src):
        html = open(src, encoding="utf-8").read()
        # 내비 CSS 가 쓰는 사이트 변수(--panel 등)가 이 대시보드에 없으므로 채워 넣는다.
        # 값은 flow.html 과 동일 — 두 페이지가 같은 dataviz 팔레트를 쓴다.
        nav_shim = (
            "<style>\n"
            "  :root { --panel:#ffffff; --line:#e6e8eb; --accent:#3b5bdb; --muted:#6b7280; }\n"
            '  :root[data-theme="dark"] { --panel:#171b21; --line:#252b33; '
            "--accent:#748ffc; --muted:#9aa2ad; }\n"
            "</style>"
        )
        html = html.replace(
            "</body>", site_nav.nav_html("crypto") + nav_shim + site_nav.NAV_CSS + "</body>")
        with open(os.path.join(OUT_DIR, "crypto.html"), "w", encoding="utf-8") as f:
            f.write(html)
    return c


def card_rs():
    """RS 스크리너: 1개월 RS 상위 테마 2개 + ETF 종합 RS 상위 2개.

    rs_build.py(주간)가 만든 rs_data.js 를 읽는다. ETF 는 화면 기본값과
    동일하게 레버리지·인버스 제외.
    """
    import json
    path = os.path.join(OUT_DIR, "rs_data.js")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    d = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))
    themes = [t for t in d.get("themes", [])
              if t.get("n", 0) >= 5 and t.get("r1") is not None]
    themes.sort(key=lambda t: t["r1"], reverse=True)
    etfs = [e for e in d.get("etfs", []) if e[5] is not None and not e[15]]
    return {"date": d.get("asof", ""), "themes": themes[:2], "etfs": etfs[:2]}


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
        cards.append(_card("briefs.html", "📰", "오늘의 뉴스", c["date"],
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

    c = None
    try:
        c = card_x()
    except Exception as e:
        print(f"[요약] X 모니터링 카드 실패: {e}")
    if c:
        body = ""
        if c["count"] is not None:
            body += (f'<div class="krow"><span class="k-name">수집</span>'
                     f'<span class="k-val">{c["count"]}건</span></div>')
        if c.get("topics"):
            body += "".join(f'<div class="xtopic">{esc(t)}</div>' for t in c["topics"])
            rest = c.get("topic_total", 0) - len(c["topics"])
            if rest > 0:
                body += f'<div class="xmore">외 {rest}개 주제</div>'
        elif c.get("summary"):                       # 주제별 정리가 없는 옛 리포트
            body += f'<p class="clamp">{esc(c["summary"])}</p>'
        if body:
            cards.append(_card("x.html", "𝕏", "X 모니터링", c["date"], body))

    c = None
    try:
        c = card_market()
    except Exception as e:
        print(f"[요약] 시장 건전성 카드 실패: {e}")
    if c:
        labels = ["냉각", "낮음", "중립", "높음", "과열"]
        body = ""
        if c.get("overall") is not None:
            lab = labels[min(4, int(c["overall"] // 20))]
            body += (f'<div class="krow"><span class="k-name">종합 체온</span>'
                     f'<span class="k-val">{c["overall"]:.0f} · {lab}</span></div>')
        if c.get("fg") is not None:
            # CNN 원판 구간: 0~25 극단공포 / 25~45 공포 / 45~55 중립 / 55~75 탐욕 / 75~ 극단탐욕
            fg = c["fg"]
            fg_lab = ("극단 공포" if fg < 25 else "공포" if fg < 45 else
                      "중립" if fg < 55 else "탐욕" if fg < 75 else "극단 탐욕")
            body += (f'<div class="krow"><span class="k-name">공포탐욕</span>'
                     f'<span class="k-val">{fg:.0f} · {fg_lab}</span></div>')
        if c.get("ma200") is not None:
            body += (f'<div class="krow"><span class="k-name">200일선 위</span>'
                     f'<span class="k-val">{c["ma200"]:.1f}%</span></div>')
        if c.get("nh") is not None and c.get("nl") is not None:
            body += (f'<div class="krow"><span class="k-name">52주 신고/신저</span>'
                     f'<span class="k-val"><span class="k-diff up">{c["nh"]}</span> / '
                     f'<span class="k-diff down">{c["nl"]}</span></span></div>')
        if c.get("vkospi") is not None:
            body += (f'<div class="krow"><span class="k-name">VKOSPI</span>'
                     f'<span class="k-val">{c["vkospi"]:.2f}</span></div>')
        if body:
            cards.append(_card("market.html", "🌡️", "시장 건전성", c["date"], body))

    c = None
    try:
        c = card_leverage()
    except Exception as e:
        print(f"[요약] 레버리지 카드 실패: {e}")
    if c:
        def _row(name, val, diff, unit, dec=2):
            cls = "up" if diff > 0 else ("down" if diff < 0 else "")
            sign = "+" if diff > 0 else ""
            return (f'<div class="krow"><span class="k-name">{name}</span>'
                    f'<span class="k-val">{val:,.{dec}f}{unit} '
                    f'<span class="k-diff {cls}">{sign}{diff:,.{dec}f}</span></span></div>')
        body = (_row("신용거래융자", c["credit"], c["credit_d"], "조원")
                + _row("투자자예탁금", c["deposit"], c["deposit_d"], "조원")
                + _row("반대매매비중", c["ratio"], c["ratio_d"], "%"))
        # 헤더 날짜는 '갱신일'. 금투협이 T 자료를 T+1 에 공표해 수치는 하루 전
        # 기준이므로, 오해가 없도록 실제 기준일을 아래에 작게 덧붙인다.
        upd = c.get("updated", c["date"])
        if upd != c["date"]:
            body += f'<div class="sc-note">수치는 {esc(c["date"])} 기준</div>'
        cards.append(_card("leverage.html", "📈", "시장 레버리지", upd, body))

    c = None
    try:
        c = card_crypto()
    except Exception as e:
        print(f"[요약] 크립토 카드 실패: {e}")
    if c and c.get("btc_price") is not None:
        def _crow(name, val, diff=None, dec=2, unit=""):
            v = f'{val:,.{dec}f}{unit}'
            if diff is not None:
                cls = "up" if diff > 0 else ("down" if diff < 0 else "")
                sign = "+" if diff > 0 else ""
                v += f' <span class="k-diff {cls}">{sign}{diff:,.2f}</span>'
            return (f'<div class="krow"><span class="k-name">{name}</span>'
                    f'<span class="k-val">{v}</span></div>')
        body = _crow("BTC", c["btc_price"], c.get("btc_chg_24h"), 0, "$")
        if c.get("rsi14") is not None:
            body += _crow("RSI(14)", c["rsi14"], None, 1)
        if c.get("mvrv") is not None:
            body += _crow("MVRV", c["mvrv"], None, 2)
        if c.get("etf_last_flow") is not None:
            f_ = c["etf_last_flow"]
            cls = "up" if f_ > 0 else ("down" if f_ < 0 else "")
            body += (f'<div class="krow"><span class="k-name">ETF 순유입</span>'
                     f'<span class="k-val {cls}">{"+" if f_ > 0 else ""}{f_:,.0f}M$</span></div>')
            # 헤더 날짜는 갱신일(KST). ETF 는 미국 세션 기준이라 하루 이상 차이가
            # 나므로 실제 기준일을 작게 덧붙인다(시장 레버리지 카드와 같은 이유).
            if c.get("etf_last_date"):
                body += f'<div class="sc-note">ETF는 {esc(c["etf_last_date"])} 기준</div>'
        cards.append(_card("crypto.html", "₿", "크립토", c.get("date", ""), body))

    # 분석 도구(차트·계절성·RS)는 상시 제공되는 정적 도구라 항상 카드 노출
    cards.append(_card(
        "chart.html", "📈", "분석 도구", "차트 · 계절성 · RS",
        '<div class="krow"><span class="k-name">주식 차트</span>'
        '<span class="k-val">캔들 · 이동평균 · Log</span></div>'
        '<div class="krow"><span class="k-name">계절성 분석</span>'
        '<span class="k-val">월별 통계 · 최적 진입 · 히트맵</span></div>'
        '<div class="krow"><span class="k-name">RS 스크리너</span>'
        '<span class="k-val">섹터 · 업종 · 테마 랭킹</span></div>'))

    c = None
    try:
        c = card_rs()
    except Exception as e:
        print(f"[요약] RS 카드 실패: {e}")
    if c and (c["themes"] or c["etfs"]):
        body = ""
        for i, t in enumerate(c["themes"]):
            # 테마명 괄호 부연은 카드에서 생략 (예: '로봇(산업용/협동로봇 등)' → '로봇')
            name = t["name"].split("(")[0].strip()
            body += (f'<div class="krow"><span class="k-name">{"테마 1M" if i == 0 else ""}</span>'
                     f'<span class="k-val">{esc(name)} '
                     f'<span class="k-diff up">{t["r1"]:.1f}</span></span></div>')
        for i, e_ in enumerate(c["etfs"]):
            body += (f'<div class="krow"><span class="k-name">{"ETF RS" if i == 0 else ""}</span>'
                     f'<span class="k-val">{esc(e_[1])} '
                     f'<span class="k-diff up">{e_[5]:.0f}</span></span></div>')
        cards.append(_card("rs.html", "🔥", "RS 스크리너", c["date"], body))

    c = None
    try:
        c = card_flows()
    except Exception as e:
        print(f"[요약] 수급 카드 실패: {e}")
    if c:
        def _frow(name, v):
            cls = "up" if v > 0 else ("down" if v < 0 else "")
            sign = "+" if v > 0 else ""
            return (f'<div class="krow"><span class="k-name">{name}</span>'
                    f'<span class="k-val {cls}">{sign}{v:,}억</span></div>')
        body = (_frow("개인", c["indiv"]) + _frow("외국인", c["forgn"])
                + _frow("기관", c["inst"]))
        if c["fut_forgn"] is not None:
            body += _frow("선물 외국인", c["fut_forgn"])
        cards.append(_card("flow.html", "💰", "수급 동향",
                           f'{c["date"]} · 코스피', body))

    import pytz
    today = datetime.datetime.now(pytz.timezone("Asia/Seoul"))   # 러너(UTC)에서도 KST 표기
    body_html = f"""<div class="wrap">
  <header>
    <div class="eyebrow">데일리 대시보드</div>
    <h1>알파노트</h1>
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
  .xtopic {{ font-size:13px; line-height:1.5; padding:6px 0; display:flex; gap:7px;
    border-bottom:1px dashed color-mix(in srgb,var(--line) 70%,transparent); }}
  .xtopic:last-of-type {{ border-bottom:none; }}
  .xtopic::before {{ content:"·"; color:var(--accent); font-weight:700; flex:0 0 auto; }}
  .xmore {{ margin-top:6px; font-size:12px; color:var(--muted); }}
  .k-name {{ color:var(--muted); flex:0 0 auto; }}
  .k-val {{ font-weight:600; text-align:right; }}
  .k-val.up {{ color:var(--up); font-variant-numeric:tabular-nums; }}
  .k-val.down {{ color:var(--accent); font-variant-numeric:tabular-nums; }}
  .k-diff {{ font-weight:600; font-size:12.5px; font-variant-numeric:tabular-nums; color:var(--muted); }}
  .sc-note {{ margin-top:7px; color:var(--muted); font-size:11.5px;
    font-variant-numeric:tabular-nums; }}
  .k-diff.up {{ color:var(--up); }}
  .k-diff.down {{ color:var(--accent); }}
  .sc-more {{ margin-top:10px; color:var(--accent); font-size:13px; font-weight:600; }}
  footer {{ margin-top:26px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
</style>""" + site_nav.NAV_CSS

    os.makedirs(OUT_DIR, exist_ok=True)
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>알파노트</title></head><body>{body_html}</body></html>")
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[요약] index.html 생성 (카드 {len(cards)}개)")


if __name__ == "__main__":
    build()
