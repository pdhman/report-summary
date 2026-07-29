# -*- coding: utf-8 -*-
"""
X(트위터) 모니터링 리포트 게시기.

입력: x-monitor/reports/*.md   (X 리스트 수집·요약 일일 리포트)
      x-monitor/data/*.json    (수집 원문 — 피드 섹션 데이터)
      x-monitor/accounts.json  (모니터링 계정, grade 2=★★ / 1=★)
출력: reports/x_YYYYMMDD.html  (날짜별 리포트 페이지, 디자인 통일)
      reports/x.html           (허브: 상단 통계·필터·피드 + 하단 날짜별 리포트)

make_summary.card_x() 에서 호출된다. x-monitor 폴더가 없는 환경(예: 과거
체크아웃)에서는 조용히 건너뛰어 다른 카드에 영향을 주지 않는다.
"""
import os
import re
import json
import glob
import markdown as md
import site_nav
from make_brief import _SHARED_STYLE

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE, "x-monitor", "reports")
DATA_DIR = os.path.join(BASE, "x-monitor", "data")
OUT_DIR = os.path.join(BASE, "reports")
FEED_MAX = 500          # 허브에 임베드할 최근 포스트 수 상한


def _files():
    return sorted(f for f in glob.glob(os.path.join(SRC_DIR, "????-??-??.md")))


def _wrap(title, body):
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title></head><body>{body}"
            f"{site_nav.nav_html('x')}{_SHARED_STYLE}{site_nav.NAV_CSS}</body></html>")


# ---------------------------------------------------------------- 피드 데이터
def _feed_data():
    """accounts.json + data/*.json 병합 → 피드 섹션용 dict (없으면 None)."""
    acc_path = os.path.join(BASE, "x-monitor", "accounts.json")
    if not os.path.exists(acc_path):
        return None
    with open(acc_path, encoding="utf-8") as f:
        accounts = json.load(f)["accounts"]
    posts, seen = [], set()
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
        for p in day.get("posts", []):
            key = p.get("url") or (p.get("handle"), p.get("time"))
            if key in seen:
                continue
            seen.add(key)
            posts.append(p)
    posts.sort(key=lambda p: p.get("time", ""), reverse=True)
    return {"accounts": accounts, "posts": posts[:FEED_MAX]}


_FEED_CSS = """
<style>
  .xstats { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:16px 0 12px; }
  @media (max-width:560px) { .xstats { grid-template-columns:repeat(2,1fr); } }
  .xstat { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; }
  .xstat b { font-size:20px; display:block; }
  .xstat span { font-size:12px; color:var(--muted); }
  .xfilters { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 14px; align-items:center; }
  .xfilters select, .xfilters input { background:var(--panel); color:var(--ink);
    border:1px solid var(--line); border-radius:8px; padding:7px 10px; font-size:13px; }
  .xfilters input { flex:1 1 140px; min-width:120px; }
  .xgrade { border:1px solid var(--line); background:var(--panel); color:var(--ink);
    border-radius:999px; padding:6px 14px; font-size:13px; cursor:pointer; }
  .xgrade.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  .xfeed { max-height:600px; overflow-y:auto; display:flex; flex-direction:column; gap:10px;
    padding-right:2px; margin-bottom:8px; }
  .xcard { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:13px 15px; }
  .xrow1 { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
  .xavatar { width:32px; height:32px; border-radius:50%; background:var(--accent); color:#fff;
    display:flex; align-items:center; justify-content:center; font-weight:700; font-size:14px; flex:0 0 auto; }
  .xwho b { font-size:13.5px; }
  .xwho .xh { color:var(--muted); font-size:11.5px; }
  .xbadge { font-size:11px; font-weight:700; border-radius:6px; padding:2px 7px;
    background:color-mix(in srgb,var(--accent) 14%,transparent); color:var(--accent); }
  .xbadge.g2 { background:color-mix(in srgb,#f59f00 18%,transparent); color:#b45309; }
  :root[data-theme="dark"] .xbadge.g2 { color:#fbbf24; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .xbadge.g2 { color:#fbbf24; } }
  .xtime { margin-left:auto; color:var(--muted); font-size:12px; white-space:nowrap; }
  .xrepost { font-size:11.5px; color:var(--muted); margin-bottom:4px; }
  .xtext { font-size:13.5px; line-height:1.55; white-space:pre-wrap; word-break:break-word; }
  .xcats { margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .xcat { font-size:11px; background:color-mix(in srgb,var(--line) 55%,transparent);
    color:var(--muted); border-radius:6px; padding:2px 8px; }
  .xlink { font-size:12px; color:var(--accent); text-decoration:none; margin-left:auto; font-weight:600; }
  .xempty { text-align:center; color:var(--muted); padding:30px 0; font-size:13.5px; }
  .xsection { margin:26px 0 10px; font-size:17px; font-weight:700;
    padding-bottom:8px; border-bottom:1px solid var(--line); }
</style>
"""

_FEED_JS = """
<script>
(function () {
  var D = window.XMON_DATA || { accounts: [], posts: [] };
  var accByHandle = {};
  D.accounts.forEach(function (a) { accByHandle[a.handle.toLowerCase()] = a; });
  var state = { grade: 0, cat: '', acc: '', date: '', q: '' };

  function kstDate(iso) {
    if (!iso) return '';
    return new Date(new Date(iso).getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  }
  function kstTime(iso) {
    if (!iso) return '';
    var d = new Date(new Date(iso).getTime() + 9 * 3600 * 1000);
    return d.toISOString().slice(5, 16).replace('T', ' ');
  }
  function esc(s) {
    return (s || '').replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var cats = {};
  D.accounts.forEach(function (a) { (a.categories || []).forEach(function (c) { cats[c] = 1; }); });
  Object.keys(cats).sort().forEach(function (c) {
    document.getElementById('xfCat').insertAdjacentHTML('beforeend',
      '<option value="' + esc(c) + '">' + esc(c) + '</option>');
  });
  D.accounts.forEach(function (a) {
    document.getElementById('xfAcc').insertAdjacentHTML('beforeend',
      '<option value="' + esc(a.handle) + '">' + (a.grade === 2 ? '\\u2605\\u2605 ' : '') + esc(a.name) + '</option>');
  });
  var dates = {};
  D.posts.forEach(function (p) { dates[kstDate(p.time)] = 1; });
  Object.keys(dates).sort().reverse().forEach(function (d) {
    document.getElementById('xfDate').insertAdjacentHTML('beforeend',
      '<option value="' + d + '">' + d + '</option>');
  });

  function render() {
    var posts = D.posts.filter(function (p) {
      var a = accByHandle[(p.handle || '').toLowerCase()];
      if (state.grade && (!a || a.grade !== state.grade)) return false;
      if (state.cat && (!a || (a.categories || []).indexOf(state.cat) < 0)) return false;
      if (state.acc && (p.handle || '').toLowerCase() !== state.acc.toLowerCase()) return false;
      if (state.date && kstDate(p.time) !== state.date) return false;
      if (state.q) {
        var q = state.q.toLowerCase();
        var hay = ((p.text || '') + ' ' + (p.handle || '') + ' ' + (a ? a.name : '')).toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });

    var today = kstDate(new Date().toISOString());
    var todayCnt = D.posts.filter(function (p) { return kstDate(p.time) === today; }).length;
    document.getElementById('xstats').innerHTML =
      '<div class="xstat"><b>' + D.posts.length + '</b><span>누적 수집</span></div>' +
      '<div class="xstat"><b>' + todayCnt + '</b><span>오늘</span></div>' +
      '<div class="xstat"><b>' + posts.length + '</b><span>필터 결과</span></div>' +
      '<div class="xstat"><b>' + D.accounts.length + '</b><span>모니터링 계정</span></div>';

    var html = '';
    posts.forEach(function (p) {
      var a = accByHandle[(p.handle || '').toLowerCase()] || {};
      var name = a.name || p.handle;
      html += '<div class="xcard"><div class="xrow1">' +
        '<div class="xavatar">' + esc((name || '?').charAt(0).toUpperCase()) + '</div>' +
        '<div class="xwho"><b>' + esc(name) + '</b> ' +
          (a.grade === 2 ? '<span class="xbadge g2">\\u2605\\u2605</span>' :
           a.grade === 1 ? '<span class="xbadge">\\u2605</span>' : '') +
          '<div class="xh">@' + esc(p.handle) + '</div></div>' +
        '<span class="xtime">' + kstTime(p.time) + '</span></div>' +
        (p.repost_by ? '<div class="xrepost">\\u21bb ' + esc(p.repost_by) + '</div>' : '') +
        '<div class="xtext">' + esc(p.text) + '</div>' +
        '<div class="xcats">' +
          (a.categories || []).map(function (c) { return '<span class="xcat">' + esc(c) + '</span>'; }).join('') +
          (p.url ? '<a class="xlink" href="' + esc(p.url) + '" target="_blank" rel="noopener">원문 ↗</a>' : '') +
        '</div></div>';
    });
    document.getElementById('xfeed').innerHTML =
      html || '<div class="xempty">조건에 맞는 글이 없습니다.</div>';
  }

  document.querySelectorAll('.xgrade').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.xgrade').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      state.grade = +b.dataset.g;
      render();
    });
  });
  document.getElementById('xfCat').addEventListener('change', function (e) { state.cat = e.target.value; render(); });
  document.getElementById('xfAcc').addEventListener('change', function (e) { state.acc = e.target.value; render(); });
  document.getElementById('xfDate').addEventListener('change', function (e) { state.date = e.target.value; render(); });
  document.getElementById('xfSearch').addEventListener('input', function (e) { state.q = e.target.value.trim(); render(); });
  render();
})();
</script>
"""


def _feed_section(data):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""
  <div class="xstats" id="xstats"></div>
  <div class="xfilters">
    <button class="xgrade on" data-g="0">전체</button>
    <button class="xgrade" data-g="2">★★ 중요</button>
    <button class="xgrade" data-g="1">★ 참고</button>
    <select id="xfCat"><option value="">분야 전체</option></select>
    <select id="xfAcc"><option value="">계정 전체</option></select>
    <select id="xfDate"><option value="">날짜 전체</option></select>
    <input id="xfSearch" type="search" placeholder="검색어...">
  </div>
  <div class="xfeed" id="xfeed"></div>
  <script>window.XMON_DATA = {payload};</script>"""


# ---------------------------------------------------------------- 빌드
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

    _build_hub()
    print(f"[X모니터] 생성 완료: {len(files)}건")


def _build_hub():
    """허브(x.html): 상단 통계·필터·피드 + 하단 날짜별 리포트(날짜 바 전환)."""
    entries = []
    for f in glob.glob(os.path.join(OUT_DIR, "x_????????.html")):
        m = re.search(r"x_(\d{8})\.html$", os.path.basename(f))
        if m:
            entries.append((m.group(1), f))
    entries.sort(key=lambda x: x[0], reverse=True)
    if not entries:
        return

    panels = []
    for i, (ymd, f) in enumerate(entries):
        with open(f, encoding="utf-8") as fh:
            html = fh.read()
        hide = "" if i == 0 else ' style="display:none"'
        inner = site_nav.extract_wrap_inner(html)
        # 허브 자체 헤더와 중복되는 패널 내부 헤더(eyebrow·h1·날짜) 제거
        inner = re.sub(r"<header>.*?</header>\s*", "", inner, count=1, flags=re.S)
        panels.append(f'<div class="day" id="day-{ymd}"{hide}>{inner}</div>')
    dates = [ymd for ymd, _ in entries]

    data = _feed_data()
    feed = _feed_section(data) if data and data["posts"] else ""
    feed_assets = (_FEED_CSS + _FEED_JS) if feed else ""

    body = f"""<div class="wrap">
  {site_nav._datebar(dates, dates[0])}
  <header>
    <div class="eyebrow">데일리 · X 모니터링</div>
    <h1>X 모니터링</h1>
    <div class="date">최신 {dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]}</div>
  </header>{feed}
  <div class="xsection">일일 리포트</div>
  <div id="view">{"".join(panels)}</div>
</div>
{site_nav.nav_html("x")}"""

    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>X 모니터링</title></head><body>{body}"
            f"{_SHARED_STYLE}{feed_assets}{site_nav.NAV_CSS}{site_nav.DATEBAR_CSS}{site_nav.HUB_JS}</body></html>")
    with open(os.path.join(OUT_DIR, "x.html"), "w", encoding="utf-8") as fh:
        fh.write(full)
    print(f"[허브] x.html 갱신 ({len(entries)}건, 최신 {dates[0]}, 피드 {'포함' if feed else '없음'})")


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
