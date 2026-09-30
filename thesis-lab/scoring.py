# -*- coding: utf-8 -*-
"""
thesis-lab 스코어링 — 9단계 투자 프레임워크의 자동 채점 15항목, 각 0~5점 (75점 만점).

  산업(3)  ① 수요 증가  ② 확산(지속성)  ③ RS 상승·가속
  병목(2)  ④ 종목 마진 확대(가격 전가 proxy)  ⑤ 업종 마진 확대 확인
  증거(2)  ⑥ 회사·애널리스트·공시 확인  ⑦ 피어 확인(업종 EPS 상향 비율)
  기업(3)  ⑧ 시장 지위(업종 내 매출 순위)  ⑨ EPS 레버리지  ⑩ 실적 가시성
  주가(2)  ⑪ 주가 위치  ⑫ 수급·거래
  실적(1)  ⑬ EPS 추정치 상향
  밸류(1)  ⑭ 밸류에이션(업종 대비 PER(E)·PEG)
  촉매(1)  ⑮ 다음 촉매 시점

수동 5항목(병목 구조·고객사 확인·Thesis Break·손익비·기술적 분석)도 각 0~5점으로 HTML 에서 입력한다.
최종 = 20항목 × 5점 = 100점 (자동 75 + 수동 25). 곱셈 환산 없음.
판정(자동 75점 기준, 100점은 비율): 63+ 매우 강함 · 53+ 적극 관심 · 43+ Watchlist · 33+ 부족 · 그 미만 보류
  (= 예전 30점 척도의 25/21/17/13 을 비율로 옮긴 것)

2026-09-30 이전 CSV 히스토리는 0/1/2 (30점) 척도이며 로드 시 ×2.5 로 환산한다.
"""
from __future__ import annotations

import math

AUTO_MAX = 75          # 15 × 5
ITEM_MAX = 5

ITEMS = [
    ("ind_demand", "산업", "산업 수요가 구조적으로 늘고 있다", "업종 컨센서스 영업이익 YoY(E) 중앙값: 0%↑1 · 10%↑2 · 20%↑3 · 30%↑4 · 50%↑5"),
    ("ind_breadth", "산업", "상승이 한두 종목이 아니라 업종 전체로 퍼져 있다", "업종 내 RS≥70 종목 비중: 20%↑1 · 30%↑2 · 40%↑3 · 50%↑4 · 60%↑5"),
    ("ind_rs", "산업", "업종 상대강도(RS)가 높고 최근 더 강해지고 있다", "업종 RS 50/60/70/80 → 1/2/3/4점, 1M RS ≥ 3M RS 또는 4주 상승이면 +1"),
    ("margin_up", "병목", "영업이익률이 전년보다 확대되고 있다 (가격 전가·병목의 간접 증거)", "최근 분기 OPM 전년동기 대비: >0 1 · +1pp 2 · +3pp 3 · +5pp 4 · +8pp 5"),
    ("ind_margin", "병목", "업종 전체의 마진이 함께 좋아지고 있다", "업종 내 OPM 확대 종목 비중: 30%↑1 · 40%↑2 · 50%↑3 · 60%↑4 · 75%↑5"),
    ("ev_reports", "증거", "증권사 리포트와 수주·공급계약 공시가 논리를 뒷받침한다", "30일 리포트 1/2/3/5건 → 1/2/3/4, 목표가 상향 +1 · 60일 공급계약 공시(매출 대비 10%↑ 또는 2건↑)면 4 이상, 30%↑면 5"),
    ("ev_peers", "증거", "업종 피어들의 이익 추정치도 함께 오르고 있다", "업종 커버 종목 중 4주 EPS(E) 상향 비중: 25%↑1 · 40%↑2 · 50%↑3 · 60%↑4 · 75%↑5"),
    ("co_position", "기업", "업종 안에서 매출 규모(시장 지위)가 크다", "업종 내 매출 순위: 1위 5 · 3위 이내 4 · 상위 10% 3 · 30% 2 · 50% 1"),
    ("co_leverage", "기업", "매출이 늘 때 이익이 훨씬 크게 늘어난다 (영업 레버리지)", "증분마진(매출 증가 시) >0/15/30/50% → 2/3/4/5, 또는 영업레버리지 >1/1.5/2/3x → 2/3/4/5 중 큰 값"),
    ("co_visibility", "기업", "실적을 내다보기 쉽다 (커버리지·수익성·낮은 변동성)", "컨센서스 커버리지 2 + ROE(E)≥10 +1, ≥20 +1 + 변동성60일<80% +1"),
    ("px_position", "주가", "주가 위치가 유리하다 (장기 추세는 살아 있고 과열은 아니다)", "MA200 위: 52주 고점 -8~-20% 5 · -20~-35% 4 · 신고가 근접 또는 -35~-50% 3 · 그 밖 1. MA200 아래: -8~-35% 조정 2, 그 밖 0"),
    ("px_flow", "주가", "외국인·기관 수급과 거래량이 뒷받침한다", "외국인+기관 순매수 일수 비중 40/50/60/70% → 1/2/3/4, 거래량 5일/20일 ≥1.1x +1"),
    ("eps_rev", "실적", "EPS 추정치가 최근 상향되고 있다", "4주 EPS(E) 변화: >0 1 · +2% 2 · +5% 3 · +10% 4 · +15% 5 (적자→흑자 5)"),
    ("valuation", "밸류", "밸류에이션이 업종 대비 부담스럽지 않다", "PER(E) ≤ 업종 중앙값 +2 (0.7배 이하 +1) · PEG(E) ≤1.5 +2 (≤1.0 +1), 최대 5"),
    ("catalyst", "촉매", "가까운 촉매가 있다 (다음 실적 발표·최근 리포트)", "다음 실적 D-30 이내 4 · D-45 3 · D-90 2 · 그 밖 1, 7일 내 리포트 +1"),
]

# (key, 구분, 질문, 만점, 채점 안내) — 모두 0~5 직접 입력. 모든 항목은 "점수가 높을수록 투자 논리에 유리".
MANUAL_ITEMS = [
    ("m_bottleneck", "병목", "공급 확대가 어렵다 — 신규 CAPA·인증·기술 장벽 때문에 병목이 오래 간다", 5,
     "0 = 누구나 빠르게 증설 가능 · 3 = 증설에 1~2년 · 5 = 수년 소요, 인증·기술 장벽으로 경쟁사 진입 어려움"),
    ("m_customer", "증거", "고객사(구매자)가 수요 강세를 직접 확인해 준다 — 실적콜 발언·주문·계약", 5,
     "0 = 고객 언급 없음 · 3 = 간접 언급이나 단일 고객 · 5 = 복수 고객이 부족·주문 증가를 직접 언급"),
    ("m_break", "반증", "반증 신호가 없다 — 논리를 깨뜨릴 조건을 정해 두었고, 현재 그 신호가 관측되지 않는다", 5,
     "0 = 반증 조건이 이미 2개 이상 발생 · 3 = 조건은 정했으나 일부 경고 신호 · 5 = 조건 명확 + 현재 신호 없음"),
    ("m_rr", "손익비", "손익비가 좋다 — 상승 여력이 하락 위험의 2배 이상", 5,
     "0 = 1:1 이하 · 3 = 2:1 · 5 = 3:1 이상 (Bull/Base/Bear 시나리오로 계산)"),
    ("m_tech", "기술", "기술적 분석이 우호적이다 — 추세·패턴·거래량·수급 종합", 5,
     "0 = 하락 추세·분산(distribution) · 3 = 바닥 형성·횡보 · 5 = 상승 추세·돌파·거래량 동반"),
]


def interpret(score: float | None, max_pts: float = AUTO_MAX) -> str:
    """판정. 기준은 30점 척도의 25/21/17/13 을 비율로 옮긴 것 (75점: 63/53/43/33)."""
    if score is None:
        return "-"
    r = score / max_pts * 30
    if r >= 25:
        return "매우 강한 투자 후보"
    if r >= 21:
        return "적극적인 관심"
    if r >= 17:
        return "Watchlist"
    if r >= 13:
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


def _band(v, cuts):
    """cuts 오름차순 임계값 리스트: v 가 cuts[k] 이상이면 k+1 점 (최대 len(cuts))."""
    pts = 0
    for k, c in enumerate(cuts):
        if v >= c:
            pts = k + 1
    return pts


def _cap(v):
    return max(0, min(ITEM_MAX, int(v)))


def score_company(c: dict, g: dict) -> dict:
    """c: 종목 지표 dict, g: 소속 그룹(업종) 지표 dict → {'items': [[점수, 근거] ...], 'total', 'na', 'grade'}

    items 는 ITEMS 순서의 [점수 0~5 또는 None(자료 없음), 근거 문자열] 배열.
    자료가 없는 항목은 0점으로 합산하되 None 으로 남겨 화면에서 '-' 로 표시한다.
    """
    out = []

    def add(key, pts, why):
        out.append([None if pts is None else _cap(pts), why])

    # ① 산업 수요
    v = _v(g.get("op_yoy_e_med"))
    if v is None:
        add("ind_demand", None, "업종 컨센서스 없음")
    else:
        add("ind_demand", _band(v, [0, 10, 20, 30, 50]), f"업종 영업이익 YoY(E) 중앙값 {v:+.0f}%")

    # ② 확산
    v = _v(g.get("breadth70"))
    add("ind_breadth", None if v is None else _band(v, [20, 30, 40, 50, 60]),
        "-" if v is None else f"업종 내 RS≥70 비중 {v:.0f}%")

    # ③ 산업 RS
    rs, r1, r3, d4 = _v(g.get("rs")), _v(g.get("r1")), _v(g.get("r3")), _v(g.get("rs_chg4w"))
    if rs is None:
        add("ind_rs", None, "-")
    else:
        accel = (r1 is not None and r3 is not None and r1 >= r3) or (d4 is not None and d4 > 0)
        pts = _band(rs, [50, 60, 70, 80]) + (1 if accel else 0)
        add("ind_rs", pts, f"업종 RS {rs:.0f} · 1M {_fmt(r1, '', 0)} vs 3M {_fmt(r3, '', 0)} · 4주 {_fmt(d4, signed=True)}{' · 가속' if accel else ''}")

    # ④ 종목 마진 확대
    v = _v(c.get("opm_yoy_pp"))
    if v is None:
        add("margin_up", None, "-")
    else:
        pts = 0 if v <= 0 else 1 if v < 1 else _band(v, [1, 3, 5, 8]) + 1
        add("margin_up", pts, f"OPM {_fmt(_v(c.get('opm_now')), '%')} (전년동기 {v:+.1f}pp)")

    # ⑤ 업종 마진 확대
    v = _v(g.get("margin_up_share"))
    add("ind_margin", None if v is None else _band(v, [30, 40, 50, 60, 75]),
        "-" if v is None else f"업종 내 OPM 확대 종목 {v:.0f}%")

    # ⑥ 리포트 + DART 수주·공급계약 공시
    n = int(c.get("rep_n30") or 0)
    up = int(c.get("rep_tp_up") or 0)
    dn = int(c.get("dart_n60") or 0)
    dr = _v(c.get("dart_ratio60"))
    pts = _band(n, [1, 2, 3, 5]) + (1 if (up >= 1 and n >= 1) else 0)
    if dn and ((dr is not None and dr >= 10) or dn >= 2):
        pts = max(pts, 4)
        if dr is not None and dr >= 30:
            pts = 5
    elif dn:
        pts = max(pts, 2)
    why = f"30일 리포트 {n}건 · 목표가 상향 {up}건"
    if dn:
        why += f" · 60일 공급계약 공시 {dn}건" + (f" (매출 대비 합계 {dr:.0f}%)" if dr is not None else "")
    add("ev_reports", pts, why)

    # ⑦ 피어 확인
    v = _v(g.get("eps_up_share"))
    add("ev_peers", None if v is None else _band(v, [25, 40, 50, 60, 75]),
        "-" if v is None else f"업종 커버 종목 중 EPS(E) 상향 {v:.0f}%")

    # ⑧ 시장 지위
    rk, n_ind = c.get("rev_rank"), g.get("n_rev")
    if rk is None or not n_ind:
        add("co_position", None, "매출 자료 없음")
    else:
        q = rk / n_ind
        pts = 5 if rk == 1 else 4 if rk <= 3 else 3 if q <= 0.10 else 2 if q <= 0.30 else 1 if q <= 0.50 else 0
        add("co_position", pts, f"업종 매출 {rk}위 / {n_ind}")

    # ⑨ EPS 레버리지
    im, lev, ry = _v(c.get("incr_margin")), _v(c.get("op_leverage")), _v(c.get("rev_yoy"))
    if im is None and lev is None:
        add("co_leverage", None, "분기 자료 없음")
    else:
        p1 = (_band(im, [0.0001, 15, 30, 50]) + 1 if (im is not None and ry is not None and ry > 0 and im > 0) else 0)
        p2 = (_band(lev, [1.0001, 1.5, 2, 3]) + 1 if (lev is not None and lev > 1) else 0)
        pts = max(p1, p2)
        add("co_leverage", pts, f"증분마진 {_fmt(im, '%')} · 영업레버리지 {_fmt(lev, 'x', 2)} · 매출YoY {_fmt(ry, '%', signed=True)}")

    # ⑩ 가시성
    cov, roe_e, vol = bool(c.get("covered")), _v(c.get("roe_e")), _v(c.get("vol60"))
    if not cov:
        add("co_visibility", 0, "컨센서스 커버리지 없음")
    else:
        pts = 2 + (1 if roe_e is not None and roe_e >= 10 else 0) + (1 if roe_e is not None and roe_e >= 20 else 0) \
            + (1 if (vol is not None and vol < 80) else 0)
        add("co_visibility", pts, f"커버리지 있음 · ROE(E) {_fmt(roe_e, '%')} · 변동성60일 {_fmt(vol, '%')}")

    # ⑪ 주가 위치
    hi, ma = _v(c.get("off_high")), _v(c.get("ma200"))
    if hi is None or ma is None:
        add("px_position", None, "-")
    else:
        if ma > 0:
            if hi > -8:
                pts, txt = 3, "신고가 근접(반응 제한 가능)"
            elif hi >= -20:
                pts, txt = 5, "적당한 조정 후 추세 유지(좋은 뉴스+과매도 조합)"
            elif hi >= -35:
                pts, txt = 4, "조정 후 추세 유지"
            elif hi >= -50:
                pts, txt = 3, "깊은 조정이나 장기 추세(MA200) 유지"
            else:
                pts, txt = 1, "급락 후 MA200 위 회복"
        else:
            if -35 <= hi <= -8:
                pts, txt = 2, "조정 중이나 MA200 하회"
            else:
                pts, txt = 0, "추세 이탈 또는 과도 급락"
        add("px_position", pts, f"52주 고점 대비 {hi:+.1f}% · MA200 대비 {ma:+.1f}% — {txt}")

    # ⑫ 수급
    nd, vr, days = c.get("fi_netbuy_days"), _v(c.get("vol_ratio")), c.get("fi_days") or 0
    if nd is None or days < 10:
        add("px_flow", None, "수급 자료 없음")
    else:
        share = nd / days
        pts = _band(share, [0.4, 0.5, 0.6, 0.7]) + (1 if (vr or 0) >= 1.1 else 0)
        add("px_flow", pts, f"외국인+기관 순매수 {nd}/{days}일 · 최근5일/20일 거래량 {_fmt(vr, 'x', 2)}")

    # ⑬ EPS 추정치
    v = _v(c.get("eps_rev4w"))
    if v is None:
        add("eps_rev", None, "추정치 변화 자료 없음")
    else:
        txt = "적자→흑자 전환" if v >= 900 else "흑자→적자" if v <= -900 else f"{v:+.1f}%"
        pts = 5 if v >= 900 else 0 if v <= 0 else _band(v, [0.0001, 2, 5, 10, 15])
        add("eps_rev", pts, f"4주 EPS(E) 변화 {txt}")

    # ⑭ 밸류에이션
    per_e, per_med, peg = _v(c.get("per_e")), _v(g.get("per_e_med")), _v(c.get("peg_e"))
    if per_e is None:
        add("valuation", None, "PER(E) 없음")
    elif per_e <= 0:
        add("valuation", 0, f"PER(E) {per_e:.1f}x (적자)")
    else:
        pts = 0
        if per_med is not None and per_e <= per_med:
            pts += 2 + (1 if per_e <= per_med * 0.7 else 0)
        if peg is not None and 0 < peg <= 1.5:
            pts += 2 + (1 if peg <= 1.0 else 0)
        add("valuation", pts, f"PER(E) {per_e:.1f}x vs 업종 중앙값 {_fmt(per_med, 'x')} · PEG(E) {_fmt(peg, '', 2)}")

    # ⑮ 촉매
    dn_e, last_rep = c.get("next_earn_days"), c.get("rep_days_since")
    if dn_e is None and last_rep is None:
        add("catalyst", None, "-")
    else:
        pts = 1
        if dn_e is not None:
            pts = 4 if dn_e <= 30 else 3 if dn_e <= 45 else 2 if dn_e <= 90 else 1
        if last_rep is not None and last_rep <= 7:
            pts += 1
        add("catalyst", pts, f"다음 실적 {c.get('next_earn_label') or '-'} (D{'' if dn_e is None else f'-{dn_e}'}) · 마지막 리포트 {'-' if last_rep is None else f'{last_rep}일 전'}")

    total = sum(i[0] or 0 for i in out)
    n_na = sum(1 for i in out if i[0] is None)
    return {"items": out, "total": int(total), "max": AUTO_MAX, "na": n_na, "grade": interpret(total)}


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
