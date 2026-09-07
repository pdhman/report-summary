# -*- coding: utf-8 -*-
"""
thesis-lab 스코어링 — 9단계 투자 프레임워크의 자동 채점 15항목(0/1/2, 30점 만점).

  산업(3)  ① 수요 증가  ② 확산(지속성)  ③ RS 상승·가속
  병목(2)  ④ 종목 마진 확대(가격 전가 proxy)  ⑤ 업종 마진 확대 확인
  증거(2)  ⑥ 회사·애널리스트 확인(리포트)  ⑦ 피어 확인(업종 EPS 상향 비율)
  기업(3)  ⑧ 시장 지위(업종 내 매출 순위)  ⑨ EPS 레버리지  ⑩ 실적 가시성
  주가(2)  ⑪ 주가 위치  ⑫ 수급·거래
  실적(1)  ⑬ EPS 추정치 상향
  밸류(1)  ⑭ 밸류에이션(업종 대비 PER(E)·PEG)
  촉매(1)  ⑮ 다음 촉매 시점

수동 4항목(병목 구조·고객사 확인·Thesis Break·손익비)은 HTML 에서 입력한다.
해석: 25~30 매우 강함 · 21~24 적극 관심 · 17~20 Watchlist · 13~16 부족 · ≤12 보류
"""
from __future__ import annotations

import math

ITEMS = [
    ("ind_demand", "산업", "산업 수요가 증가하고 있다", "업종 컨센서스 영업이익 YoY(E) 중앙값"),
    ("ind_breadth", "산업", "수요 증가가 일시적이지 않다 (확산)", "업종 내 RS≥70 종목 비중"),
    ("ind_rs", "산업", "산업 상대강도가 상승·가속 중이다", "업종 RS 수준 + 1M vs 3M + 4주 변화"),
    ("margin_up", "병목", "가격 상승이 마진으로 전가되고 있다", "최근 분기 OPM 전년동기 대비 변화(pp)"),
    ("ind_margin", "병목", "업종 전체 마진이 확대되고 있다", "업종 내 OPM 확대 종목 비중"),
    ("ev_reports", "증거", "회사·애널리스트·공시가 확인한다", "최근 30일 리포트 건수·목표가 상향 + 60일 수주·공급계약 공시(매출 대비 %)"),
    ("ev_peers", "증거", "경쟁사(피어)가 확인한다", "업종 내 EPS(E) 상향 종목 비중"),
    ("co_position", "기업", "산업 내 시장 지위가 높다", "업종 내 매출액 순위"),
    ("co_leverage", "기업", "EPS 레버리지가 크다", "증분 영업이익률(ΔOP/ΔRev)·영업레버리지"),
    ("co_visibility", "기업", "실적 가시성이 높다", "컨센서스 커버리지·ROE(E)·변동성"),
    ("px_position", "주가", "주가 위치가 유리하다", "52주 고점 대비·MA200 대비"),
    ("px_flow", "주가", "수급·거래량이 뒷받침한다", "외국인+기관 순매수 일수(20일)·거래량 비율"),
    ("eps_rev", "실적", "EPS 추정치가 올라가고 있다", "4주 EPS(E) 변화율"),
    ("valuation", "밸류", "밸류에이션 부담이 크지 않다", "PER(E) 업종 중앙값 대비·PEG(E)"),
    ("catalyst", "촉매", "1~3개월 내 촉매가 있다", "다음 실적 발표까지 일수·최근 리포트"),
]

MANUAL_ITEMS = [
    ("m_bottleneck", "병목", "신규 공급에 시간이 오래 걸린다 (공급 확대 난이도)"),
    ("m_customer", "증거", "고객사가 확인한다 (구매자 발언·계약)"),
    ("m_break", "리스크", "투자 논리를 깨뜨릴 조건이 명확하다"),
    ("m_rr", "리스크", "손익비가 최소 2:1 이상이다"),
]


def interpret(score: float | None) -> str:
    if score is None:
        return "-"
    if score >= 25:
        return "매우 강한 투자 후보"
    if score >= 21:
        return "적극적인 관심"
    if score >= 17:
        return "Watchlist"
    if score >= 13:
        return "논리가 아직 부족"
    return "투자 보류"


def _v(x):
    try:
        f = float(x)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fmt(x, unit="", nd=1, signed=False):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:+.{nd}f}{unit}" if signed else f"{x:.{nd}f}{unit}"
    return f"{x}{unit}"


def score_company(c: dict, g: dict) -> dict:
    """c: 종목 지표 dict, g: 소속 그룹(업종) 지표 dict → {'items': [...], 'total': int, 'n': int}

    items 는 ITEMS 순서의 [점수 0/1/2 또는 None(자료 없음), 근거 문자열] 배열.
    자료가 없는 항목은 0점으로 합산하되 'na' 로 표시해 수동 보정 여지를 남긴다.
    """
    out = []

    def add(key, pts, why):
        out.append([pts, why])            # ITEMS 순서와 동일 (용량 절감을 위해 배열)

    # ① 산업 수요
    v = _v(g.get("op_yoy_e_med"))
    if v is None:
        add("ind_demand", None, "업종 컨센서스 없음")
    else:
        add("ind_demand", 2 if v >= 20 else 1 if v >= 0 else 0,
            f"업종 영업이익 YoY(E) 중앙값 {v:+.0f}%")

    # ② 확산
    v = _v(g.get("breadth70"))
    add("ind_breadth", None if v is None else 2 if v >= 50 else 1 if v >= 30 else 0,
        "-" if v is None else f"업종 내 RS≥70 비중 {v:.0f}%")

    # ③ 산업 RS
    rs, r1, r3, d4 = _v(g.get("rs")), _v(g.get("r1")), _v(g.get("r3")), _v(g.get("rs_chg4w"))
    if rs is None:
        add("ind_rs", None, "-")
    else:
        accel = (r1 is not None and r3 is not None and r1 >= r3) or (d4 is not None and d4 > 0)
        pts = 2 if (rs >= 70 and accel) else 1 if rs >= 60 or (rs >= 50 and accel) else 0
        add("ind_rs", pts, f"업종 RS {rs:.0f} · 1M {_fmt(r1, '', 0)} vs 3M {_fmt(r3, '', 0)} · 4주 {_fmt(d4, signed=True)}")

    # ④ 종목 마진 확대
    v = _v(c.get("opm_yoy_pp"))
    add("margin_up", None if v is None else 2 if v >= 3 else 1 if v > 0 else 0,
        "-" if v is None else f"OPM {_fmt(_v(c.get('opm_now')), '%')} (전년동기 {v:+.1f}pp)")

    # ⑤ 업종 마진 확대
    v = _v(g.get("margin_up_share"))
    add("ind_margin", None if v is None else 2 if v >= 60 else 1 if v >= 40 else 0,
        "-" if v is None else f"업종 내 OPM 확대 종목 {v:.0f}%")

    # ⑥ 리포트 + DART 수주·공급계약 공시 (L5 데이터: 실제 주문)
    n = int(c.get("rep_n30") or 0)
    up = int(c.get("rep_tp_up") or 0)
    dn = int(c.get("dart_n60") or 0)
    dr = _v(c.get("dart_ratio60"))            # 60일 공급계약 금액 합계 / 최근 매출액 (%)
    pts = 2 if (n >= 3 or (n >= 2 and up >= 1)) else 1 if n >= 1 else 0
    if dn and ((dr is not None and dr >= 10) or dn >= 2):
        pts = 2
    elif dn:
        pts = max(pts, 1)
    why = f"30일 리포트 {n}건 · 목표가 상향 {up}건"
    if dn:
        why += f" · 60일 공급계약 공시 {dn}건" + (f" (매출 대비 합계 {dr:.0f}%)" if dr is not None else "")
    add("ev_reports", pts, why)

    # ⑦ 피어 확인
    v = _v(g.get("eps_up_share"))
    add("ev_peers", None if v is None else 2 if v >= 60 else 1 if v >= 40 else 0,
        "-" if v is None else f"업종 커버 종목 중 EPS(E) 상향 {v:.0f}%")

    # ⑧ 시장 지위
    rk, n_ind = c.get("rev_rank"), g.get("n_rev")
    if rk is None or not n_ind:
        add("co_position", None, "매출 자료 없음")
    else:
        pts = 2 if rk <= 3 else 1 if rk <= max(3, n_ind * 0.3) else 0
        add("co_position", pts, f"업종 매출 {rk}위 / {n_ind}")

    # ⑨ EPS 레버리지
    im, lev, ry = _v(c.get("incr_margin")), _v(c.get("op_leverage")), _v(c.get("rev_yoy"))
    if im is None and lev is None:
        add("co_leverage", None, "분기 자료 없음")
    else:
        pts = 0
        if (im is not None and im >= 30 and ry is not None and ry > 0) or (lev is not None and lev >= 2):
            pts = 2
        elif (im is not None and im > 0 and ry is not None and ry > 0) or (lev is not None and lev > 1):
            pts = 1
        add("co_leverage", pts, f"증분마진 {_fmt(im, '%')} · 영업레버리지 {_fmt(lev, 'x', 2)} · 매출YoY {_fmt(ry, '%', signed=True)}")

    # ⑩ 가시성
    cov, roe_e, vol = bool(c.get("covered")), _v(c.get("roe_e")), _v(c.get("vol60"))
    if not cov:
        add("co_visibility", 0, "컨센서스 커버리지 없음")
    else:
        pts = 2 if (roe_e is not None and roe_e >= 10 and (vol is None or vol < 80)) else 1
        add("co_visibility", pts, f"커버리지 있음 · ROE(E) {_fmt(roe_e, '%')} · 변동성60일 {_fmt(vol, '%')}")

    # ⑪ 주가 위치
    hi, ma = _v(c.get("off_high")), _v(c.get("ma200"))
    if hi is None or ma is None:
        add("px_position", None, "-")
    else:
        if -35 <= hi <= -8 and ma > 0:
            pts, txt = 2, "조정 후 추세 유지(좋은 뉴스+과매도 조합)"
        elif ma > 0 and hi > -8:
            pts, txt = 1, "신고가 근접(반응 제한 가능)"
        elif -50 <= hi < -35 and ma > 0:
            pts, txt = 1, "깊은 조정이나 장기 추세(MA200) 유지"
        elif -35 <= hi <= -8 and ma <= 0:
            pts, txt = 1, "조정 중이나 MA200 하회"
        else:
            pts, txt = 0, "추세 이탈 또는 과도 급락"
        add("px_position", pts, f"52주 고점 대비 {hi:+.1f}% · MA200 대비 {ma:+.1f}% — {txt}")

    # ⑫ 수급
    nd, vr, days = c.get("fi_netbuy_days"), _v(c.get("vol_ratio")), c.get("fi_days") or 0
    if nd is None or days < 10:
        add("px_flow", None, "수급 자료 없음")
    else:
        share = nd / days
        pts = 2 if (share >= 0.6 and (vr or 0) >= 1.1) else 1 if share >= 0.5 else 0
        add("px_flow", pts, f"외국인+기관 순매수 {nd}/{days}일 · 최근5일/20일 거래량 {_fmt(vr, 'x', 2)}")

    # ⑬ EPS 추정치
    v = _v(c.get("eps_rev4w"))
    if v is None:
        add("eps_rev", None, "추정치 변화 자료 없음")
    else:
        txt = "적자→흑자 전환" if v >= 900 else "흑자→적자" if v <= -900 else f"{v:+.1f}%"
        pts = 2 if v >= 5 else 1 if v > 0 else 0
        add("eps_rev", pts, f"4주 EPS(E) 변화 {txt}")

    # ⑭ 밸류에이션
    per_e, per_med, peg = _v(c.get("per_e")), _v(g.get("per_e_med")), _v(c.get("peg_e"))
    if per_e is None:
        add("valuation", None, "PER(E) 없음")
    else:
        cheap_rel = per_med is not None and per_e <= per_med
        cheap_peg = peg is not None and 0 < peg <= 1.5
        pts = 2 if (cheap_rel and cheap_peg) else 1 if (cheap_rel or cheap_peg) else 0
        add("valuation", pts, f"PER(E) {per_e:.1f}x vs 업종 중앙값 {_fmt(per_med, 'x')} · PEG(E) {_fmt(peg, '', 2)}")

    # ⑮ 촉매
    dn, last_rep = c.get("next_earn_days"), c.get("rep_days_since")
    if dn is None and last_rep is None:
        add("catalyst", None, "-")
    else:
        pts = 2 if ((dn is not None and dn <= 45) or (last_rep is not None and last_rep <= 7)) \
            else 1 if (dn is not None and dn <= 90) else 0
        add("catalyst", pts, f"다음 실적 {c.get('next_earn_label') or '-'} (D{'' if dn is None else f'-{dn}'}) · 마지막 리포트 {'-' if last_rep is None else f'{last_rep}일 전'}")

    total = sum(i[0] or 0 for i in out)
    n_na = sum(1 for i in out if i[0] is None)
    return {"items": out, "total": int(total), "na": n_na, "grade": interpret(total)}


def classify_core_beta(c: dict, g_peers: list[dict]) -> str:
    """Core(품질·가시성) vs Beta(레버리지·변동성) 분류. 업종 피어 대비 상대 위치."""
    def pct(key, val, higher=True):
        vals = [_v(p.get(key)) for p in g_peers]
        vals = [x for x in vals if x is not None]
        if val is None or len(vals) < 3:
            return None
        r = sum(1 for x in vals if x < val) / len(vals)
        return r if higher else 1 - r

    beta_pts = []
    for key, higher in (("vol60", True), ("op_leverage", True), ("mcap", False)):
        p = pct(key, _v(c.get(key)), higher)
        if p is not None:
            beta_pts.append(p)
    if not beta_pts:
        return "-"
    b = sum(beta_pts) / len(beta_pts)
    return "Beta" if b >= 0.6 else "Core" if b <= 0.4 else "Mixed"
