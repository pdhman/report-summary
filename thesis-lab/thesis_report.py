# -*- coding: utf-8 -*-
"""
종목 한 장짜리 투자 논리(마크다운) 생성 — docs/thesis_data.js 기반.

  python thesis_report.py 000660              # 종목 코드
  python thesis_report.py SK하이닉스           # 종목명(부분 일치)
  python thesis_report.py --group 반도체와반도체장비   # 그룹 요약(인과사슬·증거·기업 비교)
  python thesis_report.py --top 20            # 자동점수 상위 20 종목 표
  ... -o out.md                               # 파일로 저장 (기본: 표준출력)

HTML 페이지(docs/thesis.html)의 '마크다운 복사'와 같은 내용을 CLI 로 뽑는다.
수동 항목(병목·고객사·반증·손익비)은 여기서 빈 칸으로 둔다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.normpath(os.path.join(BASE, "..", "docs", "thesis_data.js"))


def load():
    with open(DATA_JS, encoding="utf-8") as f:
        raw = f.read()
    return json.loads(raw.split("=", 1)[1].rstrip().rstrip(";"))


def nz(v, nd=1, unit=""):
    return "-" if v is None else f"{v:.{nd}f}{unit}"


def sg(v, nd=1, unit=""):
    return "-" if v is None else f"{v:+.{nd}f}{unit}"


def fi(v):
    return "-" if v is None else f"{round(v):,}"


def rev(v):
    if v is None:
        return "-"
    return "적자→흑자" if v >= 900 else "흑자→적자" if v <= -900 else sg(v, 1, "%")


def find_company(D, key):
    C = D["companies"]
    if key in C:
        return C[key]
    hits = [c for c in C.values() if c["name"] == key] or [c for c in C.values() if key in c["name"]]
    if not hits:
        sys.exit(f"종목을 찾을 수 없음: {key}")
    return sorted(hits, key=lambda c: -(c["mcap"] or 0))[0]


def group_of(D, c):
    for g in D["groups"]:
        if g["kind"] == "업종" and g["name"] == c.get("ind"):
            return g
    return {}


def company_md(D, c) -> str:
    g = group_of(D, c)
    C = D["companies"]
    sc = c["score"]
    ev_self = [e for e in g.get("evidence", []) if e["code"] == c["code"]][:3]
    if not ev_self:                      # 업종이 심화 그룹이 아니면 종목 자체 리포트로 대체
        ev_self = [{**r, "br": r["br"] or "-"} for r in (c.get("reports") or [])][:3]
    ev_peer = [e for e in g.get("evidence", []) if e["code"] != c["code"]][:4]
    top3 = ", ".join(f"{C[m['code']]['name']}({m['total']})" for m in g.get("members", [])[:3] if m["code"] in C)
    kw = ", ".join(k for k, _ in g.get("keywords", [])[:8]) or "-"
    lv = " · ".join(f"L{l} {n}건" for l, n in sorted(g.get("ev_levels", {}).items(), key=lambda x: -int(x[0]))) or "-"
    L = []
    L.append(f"# {c['name']} ({c['code']}) 투자 논리 — 기준 {D['asof']}\n")
    L.append(f"업종 {c.get('ind') or '-'} · {c.get('cls') or '-'} · 자동점수 **{sc['total']}/30** ({sc['grade']})"
             + (f" · 자료없음 {sc['na']}항목" if sc.get("na") else "")
             + (f" · 전회({c['prev_ymd'][4:6]}/{c['prev_ymd'][6:]}) {c['score_prev']} → {c['score_chg']:+d}" if c.get("score_chg") is not None else " · 점수 히스토리 첫 기록"))
    if c.get("hist"):
        L.append("점수 추이: " + " → ".join(f"{h[0][4:6]}/{h[0][6:]} {h[1]}" for h in c["hist"]))
    if c.get("item_chg"):
        L.append("변화 항목: " + " · ".join(f"{D['items'][i]['q']} {d:+d}" for i, d in c["item_chg"]))
    L.append("")
    L.append("## 1. What is happening?")
    L.append(f"종목 RS {nz(c['rs'],0)} (1M {nz(c['r1'],0)} / 3M {nz(c['r3'],0)}), 1개월 {sg(c['ret1m'],1,'%')} · 3개월 {sg(c['ret3m'],1,'%')}.  ")
    L.append(f"업종 RS {nz(g.get('rs'),0)} · 4주 {sg(g.get('rs_chg4w'))} · RS≥70 비중 {nz(g.get('breadth70'),0,'%')} → "
             f"{'산업 전체 현상' if (g.get('breadth70') or 0) >= 50 else '부분 확산' if (g.get('breadth70') or 0) >= 30 else '개별 종목 움직임에 가까움'}.  ")
    L.append(f"재료 키워드: {kw}\n")
    L.append("## 2. Why now?")
    L += [f"- {e['d']} [{e['br'] or '-'}] {e['t']}" + (f" (TP {fi(e['tp'])})" if e.get("tp") else "") for e in ev_self] or ["- 최근 60일 이 종목 리포트 없음"]
    L += [f"- 뉴스 {n['d'][4:6]}/{n['d'][6:8]} {n['t']}" for n in (c.get("news") or [])[:3]]
    L.append("")
    L.append("## 3. Structural or Cyclical?")
    L.append(f"(직접 판단) 컨센서스 영업이익 YoY(E) {sg(c['op_yoy_e'],0,'%')} · 최근 분기 영업이익 YoY {sg(c.get('op_yoy'),0,'%')} · YoY 가속 {sg(c.get('op_accel'),0,'pp')}\n")
    L.append("## 4. Bottleneck")
    L.append(f"(직접 작성) 힌트: 최근 분기 OPM {nz(c.get('opm_now'),1,'%')} (전년동기 {sg(c.get('opm_yoy_pp'),1,'pp')}, 전분기 {sg(c.get('opm_qoq_pp'),1,'pp')}) · 업종 OPM 확대 종목 비중 {nz(g.get('margin_up_share'),0,'%')}\n")
    L.append("## 5. Evidence")
    dl = [h for h in (c.get("dart") or []) if h["kind"] == "공급계약"]
    if dl:
        L.append(f"L5 수주·공급계약 공시 60일 {c.get('dart_contracts60')}건" + (f" (매출 대비 합계 {c['dart_ratio60']:.1f}%)" if c.get("dart_ratio60") is not None else "") + ":  ")
        for h in dl[:5]:
            L.append(f"- {h['d']} {'[정정] ' if h.get('corr') else ''}{h.get('content') or '-'}"
                     + (f" — {fi(h['amount'])}억" if h.get("amount") is not None else "")
                     + (f" (매출 대비 {h['ratio']:.1f}%)" if h.get("ratio") is not None else "")
                     + (f" · {h['cp']}" if h.get("cp") else "") + f" [공시]({h['url']})")
    else:
        L.append("- 이 종목 60일 수주·공급계약 공시 없음")
    L.append(f"증거 계층(업종 리포트 30일): {lv}" + (f" · 업종 공급계약 공시 {g.get('dart60')}건/{g.get('dart_codes60')}종목" if g.get("dart60") else "") + "  ")
    L += [f"- L{e['lv']} {e['d']} {e['name']} — {e['t']}" for e in ev_peer]
    L.append(f"- 업종 커버 종목 중 EPS(E) 상향 {nz(g.get('eps_up_share'),0,'%')} · 이 종목 목표가 상향 {c['rep_tp_up']}건\n")
    L.append("## 6. Earnings Transmission")
    L.append(f"- 최근 분기 매출 YoY {sg(c.get('rev_yoy'),0,'%')} → 영업이익 YoY {sg(c.get('op_yoy'),0,'%')} (증분마진 {nz(c.get('incr_margin'),0,'%')}, 영업레버리지 {nz(c.get('op_leverage'),1,'x')})")
    L.append(f"- 컨센서스 FY1: 매출 {sg(c['rev_yoy_e'],0,'%')} · 영업이익 {sg(c['op_yoy_e'],0,'%')} · EPS(E) {fi(c['eps_e'])}원 (FY0 {fi(c['eps0'])}원)")
    L.append(f"- 4주 추정치 변화: EPS {rev(c['eps_rev4w'])} · 영업이익 {rev(c['op_rev4w'])} · 매출 {rev(c['rev_rev4w'])}")
    L.append(f"- 다음 분기 컨센서스 영업이익 {fi(c.get('next_q_op'))}억 (YoY {sg(c.get('next_q_op_yoy'),0,'%')})\n")
    if c.get("q_periods"):
        L.append("| 분기 | " + " | ".join(c["q_periods"]) + " |")
        L.append("|---|" + "---|" * len(c["q_periods"]))
        L.append("| 매출액 | " + " | ".join(fi(v) for v in c["q_rev"]) + " |")
        L.append("| 영업이익 | " + " | ".join(fi(v) for v in c["q_op"]) + " |")
        L.append("| OPM | " + " | ".join(nz(v, 1, "%") for v in c["q_opm"]) + " |\n")
    L.append("## 7. Why this company?")
    L.append(f"업종 내 매출 {c.get('rev_rank') or '-'}위 / {g.get('n_rev') or '-'} · {c.get('cls') or '-'} · 변동성60일 {nz(c['vol60'],0,'%')} · ROE(E) {nz(c['roe_e'],1,'%')} · 부채비율 {nz(c['debt'],0,'%')}  ")
    L.append(f"그룹 자동점수 상위: {top3 or '-'}\n")
    L.append("## 8. Expectations")
    L.append(f"PER(E) {nz(c['per_e'],1,'x')} vs 업종 중앙값 {nz(g.get('per_e_med'),1,'x')} · PEG(E) {nz(c['peg_e'],2)} · PBR {nz(c['pbr'],2,'x')} · 컨센서스 FY1 영업이익 {fi(c['op_e'])}억\n")
    L.append("## 9. What is not priced in?")
    L.append(f"(직접 작성) EPS(E) 4주 {rev(c['eps_rev4w'])} vs 주가 1개월 {sg(c['ret1m'],1,'%')}\n")
    L.append("## 10. Next Catalyst")
    L.append(f"다음 실적 {c.get('next_earn_label') or '-'}" + (f" (D-{c['next_earn_days']})" if c.get("next_earn_days") is not None else "")
             + f" · 마지막 리포트 {str(c['rep_days_since']) + '일 전' if c.get('rep_days_since') is not None else '-'}\n")
    L.append("## 11. Thesis Break")
    L.append("자동: EPS(E) 하향 전환 · 업종 RS 50 하회 · OPM 전년동기 대비 축소 · 외국인+기관 순매도 지속 · MA200 하회  ")
    L.append("(직접 추가) 가격 하락 / 고객 재고 증가 / 리드타임 단축 / 경쟁 CAPEX 급증 / 신규 공급 조기 가동 / 주문 둔화\n")
    L.append("## 12. Risk / Reward")
    tps = ", ".join(f"{fi(r['tp'])}({r['br'] or '-'})" for r in (c.get("reports") or []) if r.get("tp"))[:120]
    L.append(f"(직접 작성) 현재가 {fi(c['price'])}원 · 52주 고점 대비 {sg(c['off_high'],1,'%')} · 리포트 목표가 {tps or '-'}\n")
    L.append("## 체크리스트 (자동 15항목)")
    L.append("| 구분 | 항목 | 근거 | 점수 |\n|---|---|---|---|")
    for it, r in zip(D["items"], sc["items"]):
        L.append(f"| {it['cat']} | {it['q']} | {r[1]} | {'-' if r[0] is None else r[0]} |")
    for m in D["manual_items"]:
        L.append(f"| {m['cat']} | {m['q']} | 수동 | |")
    L.append(f"| | **합계** | | **{sc['total']}/30** |")
    return "\n".join(L)


def group_md(D, name) -> str:
    C = D["companies"]
    g = next((x for x in D["groups"] if x["name"] == name), None)
    if not g:
        cands = [x["name"] for x in D["groups"] if name in x["name"]]
        if len(cands) == 1:
            g = next(x for x in D["groups"] if x["name"] == cands[0])
        else:
            sys.exit(f"그룹을 찾을 수 없음: {name}" + (f" 후보: {cands[:10]}" if cands else ""))
    L = [f"# {g['kind']} · {g['name']} — 기준 {D['asof']}\n",
         f"RS {nz(g['rs'],0)} (1M {nz(g['r1'],0)} / 3M {nz(g['r3'],0)}) · 4주 {sg(g['rs_chg4w'])} · 확산 {nz(g['breadth70'],0,'%')} · "
         f"OP YoY(E) 중앙 {sg(g['op_yoy_e_med'],0,'%')} · EPS↑ {nz(g['eps_up_share'],0,'%')} · OPM↑ {nz(g['margin_up_share'],0,'%')} · PER(E) 중앙 {nz(g['per_e_med'],1,'x')}\n"]
    if g.get("chain"):
        L.append("## 인과 사슬 초안")
        L += [f"{i + 1}. {s}" for i, s in enumerate(g["chain"])]
        L.append("")
    if g.get("evidence"):
        L.append("## 증거 사다리 (리포트 30일)")
        for e in sorted(g["evidence"], key=lambda x: (-x["lv"], x["d"]), reverse=False)[:20]:
            L.append(f"- **L{e['lv']} {e['ln']}** {e['d']} {e['name']} [{e['br']}] {e['t']}" + (f" (TP {fi(e['tp'])})" if e.get("tp") else ""))
        L.append("")
    if g.get("narratives"):
        L.append("## 매크로·주도섹터 텍스트 언급")
        L += [f"- ({n['date']} {n['src']}) {n['text']}" for n in g["narratives"]]
        L.append("")
    L.append("## 기업 비교")
    L.append("| 종목 | 점수 | 구분 | 시총(억) | RS | 52주고점 | OPM(YoY pp) | 증분마진 | EPS(E) 4주 | PER(E) | 외인+기관 |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for m in g.get("members", []):
        c = C.get(m["code"])
        if not c:
            continue
        L.append(f"| {c['name']} | **{m['total']}** | {c.get('cls') or '-'} | {fi(c['mcap'])} | {nz(c['rs'],0)} | {sg(c['off_high'],0,'%')} | "
                 f"{nz(c.get('opm_now'),1,'%')} ({sg(c.get('opm_yoy_pp'),1)}) | {nz(c.get('incr_margin'),0,'%')} | {rev(c['eps_rev4w'])} | "
                 f"{nz(c['per_e'],1,'x')} | {c.get('fi_netbuy_days') if c.get('fi_days') else '-'}/{c.get('fi_days') or '-'} |")
    return "\n".join(L)


def top_md(D, n) -> str:
    rows = sorted((c for c in D["companies"].values() if c.get("deep")), key=lambda c: -c["score"]["total"])[:n]
    L = [f"# 자동점수 상위 {n} — 기준 {D['asof']}\n",
         "| # | 종목 | 업종 | 점수 | Δ전회 | 판정 | 구분 | RS | EPS(E) 4주 | OPM YoY | PER(E) |\n|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(rows, 1):
        dv = c.get("score_chg")
        L.append(f"| {i} | {c['name']} ({c['code']}) | {c.get('ind') or '-'} | **{c['score']['total']}** | {'-' if dv is None else f'{dv:+d}'} | {c['score']['grade']} | {c.get('cls') or '-'} | "
                 f"{nz(c['rs'],0)} | {rev(c['eps_rev4w'])} | {sg(c.get('opm_yoy_pp'),1,'pp')} | {nz(c['per_e'],1,'x')} |")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", help="종목 코드 또는 이름")
    ap.add_argument("--group", help="업종/테마 이름")
    ap.add_argument("--top", type=int, help="자동점수 상위 N")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    D = load()
    if a.group:
        md = group_md(D, a.group)
    elif a.top:
        md = top_md(D, a.top)
    elif a.key:
        md = company_md(D, find_company(D, a.key))
    else:
        ap.error("종목 코드/이름, --group, --top 중 하나가 필요합니다")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"저장: {a.out}")
    else:
        print(md)
