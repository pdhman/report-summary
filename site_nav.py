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

링크는 항상 존재하는 허브로 고정: 리포트 → index.html · 인사이트 → insights.html · 시황 → briefs.html
"""
import os
import re
import glob

_ITEMS = [
    ("strategy", "📝", "투자전략", "index.html"),     # 블로그 일간 주도섹터 리포트
    ("stock",    "🔎", "종목분석", "screener.html"),  # 자동 스크리너
    ("insight",  "📊", "인사이트", "insights.html"),
    ("brief",    "📰", "시황",    "briefs.html"),
]


def nav_html(active):
    cells = "".join(
        f'<a class="nav-cell{" active" if key == active else ""}" href="{href}">'
        f'<span class="ni">{icon}</span><span class="nl">{label}</span></a>'
        for key, icon, label, href in _ITEMS
    )
    return f'<nav class="bottomnav">{cells}</nav>'


NAV_CSS = """<style>
  .bottomnav { position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex; justify-content:center; gap:6px;
    background:color-mix(in srgb,var(--panel) 92%,transparent); backdrop-filter:blur(10px);
    border-top:1px solid var(--line); padding:8px 10px calc(8px + env(safe-area-inset-bottom)); }
  .bottomnav .nav-cell { display:flex; flex-direction:column; align-items:center; gap:3px; text-decoration:none; color:var(--muted);
    padding:6px 16px; border-radius:12px; min-width:70px; transition:color .15s,background .15s; }
  .bottomnav .nav-cell:hover { color:var(--accent); }
  .bottomnav .nav-cell.active { color:var(--accent); background:color-mix(in srgb,var(--accent) 12%,transparent); }
  .bottomnav .ni { font-size:20px; line-height:1; }
  .bottomnav .nl { font-size:11px; font-weight:600; }
  body { padding-bottom:92px; }
</style>"""


# ---- 허브(날짜 바 + 본문 전환) ----------------------------------------------

def _datebar(dates, active):
    chips = []
    for ymd in dates:
        cls = "chip active" if ymd == active else "chip"
        chips.append(
            f'<a class="{cls}" data-ymd="{ymd}" href="#{ymd}" onclick="return _showDay(this)">'
            f'<span class="cy">{ymd[2:4]}.</span>{ymd[4:6]}·{ymd[6:]}</a>'
        )
    return ('<div class="datewrap">'
            '<button class="dnav" data-dir="-1" aria-label="이전 날짜" onclick="_scrollDates(this)">&#8249;</button>'
            f'<div class="datebar">{"".join(chips)}</div>'
            '<button class="dnav" data-dir="1" aria-label="다음 날짜" onclick="_scrollDates(this)">&#8250;</button>'
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
  function _showDay(el){
    document.querySelectorAll('.datebar .chip').forEach(function(c){ c.classList.remove('active'); });
    el.classList.add('active');
    var ymd = el.dataset.ymd;
    document.querySelectorAll('#view > .day').forEach(function(d){
      d.style.display = (d.id === 'day-' + ymd) ? '' : 'none';
    });
    history.replaceState(null, '', '#' + ymd);
    el.scrollIntoView({ block:'nearest', inline:'center' });
    return false;
  }
  function _scrollDates(btn){
    var bar = btn.parentElement.querySelector('.datebar');
    if(bar){ bar.scrollBy({ left:(+btn.dataset.dir) * Math.round(bar.clientWidth * 0.6), behavior:'smooth' }); }
  }
  function _updateDnav(){
    document.querySelectorAll('.datewrap').forEach(function(w){
      var bar = w.querySelector('.datebar'), btns = w.querySelectorAll('.dnav');
      if(!bar || btns.length < 2) return;
      var max = bar.scrollWidth - bar.clientWidth;
      w.classList.toggle('noscroll', max <= 2);
      btns[0].classList.toggle('edge', bar.scrollLeft <= 2);
      btns[1].classList.toggle('edge', bar.scrollLeft >= max - 2);
    });
  }
  document.addEventListener('DOMContentLoaded', function(){
    var h = location.hash.slice(1);
    if(h){ var el = document.querySelector('.datebar .chip[data-ymd="' + h + '"]'); if(el){ _showDay(el); } }
    document.querySelectorAll('.datebar').forEach(function(b){ b.addEventListener('scroll', _updateDnav, { passive:true }); });
    window.addEventListener('resize', _updateDnav);
    _updateDnav();
  });
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
    """페이지의 첫 <style> 블록(본문 스타일) 추출."""
    s = html.find("<style>")
    if s == -1:
        return ""
    e = html.find("</style>", s)
    return html[s:e + len("</style>")] if e != -1 else ""


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
