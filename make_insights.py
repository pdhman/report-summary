# -*- coding: utf-8 -*-
"""
리포트서머리.xlsx → 인사이트 HTML 페이지 생성기

두 가지 표를 담은 self-contained HTML 페이지를 만든다.
  (1) 오늘자(가장 최근 수집일) 가격괴리율 TOP5
      괴리율 = (목표주가 - 리포트시점주가) / 리포트시점주가 × 100
  (2) 이번달 리포트 종목 중 실제 상승률 TOP10
      실제상승률 = (현재가 - 최초 리포트시점주가) / 최초 리포트시점주가 × 100
      · 현재가는 FinanceDataReader 의 KRX 상장종목 종가로 조회
      · 한 종목이 여러 번 등장하면 '이번달 최초 리포트'를 진입 기준으로 삼는다.

출력: reports/insights_YYYYMMDD.html  (YYYYMMDD = 가장 최근 수집일)
"""
import os
import re
import datetime
import pandas as pd

XLSX = "리포트서머리.xlsx"
OUT_DIR = "reports"
TOP_GAP = 5      # 괴리율 TOP N
TOP_RET = 10     # 실제 상승률 TOP N


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_int(x):
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


def _load_sheet(xl, sheet):
    d = pd.read_excel(xl, sheet_name=sheet)
    d["code"] = d["기업명"].str.extract(r"\((\d{6})\)")[0]
    d["name"] = d["기업명"].str.replace(r"\s*\(\d{6}\)", "", regex=True).str.strip()
    d["목표주가"] = pd.to_numeric(d["목표주가"], errors="coerce")
    d["전일수정주가"] = pd.to_numeric(d["전일수정주가"], errors="coerce")
    d["수집일자"] = pd.to_datetime(d["수집일자"], errors="coerce")
    return d


def build():
    if not os.path.exists(XLSX):
        print(f"[인사이트] {XLSX} 없음 — 건너뜀")
        return

    xl = pd.ExcelFile(XLSX)
    # 시트 이름 dt_YYYYMMDD 에서 날짜 파싱
    dated = []
    for s in xl.sheet_names:
        m = re.match(r"dt_(\d{8})$", s)
        if m:
            dated.append((m.group(1), s))
    if not dated:
        print("[인사이트] dt_YYYYMMDD 시트 없음 — 건너뜀")
        return
    dated.sort(reverse=True)             # 최신 날짜 먼저
    latest_ymd, latest_sheet = dated[0]
    month_prefix = latest_ymd[:6]        # 이번달 (최신 시트의 YYYYMM)
    month_sheets = [s for ymd, s in dated if ymd.startswith(month_prefix)]

    # ---- (1) 오늘자 괴리율 TOP5 ----
    L = _load_sheet(xl, latest_sheet)
    L = L[(L["목표주가"] > 0) & (L["전일수정주가"] > 0) & L["code"].notna()].copy()
    L["괴리율"] = (L["목표주가"] - L["전일수정주가"]) / L["전일수정주가"] * 100
    # 같은 종목 여러 리포트 → 가장 공격적인(괴리율 높은) 목표가 하나만
    L = L.sort_values("괴리율", ascending=False).drop_duplicates("code", keep="first")
    gap_top = L.head(TOP_GAP).reset_index(drop=True)

    # ---- (2) 이번달 실제 상승률 TOP10 ----
    import FinanceDataReader as fdr
    frames = [_load_sheet(xl, s) for s in month_sheets]
    J = pd.concat(frames, ignore_index=True)
    J = J[(J["전일수정주가"] > 0) & J["code"].notna()].copy()
    J = J.sort_values("수집일자")
    first = J.drop_duplicates("code", keep="first").copy()  # 이번달 최초 리포트 = 진입
    try:
        krx = fdr.StockListing("KRX").set_index("Code")["Close"]
    except Exception as e:
        print(f"[인사이트] 현재가 조회 실패: {e}")
        krx = pd.Series(dtype=float)
    first["현재가"] = pd.to_numeric(first["code"].map(krx), errors="coerce")
    first = first[first["현재가"] > 0].copy()
    first["실제상승률"] = (first["현재가"] - first["전일수정주가"]) / first["전일수정주가"] * 100
    ret_top = first.sort_values("실제상승률", ascending=False).head(TOP_RET).reset_index(drop=True)

    # ---- 요약 통계 ----
    month_uni = J["code"].nunique()
    winners = int((first["실제상승률"] > 0).sum())
    win_rate = (winners / len(first) * 100) if len(first) else float("nan")
    avg_ret = first["실제상승률"].mean() if len(first) else float("nan")

    date_str = f"{latest_ymd[:4]}-{latest_ymd[4:6]}-{latest_ymd[6:]}"
    month_str = f"{month_prefix[:4]}-{month_prefix[4:]}"
    gen_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- (1) 괴리율 행 ----
    gap_rows = []
    for i, r in gap_top.iterrows():
        gap = r["괴리율"]
        gap_rows.append(f"""
      <tr>
        <td class="rank">{i+1}</td>
        <td class="name"><span class="nm">{esc(r['name'])}</span><span class="code">{esc(r['code'])}</span></td>
        <td class="num">{fmt_int(r['전일수정주가'])}</td>
        <td class="num strong">{fmt_int(r['목표주가'])}</td>
        <td class="num up">+{gap:.1f}%</td>
        <td class="op">{esc(r.get('투자의견','-') if pd.notna(r.get('투자의견')) else '-')}</td>
      </tr>""")

    # ---- (2) 실제 상승률 행 ----
    ret_rows = []
    for i, r in ret_top.iterrows():
        ret = r["실제상승률"]
        cls = "up" if ret > 0 else ("down" if ret < 0 else "flat")
        sign = "+" if ret > 0 else ""
        ed = r["수집일자"].strftime("%m-%d") if pd.notna(r["수집일자"]) else "-"
        ret_rows.append(f"""
      <tr>
        <td class="rank">{i+1}</td>
        <td class="name"><span class="nm">{esc(r['name'])}</span><span class="code">{esc(r['code'])}</span></td>
        <td class="num muted-c">{ed}</td>
        <td class="num">{fmt_int(r['전일수정주가'])}</td>
        <td class="num">{fmt_int(r['현재가'])}</td>
        <td class="num {cls} strong">{sign}{ret:.1f}%</td>
      </tr>""")

    body = f"""<div class="wrap">
  <header>
    <div class="eyebrow">리포트서머리 · 애널리스트 리포트 분석</div>
    <h1>리포트 인사이트</h1>
    <div class="date">{date_str} 기준 <span class="gen">(생성 {gen_str})</span></div>
    <nav class="topnav"><a href="index.html">← 아카이브 목록</a></nav>
  </header>

  <section class="cards">
    <div class="card"><div class="k">이번달 리포트 종목</div><div class="v">{month_uni}<span>개</span></div></div>
    <div class="card"><div class="k">상승 종목</div><div class="v">{winners}<span>개</span></div></div>
    <div class="card"><div class="k">상승 비율</div><div class="v">{('%.0f'%win_rate) if win_rate==win_rate else '-'}<span>%</span></div></div>
    <div class="card"><div class="k">평균 상승률</div><div class="v {'up' if avg_ret>0 else 'down'}">{('%+.1f'%avg_ret) if avg_ret==avg_ret else '-'}<span>%</span></div></div>
  </section>

  <section>
    <h2>오늘자 가격괴리 TOP{TOP_GAP} <span class="sub">목표주가까지 상승여력이 큰 종목</span></h2>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th>#</th><th>종목</th><th class="num">전일종가</th><th class="num">목표주가</th>
          <th class="num">괴리율</th><th>투자의견</th>
        </tr></thead>
        <tbody>{''.join(gap_rows)}</tbody>
      </table>
    </div>
    <p class="note">괴리율 = (목표주가 − 리포트시점 주가) ÷ 리포트시점 주가 × 100. 같은 종목의 여러 리포트 중 가장 공격적인 목표가 기준.</p>
  </section>

  <section>
    <h2>이번달 실제 상승률 TOP{TOP_RET} <span class="sub">{month_str} 리포트 종목의 현재가 기준 실현 수익률</span></h2>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th>#</th><th>종목</th><th class="num">최초등장</th><th class="num">리포트주가</th>
          <th class="num">현재가</th><th class="num">실제 상승률</th>
        </tr></thead>
        <tbody>{''.join(ret_rows)}</tbody>
      </table>
    </div>
    <p class="note">실제 상승률 = (현재가 − 이번달 최초 리포트시점 주가) ÷ 리포트시점 주가 × 100. 현재가는 최근 거래일 종가.</p>
  </section>

  <footer>
    <p class="muted">본 리포트는 증권사 리포트를 집계·가공한 참고 자료이며 투자 권유가 아닙니다. · 데이터: 리포트서머리.xlsx + FinanceDataReader</p>
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
  .topnav {{ margin-top:10px; }}
  .topnav a {{ color:var(--accent); text-decoration:none; font-size:13px; font-weight:600; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:28px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .card .k {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
  .card .v {{ font-size:26px; font-weight:700; }}
  .card .v span {{ font-size:14px; font-weight:500; color:var(--muted); margin-left:2px; }}
  .card .v.up {{ color:var(--up); }} .card .v.down {{ color:var(--down); }}
  h2 {{ font-size:17px; margin:26px 0 12px; }}
  h2 .sub {{ font-size:13px; font-weight:500; color:var(--muted); margin-left:6px; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--panel); }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; min-width:560px; }}
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
  .op {{ color:var(--muted); font-size:13px; }}
  .muted-c {{ color:var(--muted); }}
  .note {{ color:var(--muted); font-size:12px; margin:10px 2px 0; }}
  footer {{ margin-top:30px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
  @media (max-width:640px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style>"""

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"insights_{latest_ymd}.html")
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>리포트 인사이트 {date_str}</title></head><body>{body}</body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"[인사이트] 생성 완료: {out_path}")
    print(f"REPORT_PATH={os.path.abspath(out_path)}")


if __name__ == "__main__":
    build()
