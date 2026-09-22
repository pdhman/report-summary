# -*- coding: utf-8 -*-
"""
'시장의 시선' — 시장 가격이 지금 어떤 변수에 가장 민감한지(웨이트 합 100·순위).

데이터: docs/data/gaze/YYYY-MM-DD.json  (매일 아침 Claude 예약 작업이 작성·push.
        절차와 형식은 market-gaze/README.md)
쓰는 곳: make_summary(홈 '오늘의 뉴스 · 시장의 시선' 카드 상단),
        make_brief(briefs.html '👁 시장의 시선' 탭), telegram/send_gaze.py(발송).

뉴스 브리핑(briefs/*.md, 수동 게시)과 만드는 주체·시각이 달라 데이터는 따로 두고
화면에서만 합친다 — 한쪽이 늦거나 빠져도 다른 쪽은 그대로 나온다. 홈 카드는 둘을
합쳐 보여주고, 눌러서 들어간 briefs.html 안에서 탭으로 갈린다(2026-09-22).
"""
import os
import glob
import json
import html as _html

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "docs", "data", "gaze")

DIR_ICON = {"우호": "🟢", "중립": "⚪", "비우호": "🔴"}


def esc(s):
    return _html.escape(str(s), quote=False)


def load_all():
    """{날짜: 데이터} (날짜 오름차순). 깨진 파일은 건너뛴다."""
    out = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "20??-??-??.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                out[os.path.basename(f)[:10]] = json.load(fh)
        except Exception as e:
            print(f"[시선] 읽기 실패 — 건너뜀: {os.path.basename(f)} ({e})")
    return out


def with_prev(date=None):
    """(해당 날짜 데이터, 그 직전 데이터). date 생략 시 가장 최근. 없으면 (None, None)."""
    allg = load_all()
    days = list(allg)
    if date is None and days:
        date = days[-1]
    if date not in allg:
        return None, None
    i = days.index(date)
    return allg[date], (allg[days[i - 1]] if i > 0 else None)


def delta(item, prev):
    """전일 대비 (웨이트 변화, 순위 변화[+면 상승], 신규 여부). 전일 자료가 없으면 None."""
    if not prev:
        return None
    old = {i["key"]: i for i in prev.get("items", [])}.get(item["key"])
    if not old:
        return (0, 0, True)
    return (item["weight"] - old["weight"], old["rank"] - item["rank"], False)


def _delta_html(item, prev):
    d = delta(item, prev)
    if d is None:
        return ""
    dw, dr, new = d
    if new:
        return ' <span class="gz-d up">NEW</span>'
    if not dw:
        return ' <span class="gz-d">=</span>'
    return f' <span class="gz-d {"up" if dw > 0 else "down"}">{dw:+d}</span>'


def card_rows(cur, prev, top=3):
    """홈 카드용: 상위 N개 변수 한 줄씩."""
    rows = ""
    for it in sorted(cur["items"], key=lambda i: i["rank"])[:top]:
        icon = DIR_ICON.get(it.get("direction", ""), "")
        rows += (f'<div class="krow"><span class="k-name gz-name">{it["rank"]}. {esc(it["name"])}</span>'
                 f'<span class="k-val">{it["weight"]}{_delta_html(it, prev)} {icon}</span></div>')
    return rows


def block_html(cur, prev):
    """뉴스 브리핑 페이지 최상단 블록: 전체 순위·근거·트리거·체크 지표·관찰 목록."""
    items = ""
    for it in sorted(cur["items"], key=lambda i: i["rank"]):
        icon = DIR_ICON.get(it.get("direction", ""), "")
        why = f'<div class="gz-why">{esc(it["why"])}</div>' if it.get("why") else ""
        trg = (f'<div class="gz-trg"><b>다음 트리거</b> {esc(it["trigger"])}</div>'
               if it.get("trigger") else "")
        items += (f'<div class="gz-item"><div class="gz-row">'
                  f'<span class="gz-rank">{it["rank"]}</span>'
                  f'<span class="gz-nm">{esc(it["name"])}</span>'
                  f'<span class="gz-dir">{icon} {esc(it.get("direction", ""))}</span>'
                  f'<span class="gz-w">{it["weight"]}{_delta_html(it, prev)}</span></div>'
                  f'<div class="gz-bar"><i style="width:{max(0, min(100, it["weight"]))}%"></i></div>'
                  f'{why}{trg}</div>')
    extra = ""
    if cur.get("indicators"):
        chips = "".join(
            f'<span class="gz-chip">{esc(i["name"])} <b>{esc(i["value"])}</b>'
            + (f' <em>기준 {esc(i["line"])}</em>' if i.get("line") else "") + "</span>"
            for i in cur["indicators"])
        extra += f'<div class="gz-sub">체크 지표</div><div class="gz-chips">{chips}</div>'
    if cur.get("watch"):
        lis = "".join(f"<li>{esc(w)}</li>" for w in cur["watch"])
        extra += f'<div class="gz-sub">관찰 목록</div><ul class="gz-watch">{lis}</ul>'
    mix = cur.get("source_mix") or {"base": 80, "x": 20}
    head = f'<p class="gz-head">{esc(cur["headline"])}</p>' if cur.get("headline") else ""
    return (f'<section class="gaze"><div class="gz-title">👁 시장의 시선'
            f'<span class="gz-date">{esc(cur["date"])} 기준</span></div>{head}{items}{extra}'
            f'<p class="gz-foot">시장 가격이 지금 어떤 변수에 가장 민감한지 매긴 웨이트(합 100)입니다. '
            f'뉴스·가격 반응·예측시장 {mix["base"]}% + X 모니터링 {mix["x"]}% · AI 판단 추정치이며 '
            f'투자 권유가 아닙니다.</p></section>')


# 홈·뉴스 페이지 공용 (사이트 변수 --panel/--line/--accent/--muted/--up 사용)
GAZE_CSS = """<style>
  .gz-d { font-size:12px; font-weight:600; color:var(--muted); font-variant-numeric:tabular-nums; }
  .gz-d.up { color:var(--up); } .gz-d.down { color:var(--accent); }
  .gz-name { color:var(--ink) !important; flex:1 1 auto !important; }
  .gz-cardhead { font-size:12px; font-weight:700; color:var(--accent); margin-bottom:2px;
    display:flex; justify-content:space-between; }
  .gz-cardhead span { color:var(--muted); font-weight:500; font-variant-numeric:tabular-nums; }
  .gz-sep { border-top:1px solid var(--line); margin:9px 0 9px; }
  .gaze { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:18px 22px 14px; margin-bottom:16px; }
  .gz-title { font-weight:700; font-size:17px; display:flex; align-items:baseline; gap:10px; }
  .gz-date { color:var(--muted); font-size:12px; font-weight:500; font-variant-numeric:tabular-nums; }
  .gz-head { margin:8px 0 12px; font-size:14px; }
  .gz-item { padding:10px 0; border-top:1px dashed var(--line); }
  .gz-row { display:flex; align-items:baseline; gap:8px; font-size:14.5px; }
  .gz-rank { flex:0 0 18px; font-weight:700; color:var(--accent); }
  .gz-nm { flex:1 1 auto; font-weight:700; }
  .gz-dir { flex:0 0 auto; font-size:12.5px; color:var(--muted); }
  .gz-w { flex:0 0 auto; font-weight:700; font-variant-numeric:tabular-nums; min-width:56px; text-align:right; }
  .gz-bar { height:5px; border-radius:3px; margin:6px 0 6px 26px;
    background:color-mix(in srgb,var(--line) 70%,transparent); overflow:hidden; }
  .gz-bar i { display:block; height:100%; background:var(--accent); border-radius:3px; }
  .gz-why, .gz-trg { margin-left:26px; font-size:13px; line-height:1.55; }
  .gz-trg { color:var(--muted); margin-top:2px; }
  .gz-trg b { font-weight:600; margin-right:4px; }
  .gz-sub { margin:12px 0 6px; font-size:12.5px; font-weight:700; color:var(--accent);
    border-top:1px dashed var(--line); padding-top:10px; }
  .gz-chips { display:flex; flex-wrap:wrap; gap:6px; }
  .gz-chip { font-size:12.5px; border:1px solid var(--line); border-radius:999px; padding:3px 10px; }
  .gz-chip em { font-style:normal; color:var(--muted); font-size:11.5px; }
  .gz-watch { margin:0; padding-left:18px; font-size:13px; }
  .gz-foot { margin:12px 0 0; color:var(--muted); font-size:11.5px; }
</style>"""
