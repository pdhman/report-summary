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


def _pie_chart(ind_counts):
    """산업 분포 도넛 차트(SVG) + 범례. ind_counts: 산업명→종목수 Series(내림차순)."""
    import math
    total = int(ind_counts.sum())
    items = list(ind_counts.items())
    if len(items) > 7:                              # 8색 팔레트: 7개 + '기타'로 접기
        items = items[:7] + [("기타", sum(int(c) for _, c in items[7:]))]

    cx = cy = 110
    R, r = 100, 55                                  # 도넛 외경/내경
    parts, legend = [], []
    ang = -90.0                                     # 12시 방향부터 시계 방향
    for i, (name, cnt) in enumerate(items):
        frac = int(cnt) / total
        a0, a1 = math.radians(ang), math.radians(ang + frac * 360)
        ang += frac * 360
        large = 1 if frac > 0.5 else 0
        x0, y0 = cx + R * math.cos(a0), cy + R * math.sin(a0)
        x1, y1 = cx + R * math.cos(a1), cy + R * math.sin(a1)
        xi1, yi1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        xi0, yi0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        pct = frac * 100
        tip = f"{name} · {int(cnt)}개 ({pct:.1f}%)"
        if frac >= 0.999:                           # 산업이 1개뿐이면 완전한 도넛
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{(R + r) / 2}" fill="none" stroke="var(--vz{i + 1})" '
                f'stroke-width="{R - r}"><title>{esc(tip)}</title></circle>')
        else:
            d = (f"M{x0:.2f} {y0:.2f} A{R} {R} 0 {large} 1 {x1:.2f} {y1:.2f} "
                 f"L{xi1:.2f} {yi1:.2f} A{r} {r} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z")
            parts.append(f'<path d="{d}" fill="var(--vz{i + 1})" stroke="var(--panel)" '
                         f'stroke-width="2"><title>{esc(tip)}</title></path>')
        if pct >= 8:                                # 큰 조각에만 % 직접 표기
            am = (a0 + a1) / 2
            lx, ly = cx + (R + r) / 2 * math.cos(am), cy + (R + r) / 2 * math.sin(am)
            parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="pie-pct">{pct:.0f}%</text>')
        legend.append(
            f'<div class="lg-row"><span class="lg-swatch" style="background:var(--vz{i + 1})"></span>'
            f'<span class="lg-name">{esc(name)}</span>'
            f'<span class="lg-val">{int(cnt)}개 · {pct:.1f}%</span></div>')

    svg = (f'<svg viewBox="0 0 220 220" role="img" aria-label="산업 분포 도넛 차트">'
           f'{"".join(parts)}</svg>')
    return f'<div class="pie-box">{svg}<div class="pie-legend">{"".join(legend)}</div></div>'


def _streak_chart(today):
    """연속 등장(2일 이상) 종목 가로 막대 차트. 없으면 None."""
    if today.empty or "streak" not in today.columns:
        return None
    d = today[today["streak"] >= 2].sort_values(["streak", "amount"], ascending=False).head(10)
    if d.empty:
        return None
    mx = int(d["streak"].max())
    rows = []
    for _, r in d.iterrows():
        s = int(r["streak"])
        rows.append(f"""
        <div class="bar-row" title="{esc(r.get('name', '-'))} · {s}일 연속">
          <div class="bar-label">{esc(r.get('name', '-'))}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{s / mx * 100:.0f}%"></div></div>
          <div class="bar-val">{s}일</div>
        </div>""")
    return "".join(rows)


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
        rows_html = ['<tr><td colspan="9" class="empty">오늘은 조건을 통과한 종목이 없습니다.</td></tr>']

    # ---- 하단 차트: 산업 분포(도넛) + 연속 등장 종목(가로 막대) ----
    pie_html = _pie_chart(ind_counts) if not ind_counts.empty else None
    streak_html = _streak_chart(today)

    html = f"""<div class="wrap">
  <header>
    <div class="eyebrow">주도섹터 필터링 · 자동 스크리너</div>
    <h1>종목탐색</h1>
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
    <div class="chart-card">{pie_html if pie_html else '<p class="muted">데이터 없음</p>'}</div>
  </section>

  <section>
    <h2>연속 등장 종목 <span class="h2sub">(2일 이상)</span></h2>
    <div class="chart-card">{streak_html if streak_html else '<p class="muted">2일 이상 연속 등장한 종목이 없습니다.</p>'}</div>
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
  /* ---- 하단 차트 공통 ---- */
  :root {{ --vz1:#2a78d6; --vz2:#008300; --vz3:#e87ba4; --vz4:#eda100;
    --vz5:#1baf7a; --vz6:#eb6834; --vz7:#4a3aa7; --vz8:#e34948; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --vz1:#3987e5; --vz2:#008300; --vz3:#d55181; --vz4:#c98500;
      --vz5:#199e70; --vz6:#d95926; --vz7:#9085e9; --vz8:#e66767; }}
  }}
  :root[data-theme="dark"] {{ --vz1:#3987e5; --vz2:#008300; --vz3:#d55181; --vz4:#c98500;
    --vz5:#199e70; --vz6:#d95926; --vz7:#9085e9; --vz8:#e66767; }}
  :root[data-theme="light"] {{ --vz1:#2a78d6; --vz2:#008300; --vz3:#e87ba4; --vz4:#eda100;
    --vz5:#1baf7a; --vz6:#eb6834; --vz7:#4a3aa7; --vz8:#e34948; }}
  .h2sub {{ color:var(--muted); font-weight:500; font-size:13px; }}
  .chart-card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px 20px; }}
  /* 도넛 + 범례 */
  .pie-box {{ display:flex; align-items:center; gap:26px; flex-wrap:wrap; }}
  .pie-box svg {{ width:220px; height:220px; flex:0 0 auto; }}
  .pie-pct {{ font-size:13px; font-weight:700; fill:#fff; text-anchor:middle; dominant-baseline:middle;
    paint-order:stroke; stroke:rgba(0,0,0,.45); stroke-width:2px; }}
  .pie-legend {{ flex:1 1 220px; min-width:220px; }}
  .lg-row {{ display:flex; align-items:center; gap:10px; padding:5px 0; font-size:13.5px; }}
  .lg-swatch {{ width:12px; height:12px; border-radius:3px; flex:0 0 auto; }}
  .lg-name {{ color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1 1 auto; }}
  .lg-val {{ color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }}
  /* 연속 등장 가로 막대 */
  .bar-row {{ display:grid; grid-template-columns:150px 1fr 44px; align-items:center; gap:12px; padding:7px 0; }}
  .bar-label {{ font-size:13.5px; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ background:color-mix(in srgb,var(--line) 60%,transparent); border-radius:6px; height:12px; }}
  .bar-fill {{ background:var(--vz1); height:12px; border-radius:0 4px 4px 0; }}
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
           f"<title>종목탐색 {date_str}</title></head><body>{html}</body></html>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[보고서] 생성 완료: {out_path}")
    # 표준출력으로 경로를 남겨 래퍼가 열 수 있게 함
    print(f"REPORT_PATH={os.path.abspath(out_path)}")


def build_index():
    """종목탐색 허브(screener.html): 상단 날짜 바 + 최신 스크리너 본문, 날짜 클릭 시 전환."""
    site_nav.build_hub(
        os.path.join(OUT_DIR, "screener.html"), "종목탐색", "stock",
        "report_*.html", r"report_(\d{8})\.html$",
    )


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--empty":
        build_empty(sys.argv[2])   # 예: python make_report.py --empty 2026-07-16
    else:
        main()
    build_index()
    try:
        import make_summary
        make_summary.build()
    except Exception as e:
        print(f"[경고] 요약 페이지 생성 건너뜀: {e}")

