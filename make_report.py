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
import site_nav

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

    date_str = latest.strftime("%Y-%m-%d") if pd.notna(latest) else "-"
    _write_report(today, date_str)


def build_empty(date_str):
    """선정 종목 0개인 날의 빈 리포트 생성(날짜 공백 방지용)."""
    cols = ["ticker", "name", "amount", "change_ratio", "rs_score",
            "return_252d", "close", "Industry", "streak"]
    _write_report(pd.DataFrame(columns=cols), date_str)


def _write_report(today, date_str):
    """today(DataFrame; 비어 있을 수 있음) → reports/report_YYYYMMDD.html 생성."""
    # 요약 통계 (0종목이어도 안전하게)
    n = len(today)
    n_new = int((today["streak"] == 1).sum()) if (n and "streak" in today.columns) else 0
    avg_rs = today["rs_score"].mean() if (n and "rs_score" in today.columns) else float("nan")
    ind_counts = today["Industry"].value_counts() if (n and "Industry" in today.columns) else pd.Series(dtype=int)
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
    if not rows_html:
        rows_html = ['<tr><td colspan="9" class="empty">이 날은 조건을 통과한 선정 종목이 없습니다.</td></tr>']

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
    <div class="eyebrow">주도섹터 필터링 · 자동 스크리너</div>
    <h1>종목분석</h1>
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
          <th class="num">RS</th><th class="num">1년 수익률</th><th class="num">종가</th><th>산업</th><th>등장</th>
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
{site_nav.nav_html("stock")}

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
  .empty {{ text-align:center; color:var(--muted); padding:28px 12px; font-size:14px; }}
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
</style>""" + site_nav.NAV_CSS

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = date_str.replace("-", "")
    out_path = os.path.join(OUT_DIR, f"report_{stamp}.html")
    full = "<!doctype html><html lang='ko'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           f"<title>종목분석 {date_str}</title></head><body>{html}</body></html>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[보고서] 생성 완료: {out_path}")
    # 표준출력으로 경로를 남겨 래퍼가 열 수 있게 함
    print(f"REPORT_PATH={os.path.abspath(out_path)}")


def build_index():
    """종목분석 허브(screener.html): 상단 날짜 바 + 최신 스크리너 본문, 날짜 클릭 시 전환."""
    site_nav.build_hub(
        os.path.join(OUT_DIR, "screener.html"), "종목분석", "stock",
        "report_*.html", r"report_(\d{8})\.html$",
    )


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--empty":
        build_empty(sys.argv[2])   # 예: python make_report.py --empty 2026-07-16
    else:
        main()
    build_index()
