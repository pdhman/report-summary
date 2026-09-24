# -*- coding: utf-8 -*-
"""메모리 사이클 대시보드 — '현재 결론'(판정 표·항목별 해설·수급 지표) 렌더러.

표준 라이브러리만 쓴다(pandas·matplotlib 불필요). 그래서 두 곳에서 같이 쓴다.
  - cycle_model.py : PC 전체 빌드(차트 포함) — 이 모듈로 결론 부분을 그린다
  - update_view.py : 경량 갱신 — docs/memory.html 의 결론 구역만 다시 그린다.
                     입력 데이터가 없는 클라우드 세션·GitHub Actions 에서도 동작.

결론 구역은 페이지 안에 <!--cv:키--> … <!--/cv:키--> 로 표시해 두고(mark),
경량 갱신은 그 사이만 바꾼다(splice). 키: now·notes·hbm(탭 패널), asof(머리말 기준일).
"""
from __future__ import annotations

import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
VIEW_JSON = os.path.join(BASE, "data", "cycle_view_manual.json")

# 공개판(docs/memory.html)에서 지우는 내부 경로·작업 메모 문구
PUBLIC_REPLACE = (
    (" 새 집계는 data/revenue_consensus_manual.json 에 추가.", ""),
    ("(data/cycle_view_manual.json)", ""),
    ("요약(사용자 제공)", "요약"),
    ("data/asp_manual.json 에 기록하면 표시", "기록 대기"),
)
PANEL_KEYS = (("now", "verdict"), ("notes", "notes"), ("hbm", "gap"))


def apply_public(html):
    for a_, b_ in PUBLIC_REPLACE:
        html = html.replace(a_, b_)
    return html


def mark(key, html):
    return f"<!--cv:{key}-->{html}<!--/cv:{key}-->"


def splice(page, key, html):
    """page 의 <!--cv:key-->…<!--/cv:key--> 사이를 html 로 교체. 표시가 없으면 None."""
    pat = re.compile(r"(<!--cv:%s-->).*?(<!--/cv:%s-->)" % (key, key), re.S)
    if not pat.search(page):
        return None
    return pat.sub(lambda m: m.group(1) + html + m.group(2), page, count=1)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- 현재 결론(수동)
# 상태색은 판정 전용(시리즈 색과 분리) — 항상 점 + 글자 라벨을 같이 쓴다.
CV_COLOR = {"g": "#0ca30c", "y": "#fab219", "o": "#ec835a", "r": "#d03b3b"}
CV_NAME = {"g": "강세", "y": "중립", "o": "약세 조짐", "r": "경고"}


def load_cycle_view(path=None):
    """'현재 결론' 수동 기록(data/cycle_view_manual.json). 없으면 None."""
    path = path or VIEW_JSON
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cv_dots(spec):
    """'gg' → 녹색 점 2개, 'y/o' → 노랑 / 주황(범위)."""
    out = []
    for part in str(spec).split("/"):
        out.append("".join(
            f'<span class="dot" style="background:{CV_COLOR[c]}" title="{CV_NAME[c]}"></span>'
            for c in part if c in CV_COLOR))
    return '<span class="sep">/</span>'.join(out)


def render_cycle_view(cv):
    rows = "".join(
        f"<tr class='{'b' if r.get('bold') else ''}'><td>{esc(r['area'])}</td>"
        f"<td><span class='dots'>{_cv_dots(r['dots'])}</span>{esc(r.get('label', ''))}</td>"
        f"<td>{esc(r.get('direction', ''))}</td></tr>"
        for r in cv["rows"])
    def _note(r):
        # note 는 문장 리스트(한 줄씩) 또는 문자열. what = 그 영역이 무엇인지 한 줄 풀이.
        n = r["note"]
        body = ("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in n) + "</ul>"
                if isinstance(n, list) else f"<p>{esc(n)}</p>")
        what = f"<div class='cvw'>{esc(r['what'])}</div>" if r.get("what") else ""
        return (f"<div class='cvn'><div class='cvh'><span class='dots'>{_cv_dots(r['dots'])}</span>"
                f"<b>{esc(r['area'])}</b> <span class='mut'>{esc(r.get('label', ''))}</span></div>"
                f"{what}{body}</div>")
    notes = "".join(_note(r) for r in cv["rows"] if r.get("note"))

    gap_html = ""
    g = cv.get("hbm_gap")
    pw = cv.get("peak_warning") or {}
    items = pw.get("items", [])
    n_met = sum(1 for i in items if i.get("met"))
    pw_lv = "r" if n_met >= 3 else ("y" if n_met == 2 else "g")
    if g:
        gap_html = f"""
<h3>HBM — 수요와 공급, 어느 쪽이 더 빨리 느는가</h3>
<div class="cvgap">
  <div><span class="k">수요 증가</span><b>{esc(g['demand']['arrow'])}</b> {esc(g['demand']['text'])}</div>
  <div><span class="k">공급 증가</span><b>{esc(g['supply']['arrow'])}</b> {esc(g['supply']['text'])}</div>
  <div><span class="k">Gap</span><b>{esc(g['gap'])}</b></div>
  <div><span class="k">공급/수요 비율</span>{esc(g['coverage'])}</div>
  <div><span class="k">Peak Warning</span><span class="dots">{_cv_dots(pw_lv)}</span><b style="white-space:nowrap">{n_met} / {len(items)}</b>
    <span class="mut">{esc(pw.get('rule', ''))}</span></div>
</div>"""
    if items:
        met_y, met_n = "<b>✓ 충족</b>", "<span class='mut'>✕ 아님</span>"
        gap_html += ("<h3>HBM 정점(Peak) 경고 조건</h3>"
                     "<table class='cvpw'><tr><th>Peak 조건</th><th>현재</th><th>충족</th></tr>"
                     + "".join(
                         f"<tr><td>{'①②③④⑤⑥⑦⑧'[k]} {esc(i['cond'])}</td><td>{esc(i.get('now', ''))}</td>"
                         f"<td>{met_y if i.get('met') else met_n}</td></tr>"
                         for k, i in enumerate(items))
                     + "</table>")

    # 리드타임(주문→납품 대기 기간) — 기록을 쌓아 '길어짐/짧아짐' 방향까지 본다.
    # 계약 가격보다 먼저 움직이므로 수급이 조이는지 풀리는지를 빨리 보여준다.
    lt = cv.get("lead_times") or {}
    lt_items = [i for i in lt.get("items", []) if i.get("history")]
    if lt_items:
        lt_rows = ""
        for it in lt_items:
            h = sorted(it["history"], key=lambda x: x["date"])
            cur, prev = h[-1], (h[-2] if len(h) > 1 else None)
            norm = it.get("normal")
            ratio = f"{cur['weeks'] / norm:.1f}배" if norm else "—"
            if prev is None:
                lv, way = ("g" if norm and cur["weeks"] >= norm * 1.5 else "y"), "첫 기록"
                prev_txt = "<span class='mut'>—</span>"
            else:
                d = cur["weeks"] - prev["weeks"]
                lv, way = (("g", "▲ 길어짐 (부족 심화)") if d > 0 else
                           ("o", "▼ 짧아짐 (공급이 따라잡는 중)") if d < 0 else
                           ("g" if norm and cur["weeks"] >= norm * 1.5 else "y", "— 유지"))
                prev_txt = f"{prev['weeks']}주 <span class='mut'>({prev['date']})</span>"
            lt_rows += (f"<tr><td>{esc(it['item'])}</td>"
                        f"<td><b>{cur['weeks']}주</b> <span class='mut'>({cur['date']})</span></td>"
                        f"<td>{esc(str(norm) + '주') if norm else '—'}</td><td>{ratio}</td>"
                        f"<td>{prev_txt}</td>"
                        f"<td><span class='dots'>{_cv_dots(lv)}</span>{way}</td></tr>")
        gap_html += ("<h3>주문 대기 기간 (리드타임)</h3>"
                     "<table class='cvpw'><tr><th>품목</th><th>최신</th><th>평상시</th>"
                     "<th>평상시 대비</th><th>직전 기록</th><th>방향</th></tr>"
                     + lt_rows + "</table>")
        if lt.get("note"):
            gap_html += f"<p class='note'>{esc(lt['note'])}</p>"

    upd = "".join(
        f"<li><span class='dots'>{_cv_dots(u.get('level', 'y'))}</span>"
        f"<span class='mut'>{esc(u['date'])}</span> <b>{esc(u['title'])}</b><br>{esc(u['impact'])}</li>"
        for u in cv.get("updates", [])[:6])
    upd_html = f"<h3>최근 반영한 신호</h3><ul class='cvu'>{upd}</ul>" if upd else ""

    legend = " · ".join(
        f'<span class="dots"><span class="dot" style="background:{CV_COLOR[c]}"></span></span>{CV_NAME[c]}'
        for c in "gyor")
    asof = f"<span class='mut'>— {esc(cv.get('as_of', ''))} 기준</span>"
    src = (f"<p class='note'>출처: {esc(cv.get('source', ''))}. 수동 요약"
           "(data/cycle_view_manual.json) — 나침반·국면·백테스트 탭의 자동 산출과는 별개의 정성 판단이다.</p>")
    more = ("<p class='note'>각 판단의 근거는 <a href='#notes'>항목별 해설</a>, "
            "정점 경고 조건·리드타임은 <a href='#hbm'>수급 지표</a> 탭에서 본다.</p>")
    return {
        "verdict": f"""<section class="cv">
<h2>현재 결론 {asof}</h2>
<table class="cvt"><tr><th>영역</th><th>현재 판단</th><th>방향</th></tr>{rows}</table>
<p class="note">{legend} &nbsp;(점 2개 = 강도 높음)</p>
{more}
{upd_html}
{src}
</section>""",
        "notes": f"""<section class="cv">
<h2>항목별 해설 {asof}</h2>
<div class="cvnotes">{notes}</div>
{src}
</section>""",
        "gap": (f"""<section class="cv">
<h2>수급 지표 {asof}</h2>
{gap_html}
{src}
</section>""" if gap_html else ""),
    }


# ---------------------------------------------------------------- 입력 검사
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# note 등 화면에 보이는 문구에 들어가면 안 되는 것(내부 경로·작업 안내 노출 방지)
_LEAK = re.compile(r"(data/|\.json|history 에|_comment)")


def validate(cv):
    """형식 오류 목록을 돌려준다(빈 리스트 = 통과). 원격 갱신에서 깨진 페이지가
    올라가지 않도록 update_view.py 가 쓰기 전에 검사한다."""
    errs = []

    def need(obj, key, where):
        if not isinstance(obj, dict) or key not in obj or obj[key] in (None, ""):
            errs.append(f"{where}: '{key}' 없음")
            return False
        return True

    def text(v, where):
        vals = v if isinstance(v, list) else [v]
        for t in vals:
            if not isinstance(t, str):
                errs.append(f"{where}: 문자열이 아님")
            elif _LEAK.search(t):
                errs.append(f"{where}: 내부 경로·작업 안내 문구가 공개 화면에 노출됨 → '{_LEAK.search(t).group(0)}'")

    def dots(v, where):
        if not isinstance(v, str) or not v or any(c not in "gyor/" for c in v):
            errs.append(f"{where}: dots 는 g·y·o·r 과 / 만 가능 (받은 값 {v!r})")

    if need(cv, "as_of", "as_of") and not _DATE.match(str(cv["as_of"])):
        errs.append(f"as_of: YYYY-MM-DD 형식이어야 함 (받은 값 {cv['as_of']!r})")
    rows = cv.get("rows")
    if not isinstance(rows, list) or not rows:
        errs.append("rows: 비어 있음")
    else:
        for i, r in enumerate(rows):
            w = f"rows[{i}]({r.get('area', '?') if isinstance(r, dict) else '?'})"
            for k in ("area", "dots", "label", "direction"):
                need(r, k, w)
            if isinstance(r, dict):
                if "dots" in r:
                    dots(r["dots"], w)
                for k in ("area", "label", "direction", "what", "note"):
                    if k in r:
                        text(r[k], f"{w}.{k}")
    g = cv.get("hbm_gap")
    if g is not None:
        for k in ("demand", "supply"):
            if need(g, k, "hbm_gap"):
                need(g[k], "arrow", f"hbm_gap.{k}")
                need(g[k], "text", f"hbm_gap.{k}")
        for k in ("gap", "coverage"):
            if need(g, k, "hbm_gap"):
                text(g[k], f"hbm_gap.{k}")
    for i, it in enumerate((cv.get("peak_warning") or {}).get("items", [])):
        w = f"peak_warning.items[{i}]"
        need(it, "cond", w)
        need(it, "now", w)
        if not isinstance(it.get("met"), bool):
            errs.append(f"{w}: met 는 true/false")
    lt = cv.get("lead_times") or {}
    if "note" in lt:
        text(lt["note"], "lead_times.note")
    for i, it in enumerate(lt.get("items", [])):
        w = f"lead_times.items[{i}]"
        need(it, "item", w)
        for j, h in enumerate(it.get("history", [])):
            if not _DATE.match(str(h.get("date", ""))):
                errs.append(f"{w}.history[{j}]: date 는 YYYY-MM-DD")
            if not isinstance(h.get("weeks"), (int, float)):
                errs.append(f"{w}.history[{j}]: weeks 는 숫자")
    for i, u in enumerate(cv.get("updates", [])):
        w = f"updates[{i}]"
        for k in ("date", "title", "impact"):
            need(u, k, w)
        if "date" in u and not _DATE.match(str(u["date"])):
            errs.append(f"{w}: date 는 YYYY-MM-DD")
        if u.get("level", "y") not in ("g", "y", "o", "r"):
            errs.append(f"{w}: level 은 g·y·o·r 중 하나")
        for k in ("title", "impact"):
            if k in u:
                text(u[k], f"{w}.{k}")
    return errs
