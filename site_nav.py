# -*- coding: utf-8 -*-
"""
모든 페이지 하단에 고정으로 들어가는 공용 내비게이션 바.

- nav_html(active): 하단 고정 바 HTML. active 는 'report' | 'insight' | 'brief'.
- NAV_CSS: 바 스타일(<style> 블록). 각 페이지 <body> 끝에 nav_html 과 함께 붙인다.

링크는 항상 존재하는 '섹션 목록' 페이지로 고정한다(생성 순서에 따른 깨진 링크 방지):
  리포트 → index.html · 인사이트 → insights.html · 시황 → briefs.html
색상 변수(--panel/--line/--accent/--muted)는 각 페이지가 정의한 값을 사용한다.
"""

_ITEMS = [
    ("report",  "📄", "리포트",  "index.html"),
    ("insight", "📊", "인사이트", "insights.html"),
    ("brief",   "📰", "시황",    "briefs.html"),
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
