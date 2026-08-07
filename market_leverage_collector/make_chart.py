# -*- coding: utf-8 -*-
"""
수집한 레버리지 데이터 → 단독 HTML 차트 페이지(leverage.html) 생성.

data/credit_balance.csv, data/market_funds.csv 를 읽어
  · KPI 타일 3개(현재값 + 전일 대비)
  · 신용거래융자 추이(유가증권/코스닥 2계열)
  · 투자자예탁금 추이(1계열)
  · 미수금 대비 반대매매비중 추이(1계열)
  · 원자료 표(접기)
를 담은 self-contained HTML 을 만든다. 외부 라이브러리·폰트 없이 인라인 SVG 만 쓴다.

실행:  chart.cmd  (또는 python make_chart.py)
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "leverage.html")

# 검증된 팔레트: 카테고리 슬롯 1·2 (문서상 all-pairs 통과 구간), 라이트/다크 각각
S1L, S2L = "#2a78d6", "#eb6834"     # blue, orange (light)
S1D, S2D = "#3987e5", "#d95926"     # blue, orange (dark)


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").replace("-", None), errors="coerce")


def load():
    cb = pd.read_csv(os.path.join(DATA, "credit_balance.csv"))
    mf = pd.read_csv(os.path.join(DATA, "market_funds.csv"))
    for d in (cb, mf):
        d["date"] = pd.to_datetime(d["구 분"], format="%Y/%m/%d", errors="coerce")
        d.dropna(subset=["date"], inplace=True)
        d.sort_values("date", inplace=True)          # 오래된 → 최신
        d.reset_index(drop=True, inplace=True)
    return cb, mf


# ---------------------------------------------------------------- SVG 차트
def line_chart(cid, dates, series, unit, decimals=1):
    """series: [(이름, [값...], 라이트색, 다크색)] — 1~2계열."""
    W, H = 880, 300
    ML, MR, MT, MB = 62, 92, 18, 34
    pw, ph = W - ML - MR, H - MT - MB

    vals = [v for _, ys, _, _ in series for v in ys if v == v]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or (abs(hi) * 0.05 or 1)
    lo, hi = lo - pad, hi + pad

    def X(i):
        return ML + (pw * i / max(len(dates) - 1, 1))

    def Y(v):
        return MT + ph - (ph * (v - lo) / (hi - lo))

    # y 눈금 5개
    ticks = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        ticks.append(f'<line x1="{ML}" y1="{Y(v):.1f}" x2="{ML + pw}" y2="{Y(v):.1f}" class="grid"/>'
                     f'<text x="{ML - 10}" y="{Y(v) + 4:.1f}" class="ax ax-y">{v:,.{decimals}f}</text>')

    # x 눈금 5개(날짜)
    xt = []
    step = max((len(dates) - 1) // 4, 1)
    for i in range(0, len(dates), step):
        xt.append(f'<text x="{X(i):.1f}" y="{MT + ph + 22}" class="ax ax-x">{dates[i][5:]}</text>')

    paths, dots, labels = [], [], []
    for name, ys, cl, cd in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if v == v)
        paths.append(f'<polyline points="{pts}" class="ln" style="--cl:{cl};--cd:{cd}"/>')
        last = len(ys) - 1
        dots.append(f'<circle cx="{X(last):.1f}" cy="{Y(ys[last]):.1f}" r="4.5" '
                    f'class="dot" style="--cl:{cl};--cd:{cd}"/>')
        # 직접 라벨(오른쪽 여백)
        labels.append(f'<text x="{ML + pw + 10}" y="{Y(ys[last]) + 4:.1f}" class="dlabel">{html.escape(name)}</text>')

    payload = {"dates": dates, "series": [{"name": n, "ys": ys} for n, ys, _, _ in series],
               "unit": unit, "dec": decimals,
               "geom": {"ML": ML, "MT": MT, "pw": pw, "ph": ph, "lo": lo, "hi": hi}}

    return f'''<div class="chartbox">
  <svg viewBox="0 0 {W} {H}" class="chart" id="{cid}" role="img">
    {''.join(ticks)}
    <line x1="{ML}" y1="{MT + ph}" x2="{ML + pw}" y2="{MT + ph}" class="axis"/>
    {''.join(xt)}
    {''.join(paths)}{''.join(dots)}{''.join(labels)}
    <line class="cross" id="{cid}-cross" x1="0" y1="{MT}" x2="0" y2="{MT + ph}" style="display:none"/>
    <rect x="{ML}" y="{MT}" width="{pw}" height="{ph}" fill="transparent" id="{cid}-hit"/>
  </svg>
  <div class="tip" id="{cid}-tip"></div>
  <script>window.__CH = window.__CH || {{}}; window.__CH["{cid}"] = {json.dumps(payload, ensure_ascii=False)};</script>
</div>'''


def kpi(label, value, prev, unit, decimals=1):
    diff = value - prev
    pct = (diff / prev * 100) if prev else 0
    cls = "up" if diff > 0 else ("down" if diff < 0 else "flat")
    sign = "+" if diff > 0 else ""
    return f'''<div class="tile">
      <div class="k">{html.escape(label)}</div>
      <div class="v">{value:,.{decimals}f}<span class="u">{unit}</span></div>
      <div class="d {cls}">{sign}{diff:,.{decimals}f} ({sign}{pct:.2f}%)</div>
    </div>'''


def _last_update(data_date):
    """마지막 수집 성공일(collector 가 남긴 스탬프). 없으면 데이터 날짜로 대체.

    금투협은 T 자료를 T+1 에 공표해 데이터 날짜가 항상 하루 뒤처진다. 요약 카드는
    "언제 갱신됐는지"를 보여주는 게 오해가 적어 이 값을 쓴다. 수집이 며칠 멈추면
    스탬프도 함께 멈추므로 낡은 상태가 그대로 드러난다.
    """
    d = data_date.strftime("%Y-%m-%d")
    try:
        with open(os.path.join(DATA, "last_update.txt"), encoding="utf-8") as f:
            stamp = f.read().strip()[:10]
        if len(stamp) == 10:
            return max(d, stamp)
    except OSError:
        pass
    return d


def latest_stats():
    """요약 카드용 최신 수치. (날짜, 신용융자, 예탁금, 반대매매비중, 각 전일대비)"""
    cb, mf = load()
    JO = 1_000_000
    ca = (_num(cb["신용거래융자_전체"]) / JO).tolist()
    dp = (_num(mf["투자자예탁금 (장내파생상품 거래예수금제외)"]) / JO).tolist()
    rt = _num(mf["미수금 대비 반대매매비중(%)"]).tolist()
    return {
        "date": cb["date"].iloc[-1].strftime("%Y-%m-%d"),
        "updated": _last_update(cb["date"].iloc[-1]),
        "credit": ca[-1], "credit_d": ca[-1] - ca[-2],
        "deposit": dp[-1], "deposit_d": dp[-1] - dp[-2],
        "ratio": rt[-1], "ratio_d": rt[-1] - rt[-2],
    }


def build(out=None, nav_active=None):
    """out: 출력 경로(기본 leverage.html). nav_active 를 주면 사이트 하단 내비를 붙인다."""
    cb, mf = load()
    dates = [d.strftime("%Y-%m-%d") for d in cb["date"]]
    JO = 1_000_000  # 백만원 → 조원

    credit_ks = (_num(cb["신용거래융자_유가증권"]) / JO).round(3).tolist()
    credit_kq = (_num(cb["신용거래융자_코스닥"]) / JO).round(3).tolist()
    credit_all = (_num(cb["신용거래융자_전체"]) / JO).round(3).tolist()
    deposit = (_num(mf["투자자예탁금 (장내파생상품 거래예수금제외)"]) / JO).round(3).tolist()
    ratio = _num(mf["미수금 대비 반대매매비중(%)"]).round(2).tolist()

    tiles = (kpi("신용거래융자 잔고", credit_all[-1], credit_all[-2], "조원", 2)
             + kpi("투자자예탁금", deposit[-1], deposit[-2], "조원", 2)
             + kpi("미수금 대비 반대매매비중", ratio[-1], ratio[-2], "%", 2))

    charts = (
        '<section><h2>신용거래융자 잔고</h2>'
        '<p class="sub">빚내서 산 주식 규모. 시장 별로 나눠 본다. 단위: 조원</p>'
        '<div class="legend"><span class="lg"><i style="--cl:%s;--cd:%s"></i>유가증권</span>'
        '<span class="lg"><i style="--cl:%s;--cd:%s"></i>코스닥</span></div>' % (S1L, S1D, S2L, S2D)
        + line_chart("c1", dates, [("유가증권", credit_ks, S1L, S1D),
                                   ("코스닥", credit_kq, S2L, S2D)], "조원", 2) + '</section>'
        + '<section><h2>투자자예탁금</h2>'
          '<p class="sub">증시 대기 자금. 단위: 조원</p>'
        + line_chart("c2", dates, [("예탁금", deposit, S1L, S1D)], "조원", 2) + '</section>'
        + '<section><h2>미수금 대비 반대매매 비중</h2>'
          '<p class="sub">값이 튀면 강제 청산 압력이 커졌다는 신호. 단위: %</p>'
        + line_chart("c3", dates, [("반대매매비중", ratio, S1L, S1D)], "%", 2) + '</section>'
    )

    rows = "".join(
        f"<tr><td>{dates[i]}</td><td class='n'>{credit_all[i]:,.2f}</td>"
        f"<td class='n'>{credit_ks[i]:,.2f}</td><td class='n'>{credit_kq[i]:,.2f}</td>"
        f"<td class='n'>{deposit[i]:,.2f}</td><td class='n'>{ratio[i]:,.2f}</td></tr>"
        for i in range(len(dates) - 1, -1, -1))

    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>시장 레버리지 추이</title>
<style>
  :root {{ color-scheme: light;
    --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --line:#e6e5df; --up:#c0392b; --down:#1f6fb2; --mode:0; }}
  @media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) {{ color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --line:#2c2c2a; --up:#ff6b6b; --down:#4dabf7; --mode:1; }} }}
  :root[data-theme="dark"] {{ color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --line:#2c2c2a; --up:#ff6b6b; --down:#4dabf7; --mode:1; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--page); color:var(--ink); line-height:1.55;
    font-family:system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:30px 20px 60px; }}
  h1 {{ font-size:26px; margin:4px 0 4px; letter-spacing:-.01em; }}
  .eyebrow {{ color:var(--muted); font-size:13px; font-weight:600; }}
  .gen {{ color:var(--muted); font-size:13px; margin-bottom:22px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:26px; }}
  @media (max-width:680px) {{ .tiles {{ grid-template-columns:1fr; }} }}
  .tile {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .tile .k {{ color:var(--ink2); font-size:13px; }}
  .tile .v {{ font-size:26px; font-weight:700; margin-top:2px; }}
  .tile .v .u {{ font-size:13px; font-weight:500; color:var(--muted); margin-left:3px; }}
  .tile .d {{ font-size:13px; font-variant-numeric:tabular-nums; margin-top:2px; }}
  .d.up {{ color:var(--up); }} .d.down {{ color:var(--down); }} .d.flat {{ color:var(--muted); }}
  section {{ background:var(--surface); border:1px solid var(--line); border-radius:12px;
    padding:18px 20px 12px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin:0 0 2px; }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 6px; }}
  .legend {{ display:flex; gap:14px; margin:6px 0 2px; }}
  .lg {{ display:flex; align-items:center; gap:6px; font-size:13px; color:var(--ink2); }}
  .lg i {{ width:14px; height:3px; border-radius:2px; background:var(--cl); }}
  .chartbox {{ position:relative; }}
  .chart {{ width:100%; height:auto; display:block; overflow:visible; }}
  .grid {{ stroke:var(--grid); stroke-width:1; }}
  .axis {{ stroke:var(--axis); stroke-width:1; }}
  .ax {{ fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }}
  .ax-y {{ text-anchor:end; }} .ax-x {{ text-anchor:middle; }}
  /* 계열 색: 라이트는 --cl, 다크는 --cd (요소마다 인라인으로 두 값을 준다) */
  .ln {{ fill:none; stroke:var(--cl); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }}
  .dot {{ fill:var(--cl); stroke:var(--surface); stroke-width:2; }}
  @media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) .ln {{ stroke:var(--cd); }}
    :root:where(:not([data-theme="light"])) .dot {{ fill:var(--cd); }}
    :root:where(:not([data-theme="light"])) .lg i {{ background:var(--cd); }} }}
  :root[data-theme="dark"] .ln {{ stroke:var(--cd); }}
  :root[data-theme="dark"] .dot {{ fill:var(--cd); }}
  :root[data-theme="dark"] .lg i {{ background:var(--cd); }}
  .dlabel {{ fill:var(--ink2); font-size:12px; }}
  .cross {{ stroke:var(--axis); stroke-width:1; stroke-dasharray:3 3; }}
  .tip {{ position:absolute; pointer-events:none; display:none; background:var(--surface);
    border:1px solid var(--line); border-radius:8px; padding:7px 10px; font-size:12.5px;
    box-shadow:0 3px 12px rgba(0,0,0,.14); white-space:nowrap; z-index:5; }}
  .tip b {{ font-variant-numeric:tabular-nums; }}
  details {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px 18px; }}
  summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:10px; }}
  th, td {{ padding:7px 8px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
  th {{ color:var(--muted); font-size:12px; font-weight:600; }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .tablewrap {{ max-height:420px; overflow:auto; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:18px; }}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">금융투자협회 FreeSIS · 자동 수집</div>
  <h1>시장 레버리지 추이</h1>
  <div class="gen">{dates[0]} ~ {dates[-1]} · 총 {len(dates)}거래일 · 생성 {gen}</div>
  <div class="tiles">{tiles}</div>
  {charts}
  <details><summary>원자료 표 보기</summary><div class="tablewrap"><table>
    <thead><tr><th>일자</th><th class="n">신용융자 전체</th><th class="n">유가증권</th>
    <th class="n">코스닥</th><th class="n">투자자예탁금</th><th class="n">반대매매비중</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    <p class="sub" style="margin-top:8px">금액 단위: 조원 · 비중 단위: %</p>
  </details>
  <footer>본 페이지는 자동 수집·생성된 참고 자료입니다. 투자 판단과 그 결과에 대한 책임은 이용자 본인에게 있습니다.</footer>
</div>
<script>
(function () {{
  Object.keys(window.__CH || {{}}).forEach(function (cid) {{
    var cfg = window.__CH[cid], svg = document.getElementById(cid);
    var hit = document.getElementById(cid + '-hit'), cross = document.getElementById(cid + '-cross');
    var tip = document.getElementById(cid + '-tip'), g = cfg.geom;
    var n = cfg.dates.length;
    function idxAt(px) {{
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      var x = (px - r.left) / r.width * vb.width;
      var i = Math.round((x - g.ML) / g.pw * (n - 1));
      return Math.max(0, Math.min(n - 1, i));
    }}
    hit.addEventListener('mousemove', function (e) {{
      var i = idxAt(e.clientX), X = g.ML + g.pw * i / (n - 1);
      cross.setAttribute('x1', X); cross.setAttribute('x2', X); cross.style.display = '';
      var rows = cfg.series.map(function (s) {{
        return s.name + ' <b>' + s.ys[i].toLocaleString('ko-KR', {{minimumFractionDigits: cfg.dec, maximumFractionDigits: cfg.dec}}) + '</b> ' + cfg.unit;
      }}).join('<br>');
      tip.innerHTML = '<div style="color:var(--muted)">' + cfg.dates[i] + '</div>' + rows;
      tip.style.display = 'block';
      var box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      var left = X / vb.width * box.width;
      tip.style.left = Math.min(Math.max(left + 12, 0), box.width - tip.offsetWidth - 4) + 'px';
      tip.style.top = '8px';
    }});
    hit.addEventListener('mouseleave', function () {{
      cross.style.display = 'none'; tip.style.display = 'none';
    }});
  }});
}})();
</script>
</body></html>'''

    # 사이트에 편입할 때는 하단 내비를 붙여 다른 탭으로 돌아갈 수 있게 한다
    if nav_active is not None:
        import sys
        root = os.path.dirname(BASE)
        if root not in sys.path:
            sys.path.insert(0, root)
        import site_nav
        doc = doc.replace("</div>\n<script>",
                          f'</div>\n{site_nav.nav_html(nav_active)}\n<style>body{{padding-bottom:92px}}</style>'
                          f'{site_nav.NAV_CSS}\n<script>', 1)

    path = out or OUT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[레버리지] 차트 생성: {path} ({dates[0]} ~ {dates[-1]}, {len(dates)}일)")


if __name__ == "__main__":
    build()
