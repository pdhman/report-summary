"""
PMSS 100점 모델 (문서 12장) + Macro Regime 판정 (문서 8~9장) + 노출 조절 (문서 13장)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config as C
from cross_asset import Confirmation
from signals import Signal


@dataclass
class Score:
    shock: float          # /30
    confirm: float        # /30
    liquidity: float      # /15
    persistence: float    # /15
    importance: float     # /10

    @property
    def total(self) -> float:
        return round(self.shock + self.confirm + self.liquidity + self.persistence + self.importance, 1)

    @property
    def label(self) -> str:
        for th, name in C.PMSS_BANDS:
            if self.total < th:
                return name
        return "Event Shock"


def score(sig: Signal, conf: Confirmation, importance: int) -> Score:
    # 1) Probability Shock (30): |Z|와 |ΔP24h| 중 큰 쪽. Z=3 또는 ΔP=25%p에서 만점
    # 극단 확률(3% 미만·97% 초과) 시장은 logit 이 몇 %p 에도 크게 튀어 Z 가 노이즈 → ΔP 만 사용
    z_part = min(abs(sig.z) / 3.0, 1.0) if 0.03 <= sig.prob <= 0.97 else 0.0
    dp_part = min(abs(sig.dp_24h or 0) / 0.25, 1.0)
    shock = 30 * max(z_part, dp_part)

    # 2) Cross-asset (30): -1~+1 → 0~30 (반대면 0)
    confirm = 30 * max(conf.score, 0.0)

    # 3) Liquidity (15)
    liquidity = {"A": 15, "B": 10, "X": 0}[sig.liq_grade]

    # 4) Persistence (15)
    persist = 15 * sig.persistence

    # 5) Event Importance (10)
    imp = float(min(importance, 10))

    return Score(round(shock, 1), round(confirm, 1), liquidity, round(persist, 1), imp)


# ─────────────────────────────────────────────
# Macro Regime
# ─────────────────────────────────────────────
REGIMES = {
    (+1, -1, -1): ("🟢", "Goldilocks",   {"Equity": "+",  "Duration": "+",  "Semis": "++", "Korea": "+"}),
    (+1, +1, -1): ("🔵", "Hard Landing", {"Equity": "-",  "Duration": "+++", "Semis": "-",  "Korea": "--"}),
    (-1, -1, +1): ("🟠", "Reflation",    {"Equity": "+",  "Duration": "--", "Semis": "→",  "Korea": "+"}),
    (-1, +1, +1): ("🔴", "Stagflation",  {"Equity": "--", "Duration": "→",  "Semis": "--", "Korea": "--"}),
}


RISK_OFF_SIGNALS = {"Equity": "--", "Duration": "+", "Semis": "--", "Korea": "--"}


@dataclass
class Regime:
    icon: str
    name: str
    easing: float      # 축별 24h 가중 ΔP (%p)
    recession: float
    inflation: float
    risk: float
    signals: dict[str, str]
    base_name: str = ""                                   # 위험 회피 발동 시 원래(4분면) 레짐
    risk_off: list[str] = field(default_factory=list)     # 발동 사유 (비어 있으면 미발동)

    @property
    def arrows(self) -> str:
        def a(v):
            return "↑" if v > 0.02 else ("↓" if v < -0.02 else "→")
        return (f"Easing {a(self.easing)} / Recession {a(self.recession)} / "
                f"Inflation {a(self.inflation)} / Risk {a(self.risk)}")


def _sgn(v: float, th: float = 0.02) -> int:
    return 1 if v > th else (-1 if v < -th else 0)


def risk_off_reasons(snap) -> list[str]:
    """급락 안전장치: 실제 시장이 공포 상태면 사유 목록을 돌려준다(아니면 빈 목록).

    폴리마켓의 '완화 축↑'은 급락 때문에 연준이 덜 올릴 거라는 뜻일 수 있다
    (2026-11-06 AI 반도체 급락 때 모델이 Goldilocks·비중 70% 를 낸 사고).
    그래서 확률 이동보다 실제 가격의 공포 신호를 우선한다.
    """
    if snap is None or not getattr(snap, "ok", False):
        return []
    R = C.RISK_OFF
    out = []
    vix = snap.chg_24h.get(R["vix_ticker"])
    if vix is not None and vix >= R["vix_up"]:
        out.append(f"VIX {vix*100:+.1f}% (기준 +{R['vix_up']*100:.0f}%)")
    nq = snap.chg_24h.get(R["nasdaq_ticker"])
    if nq is not None and nq <= R["nasdaq_down"]:
        out.append(f"나스닥 선물 {nq*100:+.1f}% (기준 {R['nasdaq_down']*100:.0f}%)")
    return out


def classify_regime(axis_moves: dict[str, float], snap=None) -> Regime:
    """
    axis_moves: {"easing": +0.12, "recession": -0.01, ...}
      = 카테고리 내 시장들의 polarity·유동성 가중 24h ΔP 합
    snap: Cross-asset 스냅샷. 급락 조건(RISK_OFF)이면 4분면 대신 Risk-Off 레짐.
    """
    e = axis_moves.get("easing", 0.0)
    r = axis_moves.get("recession", 0.0)
    i = axis_moves.get("inflation", 0.0)
    k = axis_moves.get("risk", 0.0)
    key = (_sgn(e), _sgn(r), _sgn(i))

    if key in REGIMES:
        icon, name, sigs = REGIMES[key]
    else:
        # 축 하나가 → 인 경우: 가장 가까운 레짐으로 근사 (0은 -1로 간주 = 변화 없음 → 완화적 해석)
        approx = tuple(v if v != 0 else -1 for v in key)
        icon, name, sigs = REGIMES.get(approx, ("⚪", "Neutral / Mixed", {"Equity": "→", "Duration": "→", "Semis": "→", "Korea": "→"}))
        if key != approx:
            name = f"{name} (근사)"
    sigs = dict(sigs)
    if k > 0.05:
        sigs["Risk"] = "⚠ Geopolitical/Tariff ↑"

    reasons = risk_off_reasons(snap)
    if reasons:
        ro_sigs = dict(RISK_OFF_SIGNALS)
        if "Risk" in sigs:
            ro_sigs["Risk"] = sigs["Risk"]
        return Regime("🟣", "Risk-Off (급락·공포)", e, r, i, k, ro_sigs, base_name=name, risk_off=reasons)
    return Regime(icon, name, e, r, i, k, sigs, base_name=name)


def exposure_adjustment(regime_name: str, pmss: float, base_net: float = 0.50) -> float:
    """
    문서 13장: Polymarket은 종목 선정이 아니라 Net Exposure(베타) 조절 변수.
    """
    # 위험 회피(급락·공포) 중에는 비중 확대를 제안하지 않는다 — 기본 비중 유지
    if regime_name.startswith("Risk-Off"):
        return base_net
    if pmss < 55:
        return base_net
    step = 0.10 if pmss < 80 else 0.20
    if regime_name.startswith("Goldilocks"):
        return base_net + step
    if regime_name.startswith("Hard Landing"):
        return base_net - 2 * step
    if regime_name.startswith("Stagflation"):
        return base_net - 2 * step
    if regime_name.startswith("Reflation"):
        return base_net
    return base_net
