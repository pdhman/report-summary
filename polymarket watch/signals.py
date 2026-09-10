"""
확률 → 신호 변환 (문서 3~5장)
  ΔP_h      : h시간 전 대비 확률 변화 (%p)
  L_t       : logit = ln(P/(1-P))
  Z_h       : ΔL_h / (σ_1h · √h)   — 1시간 logit 변화의 표준편차로 정규화
  Liquidity : A / B / X 등급
  Persistence: 24h 이동이 얼마나 유지되고 있는가 (0~1)

히스토리가 없는 시장(호출 상한 밖)은 Gamma 의 oneHourPriceChange / oneDayPriceChange 로
ΔP 만 채우고 Z·persistence 는 0 으로 둔다(lite 신호).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

import config as C
from polymarket_client import Market

EPS = 1e-4


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def price_at(history: list[tuple[int, float]], hours_ago: float, now: float | None = None) -> float | None:
    if not history:
        return None
    now = now or time.time()
    target = now - hours_ago * 3600
    cand = [p for t, p in history if t <= target]
    if cand:
        return cand[-1]
    # 1주 히스토리의 첫 점은 정확히 168h 전보다 조금 뒤에 찍힌다 → 12시간 이내면 첫 점으로 대체
    if history[0][0] - target <= 12 * 3600:
        return history[0][1]
    return None


@dataclass
class Signal:
    prob: float
    dp_1h: float | None
    dp_6h: float | None
    dp_24h: float | None
    dp_7d: float | None
    z_1h: float | None
    z_24h: float | None
    sigma_1h: float | None
    liq_grade: str
    persistence: float
    band: str
    lite: bool = False

    @property
    def z(self) -> float:
        for v in (self.z_24h, self.z_1h):
            if v is not None:
                return v
        return 0.0

    @property
    def direction(self) -> int:
        d = self.dp_24h if self.dp_24h is not None else (self.dp_1h or 0.0)
        return 1 if d > 0 else (-1 if d < 0 else 0)


def dp_band(dp: float | None) -> str:
    if dp is None:
        return "n/a"
    a = abs(dp)
    for th, name in C.DP_BANDS:
        if a < th:
            return name
    return "Event Shock"


def liquidity_grade(mk: Market) -> str:
    sp = mk.spread if mk.spread is not None else 1.0
    if sp > C.MAX_SPREAD:
        return "X"
    if mk.volume24h >= C.LIQUIDITY["A"]["volume24h"] and sp <= C.LIQUIDITY["A"]["spread"]:
        return "A"
    if mk.volume24h >= C.LIQUIDITY["B"]["volume24h"] and sp <= C.LIQUIDITY["B"]["spread"]:
        return "B"
    if mk.liquidity >= C.MIN_LIQUIDITY_USD and sp <= C.LIQUIDITY["B"]["spread"]:
        return "B"
    return "X"


def hourly_sigma(history: list[tuple[int, float]]) -> float | None:
    if len(history) < 24:
        return None
    ts = np.array([t for t, _ in history], dtype=float)
    ps = np.array([p for _, p in history], dtype=float)
    grid = np.arange(ts[0], ts[-1] + 1, 3600.0)
    if len(grid) < 24:
        return None
    idx = np.searchsorted(ts, grid, side="right") - 1
    idx = np.clip(idx, 0, len(ps) - 1)
    lg = np.array([logit(p) for p in ps[idx]])
    d = np.diff(lg)
    d = d[np.isfinite(d)]
    if len(d) < 12:
        return None
    s = float(np.std(d, ddof=1))
    return max(s, 0.02)


def persistence(history: list[tuple[int, float]], prob_now: float, dp_24h: float | None,
                now: float | None = None) -> float:
    if not history or not dp_24h or abs(dp_24h) < 0.02:
        return 0.0
    now = now or time.time()
    p_24 = prob_now - dp_24h
    level = p_24 + 0.5 * dp_24h
    recent = [p for t, p in history if t >= now - 6 * 3600]
    if not recent:
        return 0.0
    if dp_24h > 0:
        held = sum(1 for p in recent if p >= level)
    else:
        held = sum(1 for p in recent if p <= level)
    return held / len(recent)


def compute(mk: Market, now: float | None = None) -> Signal:
    now = now or time.time()
    p = mk.prob
    h = mk.history

    if not h:
        d1, d24 = mk.gamma_d1h, mk.gamma_d24
        return Signal(prob=p, dp_1h=d1, dp_6h=None, dp_24h=d24, dp_7d=None,
                      z_1h=None, z_24h=None, sigma_1h=None,
                      liq_grade=liquidity_grade(mk), persistence=0.0, band=dp_band(d24), lite=True)

    def dp(hrs):
        q = price_at(h, hrs, now)
        return None if q is None else p - q

    d1, d6, d24, d7 = dp(1), dp(6), dp(24), dp(24 * 7)
    # 히스토리 마지막 점이 1시간 넘게 오래됐으면 Gamma 의 1h 변화가 더 정확
    if mk.gamma_d1h is not None and (not h or now - h[-1][0] > 3600):
        d1 = mk.gamma_d1h
    sig = hourly_sigma(h)

    def z(hrs, d):
        if sig is None or d is None:
            return None
        q = p - d
        return (logit(p) - logit(q)) / (sig * math.sqrt(hrs))

    return Signal(
        prob=p, dp_1h=d1, dp_6h=d6, dp_24h=d24, dp_7d=d7,
        z_1h=z(1, d1), z_24h=z(24, d24), sigma_1h=sig,
        liq_grade=liquidity_grade(mk),
        persistence=persistence(h, p, d24, now),
        band=dp_band(d24),
    )
