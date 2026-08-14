DATEBAR_CSS = """<style>
  .datepick { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
  .dp-label { color:var(--muted); font-size:13px; font-weight:600; }
  .dp-date { border:1px solid var(--line); background:var(--panel); color:var(--ink);
    border-radius:10px; padding:7px 10px; font:inherit; font-size:13.5px;
    font-variant-numeric:tabular-nums; color-scheme:inherit; }
  .dp-btn { border:1px solid var(--line); background:var(--panel); color:var(--muted);
    border-radius:10px; min-width:36px; height:34px; padding:0 12px; font-family:inherit;
    font-size:13px; font-weight:600; cursor:pointer;
    transition:color .15s,border-color .15s,opacity .15s; }
  .dp-btn:hover:not(:disabled) { color:var(--accent); border-color:var(--accent); }
  .dp-btn:disabled { opacity:.3; cursor:default; }
  #view > .day { animation:fadein .18s ease; }
  @keyframes fadein { from { opacity:0; } to { opacity:1; } }
</style>"""

# -*- coding: utf-8 -*-
"""
모든 페이지 공용 요소.

1) 하단 고정 내비게이션 바
   - nav_html(active): active 는 'report' | 'insight' | 'brief'.
   - NAV_CSS: 바 스타일.

2) 섹션 '허브' 페이지 (index.html / insights.html / briefs.html)
   - 상단 가로 스크롤 날짜 바 + 최신 본문을 바로 표시, 날짜 클릭 시 아래 본문 전환.
   - build_hub(...) 이 각 섹션의 날짜별 페이지(report_*/insights_*/brief_*.html)를
     읽어 본문(.wrap 안쪽)을 모아 하나의 허브 페이지로 만든다.

링크는 항상 존재하는 허브로 고정: 리포트 → index.html · 인사이트 → insights.html · 뉴스 → briefs.html
"""
import os
import re
import glob
import json

_ITEMS = [
    ("home",     "🏠", "홈",      "index.html"),      # 오늘의 요약 대시보드
    ("strategy", "📝", "투자전략", "strategy.html"),   # 블로그 일간 주도섹터 리포트
    ("stock",    "🔎", "종목탐색", "screener.html"),  # 자동 스크리너
    ("analysis", "📈", "분석",    "chart.html"),      # 주식 차트 · 계절성 (인사이트는 홈 카드로 접근)
    ("brief",    "📰", "뉴스",    "briefs.html"),
]


# 다크 ↔ 라이트 테마: 버튼은 홈에만(다른 페이지는 날짜 바 화살표와 겹침),
# 저장된 선택을 적용하는 스크립트는 모든 페이지에 포함해 테마가 유지되게 한다.
_THEME_BTN = '<button id="theme-toggle" aria-label="테마 전환" onclick="_tgTheme()">🌓</button>'

_THEME_SCRIPT = """<script>
function _tgCur(){
  var t = document.documentElement.dataset.theme;
  if(t) return t;
  return (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
}
function _tgSync(){
  var b = document.getElementById('theme-toggle');
  if(b) b.textContent = _tgCur() === 'dark' ? '\\u2600\\uFE0F' : '\\uD83C\\uDF19';
}
function _tgTheme(){
  var next = _tgCur() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('theme', next); } catch(e) {}
  _tgSync();
}
(function(){
  try {
    var t = localStorage.getItem('theme');
    if(t){ document.documentElement.dataset.theme = t; }
  } catch(e) {}
  _tgSync();
})();
</script>
<style>
  #theme-toggle { position:fixed; top:14px; right:14px; z-index:60; width:38px; height:38px;
    border-radius:50%; border:1px solid var(--line); background:var(--panel); font-size:16px;
    line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center;
    box-shadow:0 2px 8px rgba(0,0,0,.12); transition:border-color .15s; padding:0; }
  #theme-toggle:hover { border-color:var(--accent); }
</style>"""


# 자동 갱신: 주기적으로 현재 페이지가 바뀌었는지만 확인(HEAD)하고, 바뀐 경우에만
# 새로고침한다. 내용이 그대로면 아무 일도 일어나지 않아 화면이 튀지 않는다.
# 모바일은 백그라운드 타이머를 늦추므로 탭 복귀 시에도 즉시 확인한다.
_AUTOREFRESH_SCRIPT = """<script>
(function(){
  if(!/^https?:$/.test(location.protocol)) return;   // file:// 등에서는 동작 안 함
  var POLL = 5 * 60 * 1000;                          // 5분마다 확인
  var tag = null;
  function stamp(res){
    return res.headers.get('etag') || res.headers.get('last-modified')
        || res.headers.get('content-length');
  }
  function check(){
    fetch(location.pathname, { method:'HEAD', cache:'no-store' }).then(function(res){
      if(!res.ok) return;
      var s = stamp(res);
      if(!s) return;                  // 판별 정보가 없으면 새로고침하지 않는다
      if(tag === null){ tag = s; return; }   // 첫 확인은 기준값만 기록
      if(s !== tag){ location.reload(); }
    }).catch(function(){});
  }
  window.__checkUpdate = check;       // 콘솔에서 즉시 확인용
  check();
  setInterval(check, POLL);
  document.addEventListener('visibilitychange', function(){
    if(document.visibilityState === 'visible') check();
  });
})();
</script>"""


def nav_html(active):
    cells = "".join(
        f'<a class="nav-cell{" active" if key == active else ""}" href="{href}">'
        f'<span class="ni">{icon}</span><span class="nl">{label}</span></a>'
        for key, icon, label, href in _ITEMS
    )
    btn = _THEME_BTN if active == "home" else ""
    return f'<nav class="bottomnav">{cells}</nav>{btn}{_THEME_SCRIPT}{_AUTOREFRESH_SCRIPT}'


NAV_CSS = """<style>
  .bottomnav { position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex; justify-content:center; gap:2px;
    background:color-mix(in srgb,var(--panel) 92%,transparent); backdrop-filter:blur(10px);
    border-top:1px solid var(--line); padding:8px 8px calc(8px + env(safe-area-inset-bottom)); }
  .bottomnav .nav-cell { display:flex; flex-direction:column; align-items:center; gap:3px; text-decoration:none; color:var(--muted);
    padding:6px 4px; border-radius:12px; flex:1 1 0; max-width:96px; transition:color .15s,background .15s; }
  .bottomnav .nav-cell:hover { color:var(--accent); }
  .bottomnav .nav-cell.active { color:var(--accent); background:color-mix(in srgb,var(--accent) 12%,transparent); }
  .bottomnav .ni { font-size:20px; line-height:1; }
  .bottomnav .nl { font-size:11px; font-weight:600; }
  body { padding-bottom:92px; }
</style>"""


# ---- 허브(날짜 바 + 본문 전환) ----------------------------------------------

def _datebar(dates, active):
    """날짜 선택 컨트롤: 달력 입력 + 이전/다음 + 최신으로.

    예전에는 날짜 칩을 가로 스크롤로 늘어놓았는데, 이력이 쌓일수록 과거로
    가기 힘들다(2026-08 사용자 요청으로 한국 사이클 모델의 방식으로 통일).
    dates 는 YYYYMMDD 내림차순(최신 먼저). 본문 패널(#day-YYYYMMDD) 전환은
    HUB_JS 가 담당하고, 날짜가 바뀔 때 document 에 'hubdate' 이벤트를 쏜다.
    """
    def iso(y):
        return f"{y[:4]}-{y[4:6]}-{y[6:]}"
    return ('<div class="datepick">'
            '<span class="dp-label">과거 시점 보기</span>'
            f'<input type="date" class="dp-date" value="{iso(active)}" '
            f'min="{iso(dates[-1])}" max="{iso(dates[0])}">'
            '<button class="dp-btn dp-prev" aria-label="이전 날짜">&#9664;</button>'
            '<button class="dp-btn dp-next" aria-label="다음 날짜">&#9654;</button>'
            '<button class="dp-btn dp-latest">최신으로</button>'
            f'<script>window.__HUBDATES = {json.dumps(dates)};</script>'
            '</div>')


DATEBAR_CSS = """<style>
  .datewrap { display:flex; align-items:center; gap:6px; margin-bottom:6px; }
  .datewrap.noscroll .dnav { display:none; }
  .dnav { flex:0 0 auto; width:32px; height:32px; border-radius:50%; border:1px solid var(--line);
    background:var(--panel); color:var(--muted); font-size:18px; line-height:1; cursor:pointer;
    display:flex; align-items:center; justify-content:center; padding:0 0 2px; font-family:inherit;
    transition:color .15s,border-color .15s,opacity .15s; }
  .dnav:hover { color:var(--accent); border-color:var(--accent); }
  .dnav.edge { opacity:.25; pointer-events:none; }
  .datebar { flex:1 1 auto; display:flex; gap:8px; overflow-x:auto; padding:2px;
    scrollbar-width:none; -webkit-overflow-scrolling:touch; }
  .datebar::-webkit-scrollbar { display:none; }
  .datebar .chip { flex:0 0 auto; text-decoration:none; color:var(--muted);
    border:1px solid var(--line); background:var(--panel); border-radius:20px; padding:8px 14px;
    font-size:13px; font-weight:700; font-variant-numeric:tabular-nums; white-space:nowrap;
    transition:color .15s,background .15s,border-color .15s; }
  .datebar .chip .cy { opacity:.55; font-size:11px; font-weight:600; }
  .datebar .chip:hover { border-color:var(--accent); color:var(--accent); }
  .datebar .chip.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  .datebar .chip.active .cy { opacity:.8; }
  #view > .day { animation:fadein .18s ease; }
  @keyframes fadein { from { opacity:0; } to { opacity:1; } }
</style>"""

HUB_JS = """<script>
  // 날짜 선택(달력 + 이전/다음 + 최신으로). __HUBDATES 는 YYYYMMDD 내림차순.
  (function(){
    var DS = window.__HUBDATES || [];
    if(!DS.length) return;
    var cur = 0;   // DS 인덱스(0 = 최신)
    function iso(y){ return y.slice(0,4) + '-' + y.slice(4,6) + '-' + y.slice(6); }
    function show(i){
      cur = Math.max(0, Math.min(DS.length - 1, i));
      var ymd = DS[cur];
      document.querySelectorAll('#view > .day').forEach(function(d){
        d.style.display = (d.id === 'day-' + ymd) ? '' : 'none';
      });
      var inp = document.querySelector('.dp-date');
      if(inp) inp.value = iso(ymd);
      var q = function(s){ return document.querySelector(s); };
      if(q('.dp-prev')) q('.dp-prev').disabled = (cur === DS.length - 1);
      if(q('.dp-next')) q('.dp-next').disabled = (cur === 0);
      if(q('.dp-latest')) q('.dp-latest').disabled = (cur === 0);
      history.replaceState(null, '', '#' + ymd);
      document.dispatchEvent(new CustomEvent('hubdate', { detail:{ ymd: ymd } }));
    }
    document.addEventListener('DOMContentLoaded', function(){
      var inp = document.querySelector('.dp-date');
      if(inp) inp.addEventListener('change', function(){
        var v = inp.value; if(!v) return;
        var ymd = v.replace(/-/g, '');
        var i = DS.indexOf(ymd);
        if(i === -1){   // 발행일이 아니면 그 이전 가장 가까운 날짜로
          for(var k = 0; k < DS.length; k++){ if(DS[k] <= ymd){ i = k; break; } }
          if(i === -1) i = DS.length - 1;
        }
        show(i);
      });
      var on = function(sel, fn){ var b = document.querySelector(sel); if(b) b.addEventListener('click', fn); };
      on('.dp-prev', function(){ show(cur + 1); });
      on('.dp-next', function(){ show(cur - 1); });
      on('.dp-latest', function(){ show(0); });
      var h = location.hash.slice(1);
      if(h && DS.indexOf(h) !== -1){ show(DS.indexOf(h)); } else { show(0); }
    });
  })();
</script>"""


def extract_wrap_inner(html):
    """페이지 HTML에서 <div class="wrap"> 안쪽 내용만 추출(하단 nav 앞까지)."""
    key = '<div class="wrap">'
    s = html.find(key)
    if s == -1:
        return ""
    s += len(key)
    nav = html.find('<nav class="bottomnav"', s)
    seg = (html[s:nav] if nav != -1 else html[s:]).rstrip()
    if seg.endswith("</div>"):
        seg = seg[:-len("</div>")]
    return seg


def extract_style(html):
    """페이지의 본문 스타일 <style> 블록 추출.

    페이지에는 토글·내비 등 작은 style 블록이 여러 개 있으므로,
    가장 큰 블록(=본문 스타일)을 고른다. (첫 블록을 집으면 토글 CSS 만
    가져와 허브가 무스타일이 되는 버그가 있었음)
    """
    blocks = re.findall(r"<style>.*?</style>", html, re.S)
    return max(blocks, key=len) if blocks else ""


def build_hub(out_path, title, section, glob_name, id_regex, fallback_style=""):
    """날짜별 페이지들을 모아 허브(날짜 바 + 본문 전환) 페이지 생성."""
    d = os.path.dirname(out_path) or "."
    entries = []
    for f in glob.glob(os.path.join(d, glob_name)):
        m = re.search(id_regex, os.path.basename(f))
        if m:
            entries.append((m.group(1), f))
    entries.sort(key=lambda x: x[0], reverse=True)   # 최신 먼저
    if not entries:
        return

    style = fallback_style
    panels = []
    for i, (ymd, f) in enumerate(entries):
        with open(f, encoding="utf-8") as fh:
            html = fh.read()
        if i == 0 and not style:
            style = extract_style(html)
        hide = "" if i == 0 else ' style="display:none"'
        panels.append(f'<div class="day" id="day-{ymd}"{hide}>{extract_wrap_inner(html)}</div>')

    dates = [ymd for ymd, _ in entries]
    body = (f'<div class="wrap">{_datebar(dates, dates[0])}'
            f'<div id="view">{"".join(panels)}</div></div>\n{nav_html(section)}')
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title></head><body>{body}"
            f"{style}{NAV_CSS}{DATEBAR_CSS}{HUB_JS}</body></html>")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(full)
    print(f"[허브] {os.path.basename(out_path)} 갱신 ({len(entries)}건, 최신 {dates[0]})")
