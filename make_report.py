# -*- coding: utf-8 -*-
"""
주도섹터 필터링 결과 → 깔끔한 HTML 보고서 생성기

- 종목탐색_TOP30.xlsx 의 '가장 최근 날짜' 데이터를 읽어
- reports\report_YYYYMMDD.html 로 self-contained(단일 파일) 보고서를 만든다.
- 매일 실행 래퍼(run_screener.ps1)의 마지막 단계에서 호출된다.
"""
import os
import sys
import glob
import re
import datetime
import pandas as pd

XLSX = "종목탐색_TOP30.xlsx"
OUT_DIR = "reports"


def fmt_int(x):
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    if not os.path.exists(XLSX):
        print(f"[보고서] {XLSX} 없음 — 보고서 생성 건너뜀")
        return

    df = pd.read_excel(XLSX)
    if df.empty:
        print("[보고서] 데이터 없음 — 건너뜀")
        return

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    latest = df["Date"].max()
    today = df[df["Date"] == latest].copy()
    # 종목 기준 중복 제거(같은 날 여러 번 실행분)
    today = today.drop_duplicates(subset=["ticker"], keep="first")

    # 숫자화
    for c in ["amount", "rs_score", "return_252d", "change_ratio", "close"]:
        if c in today.columns:
            today[c] = pd.to_numeric(today[c], errors="coerce")
    today = today.sort_values("amount", ascending=False).reset_index(drop=True)

    # 연속 등장(Streak) 계산: 최근 날짜부터 연속으로 등장한 일수
    all_dates = sorted(df["Date"].dropna().unique(), reverse=True)
    streaks = {}
    for tk in today["ticker"].unique():
        appeared = set(df[df["ticker"] == tk]["Date"])
        s = 0
        for d in all_dates:
            if d in appeared:
                s += 1
            else:
                break
        streaks[tk] = s
    today["streak"] = today["ticker"].map(streaks)

    # 요약 통계
    n = len(today)
    n_new = int((today["streak"] == 1).sum())
    avg_rs = today["rs_score"].mean() if "rs_score" in today else float("nan")
    ind_counts = today["Industry"].value_counts() if "Industry" in today else pd.Series(dtype=int)

    date_str = latest.strftime("%Y-%m-%d") if pd.notna(latest) else "-"
    gen_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- 종목 테이블 행 ----
    rows_html = []
    for i, r in today.iterrows():
        code = str(int(r["ticker"])).zfill(6) if pd.notna(r["ticker"]) else "-"
        chg = r.get("change_ratio", float("nan"))
        chg_cls = "up" if pd.notna(chg) and chg > 0 else ("down" if pd.notna(chg) and chg < 0 else "flat")
        chg_txt = (f"{chg:+.2f}%" if pd.notna(chg) else "-")
        streak = int(r.get("streak", 1) or 1)
        streak_badge = f'<span class="badge new">NEW</span>' if streak == 1 else f'<span class="badge streak">{streak}일 연속</span>'
        rs = r.get("rs_score", float("nan"))
        ret = r.get("return_252d", float("nan"))
        rows_html.append(f"""
      <tr>
        <td class="rank">{i+1}</td>
        <td class="name"><span class="nm">{esc(r.get('name','-'))}</span><span class="code">{code}</span></td>
        <td class="num strong">{fmt_int(r.get('amount'))}</td>
        <td class="num {chg_cls}">{chg_txt}</td>
        <td class="num">{('%.1f'%rs) if pd.notna(rs) else '-'}</td>
        <td class="num">{('%.1f%%'%ret) if pd.notna(ret) else '-'}</td>
        <td class="num">{fmt_int(r.get('close'))}</td>
        <td class="ind">{esc(r.get('Industry','-'))}</td>
        <td class="st">{streak_badge}</td>
      </tr>""")

    # ---- 산업 분포 막대 ----
    ind_html = []
    if not ind_counts.empty:
        mx = int(ind_counts.max())
        for name, cnt in ind_counts.items():
            pct = int(cnt) / mx * 100
            ind_html.append(f"""
        <div class="bar-row">
          <div class="bar-label">{esc(name)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct:.0f}%"></div></div>
          <div class="bar-val">{int(cnt)}</div>
        </div>""")

    html = f"""<div class="wrap">
  <header>
    <div class="eyebrow">주도섹터 필터링 · 미너비니 트렌드 템플릿</div>
    <h1>주도섹터 리포트</h1>
    <div class="date">{date_str} <span class="gen">(생성 {gen_str})</span></div>
  </header>

  <section class="cards">
    <div class="card"><div class="k">선정 종목</div><div class="v">{n}<span>개</span></div></div>
    <div class="card"><div class="k">신규 진입</div><div class="v">{n_new}<span>개</span></div></div>
    <div class="card"><div class="k">평균 RS 점수</div><div class="v">{('%.1f'%avg_rs) if avg_rs==avg_rs else '-'}</div></div>
    <div class="card"><div class="k">산업 수</div><div class="v">{ind_counts.shape[0]}<span>개</span></div></div>
  </section>

  <section>
    <h2>선정 종목 (거래대금 순)</h2>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th>#</th><th>종목</th><th class="num">거래대금(백만)</th><th class="num">등락률</th>
          <th class="num">RS</th><th class="num">252일수익</th><th class="num">종가</th><th>산업</th><th>등장</th>
        </tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>산업 분포</h2>
    <div class="bars">{''.join(ind_html) if ind_html else '<p class="muted">데이터 없음</p>'}</div>
  </section>

  <footer>
    <p class="muted">본 리포트는 자동 생성된 참고 자료이며 투자 권유가 아닙니다. · RS: 상대강도(백분위) · 거래대금 단위: 백만원</p>
  </footer>
</div>

<style>
  :root {{
    --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280;
    --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131; --down:#1971c2;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad;
      --line:#252b33; --accent:#748ffc; --up:#ff6b6b; --down:#4dabf7; }}
  }}
  :root[data-theme="dark"] {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad; --line:#252b33; --accent:#748ffc; --up:#ff6b6b; --down:#4dabf7; }}
  :root[data-theme="light"] {{ --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#6b7280; --line:#e6e8eb; --accent:#3b5bdb; --up:#e03131; --down:#1971c2; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif; line-height:1.5; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:32px 20px 60px; }}
  header {{ border-bottom:1px solid var(--line); padding-bottom:20px; margin-bottom:24px; }}
  .eyebrow {{ color:var(--accent); font-weight:600; font-size:13px; letter-spacing:.02em; }}
  h1 {{ margin:6px 0 4px; font-size:28px; letter-spacing:-.01em; }}
  .date {{ color:var(--muted); font-size:15px; font-weight:500; }}
  .gen {{ font-size:12px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:28px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .card .k {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
  .card .v {{ font-size:26px; font-weight:700; }}
  .card .v span {{ font-size:14px; font-weight:500; color:var(--muted); margin-left:2px; }}
  h2 {{ font-size:17px; margin:26px 0 12px; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--panel); }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; min-width:720px; }}
  thead th {{ text-align:left; color:var(--muted); font-weight:600; font-size:12px;
    padding:12px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  tbody td {{ padding:12px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }}
  tbody tr:last-child td {{ border-bottom:none; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .rank {{ color:var(--muted); width:28px; }}
  .name .nm {{ font-weight:600; }}
  .name .code {{ display:block; color:var(--muted); font-size:12px; }}
  .strong {{ font-weight:700; }}
  .up {{ color:var(--up); font-weight:600; }}
  .down {{ color:var(--down); font-weight:600; }}
  .ind {{ color:var(--muted); font-size:13px; }}
  .badge {{ display:inline-block; font-size:11px; font-weight:700; padding:3px 8px; border-radius:20px; white-space:nowrap; }}
  .badge.new {{ background:color-mix(in srgb,var(--accent) 18%,transparent); color:var(--accent); }}
  .badge.streak {{ background:color-mix(in srgb,var(--up) 16%,transparent); color:var(--up); }}
  .bars {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .bar-row {{ display:grid; grid-template-columns:160px 1fr 40px; align-items:center; gap:12px; padding:6px 0; }}
  .bar-label {{ font-size:13px; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ background:color-mix(in srgb,var(--line) 60%,transparent); border-radius:6px; height:10px; }}
  .bar-fill {{ background:var(--accent); height:10px; border-radius:6px; }}
  .bar-val {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); font-size:13px; }}
  footer {{ margin-top:30px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
  @media (max-width:640px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} .bar-row {{ grid-template-columns:110px 1fr 34px; }} }}
</style>"""

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = latest.strftime("%Y%m%d") if pd.notna(latest) else datetime.datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(OUT_DIR, f"report_{stamp}.html")
    full = "<!doctype html><html lang='ko'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           f"<title>주도섹터 리포트 {date_str}</title></head><body>{html}</body></html>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[보고서] 생성 완료: {out_path}")
    # 표준출력으로 경로를 남겨 래퍼가 열 수 있게 함
    print(f"REPORT_PATH={os.path.abspath(out_path)}")


def build_index():
    """reports 폴더의 모든 일별 보고서를 모아 index.html(목록) 생성."""
    files = glob.glob(os.path.join(OUT_DIR, "report_*.html"))
    dates = []
    for f in files:
        m = re.search(r"report_(\d{8})\.html$", os.path.basename(f))
        if m:
            dates.append(m.group(1))
    dates = sorted(set(dates), reverse=True)
    if not dates:
        return

    # 날짜별 선정 종목 수.
    # 종목탐색_TOP30.xlsx 는 로컬에만 있고(*.xlsx는 gitignore) 클라우드에는 없으므로,
    # 집계 결과를 reports/counts.json 사이드카에 커밋해 두고 클라우드에서도 재사용한다.
    import json
    counts_path = os.path.join(OUT_DIR, "counts.json")
    counts = {}
    # 1) 커밋된 사이드카 우선 로드 (클라우드에서 개수 표시용)
    if os.path.exists(counts_path):
        try:
            with open(counts_path, encoding="utf-8") as f:
                counts = {str(k): int(v) for k, v in json.load(f).items()}
        except Exception:
            counts = {}
    # 2) 로컬에 엑셀이 있으면 최신 집계로 갱신하고 사이드카에 반영
    if os.path.exists(XLSX):
        try:
            d = pd.read_excel(XLSX)
            d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
            for k, g in d.groupby(d["Date"].dt.strftime("%Y%m%d")):
                counts[str(k)] = int(g["ticker"].nunique())
            with open(counts_path, "w", encoding="utf-8") as f:
                json.dump(counts, f, ensure_ascii=False, indent=0)
        except Exception:
            pass

    items = []
    for ds in dates:
        pretty = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
        cnt = counts.get(ds)
        cnt_html = f'<span class="cnt">{cnt}종목</span>' if cnt is not None else ""
        items.append(
            f'<li><a href="report_{ds}.html"><span class="d">{pretty}</span>{cnt_html}'
            f'<span class="go">리포트 보기 →</span></a></li>'
        )

    # 리포트 인사이트 페이지(가장 최근)로 가는 배너 링크
    insight_files = glob.glob(os.path.join(OUT_DIR, "insights_*.html"))
    insight_html = ""
    if insight_files:
        latest_ins = sorted(insight_files, reverse=True)[0]
        ins_name = os.path.basename(latest_ins)
        insight_html = (
            f'<a class="feature" href="{ins_name}">'
            f'<span class="ftag">인사이트</span>'
            f'<span class="ftxt"><b>리포트 인사이트</b>오늘자 가격괴리 TOP5 · 이번달 실제 상승률 TOP10</span>'
            f'<span class="go">보기 →</span></a>'
        )

    # 시황 브리핑(가장 최근)으로 가는 배너 링크
    brief_files = glob.glob(os.path.join(OUT_DIR, "brief_*.html"))
    brief_html = ""
    if brief_files:
        latest_bf = sorted(brief_files, reverse=True)[0]
        m = re.search(r"brief_(\d{8})\.html$", os.path.basename(latest_bf))
        bf_date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""
        brief_html = (
            f'<a class="feature" href="{os.path.basename(latest_bf)}">'
            f'<span class="ftag">시황</span>'
            f'<span class="ftxt"><b>시황 브리핑</b>{bf_date} 시장 요약 · 지난 시황은 아카이브에서</span>'
            f'<span class="go">보기 →</span></a>'
        )

    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = f"""<div class="wrap">
  <header>
    <div class="eyebrow">주도섹터 리포트 · 아카이브</div>
    <h1>일별 리포트 목록</h1>
    <div class="date">총 {len(dates)}일치 · 최종 갱신 {gen}</div>
  </header>
  {brief_html}
  {insight_html}
  <ul class="list">{''.join(items)}</ul>
  <footer><p class="muted">가장 최근 날짜가 맨 위에 표시됩니다. 자동 생성됨.</p></footer>
</div>
<style>
  :root {{ --bg:#f6f7f9; --panel:#fff; --ink:#1a1d21; --muted:#6b7280; --line:#e6e8eb; --accent:#3b5bdb; }}
  @media (prefers-color-scheme:dark) {{ :root {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad; --line:#252b33; --accent:#748ffc; }} }}
  :root[data-theme="dark"] {{ --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa2ad; --line:#252b33; --accent:#748ffc; }}
  :root[data-theme="light"] {{ --bg:#f6f7f9; --panel:#fff; --ink:#1a1d21; --muted:#6b7280; --line:#e6e8eb; --accent:#3b5bdb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:32px 20px 60px; }}
  header {{ border-bottom:1px solid var(--line); padding-bottom:18px; margin-bottom:20px; }}
  .eyebrow {{ color:var(--accent); font-weight:600; font-size:13px; }}
  h1 {{ margin:6px 0 4px; font-size:26px; }}
  .date {{ color:var(--muted); font-size:14px; }}
  .feature {{ display:flex; align-items:center; gap:14px; text-decoration:none; color:var(--ink);
    background:color-mix(in srgb,var(--accent) 8%,var(--panel)); border:1px solid color-mix(in srgb,var(--accent) 35%,var(--line));
    border-radius:12px; padding:16px 18px; margin-bottom:18px; transition:border-color .15s; }}
  .feature:hover {{ border-color:var(--accent); }}
  .ftag {{ background:var(--accent); color:#fff; font-size:11px; font-weight:700; padding:4px 9px; border-radius:20px; white-space:nowrap; }}
  .ftxt {{ font-size:13px; color:var(--muted); line-height:1.35; }}
  .ftxt b {{ display:block; color:var(--ink); font-size:15px; margin-bottom:1px; }}
  .list {{ list-style:none; margin:0; padding:0; }}
  .list li {{ margin-bottom:10px; }}
  .list a {{ display:flex; align-items:center; gap:14px; text-decoration:none; color:var(--ink);
    background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; transition:border-color .15s; }}
  .list a:hover {{ border-color:var(--accent); }}
  .d {{ font-weight:700; font-size:16px; font-variant-numeric:tabular-nums; }}
  .cnt {{ color:var(--muted); font-size:13px; }}
  .go {{ margin-left:auto; color:var(--accent); font-weight:600; font-size:14px; }}
  footer {{ margin-top:24px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
</style>"""
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>주도섹터 리포트 아카이브</title></head><body>" + body + "</body></html>")
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[보고서] 목록 갱신: {os.path.join(OUT_DIR, 'index.html')} ({len(dates)}일치)")


if __name__ == "__main__":
    main()
    build_index()
