"""
Cross-Asset Confirmation (문서 6, 7, 10, 11장)
Polymarket = 선행 신호, 채권/환율/선물 = 확인 신호.
yfinance 1시간봉으로 24h 변화율을 계산하고 EXPECTED_DIRECTION과 대조한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import config as C

log = logging.getLogger("pmw.xasset")


@dataclass
class AssetSnapshot:
    chg_24h: dict[str, float] = field(default_factory=dict)   # ticker → 24h return
    last: dict[str, float] = field(default_factory=dict)
    ok: bool = False

    def arrow(self, ticker: str) -> str:
        v = self.chg_24h.get(ticker)
        if v is None:
            return "?"
        th = C.ASSET_FLAT_THRESHOLD.get(ticker, C.ASSET_FLAT_THRESHOLD["default"])
        return "↑" if v > th else ("↓" if v < -th else "→")

    def sign(self, ticker: str) -> int:
        a = self.arrow(ticker)
        return {"↑": 1, "↓": -1}.get(a, 0)


def fetch_assets(tickers: list[str] | None = None) -> AssetSnapshot:
    tickers = tickers or list(C.ASSETS)
    snap = AssetSnapshot()
    try:
        import yfinance as yf
        df = yf.download(tickers, period="5d", interval="1h", progress=False,
                         auto_adjust=False, group_by="ticker", threads=True)
    except Exception as e:                       # noqa: BLE001
        log.warning("yfinance failed: %s", e)
        return snap
    for t in tickers:
        try:
            s = df[t]["Close"].dropna() if len(tickers) > 1 else df["Close"].dropna()
            if len(s) < 2:
                continue
            last = float(s.iloc[-1])
            # 24시간 전 이상 떨어진 마지막 봉
            cutoff = s.index[-1] - __import__("pandas").Timedelta(hours=24)
            prev_s = s[s.index <= cutoff]
            prev = float(prev_s.iloc[-1]) if len(prev_s) else float(s.iloc[0])
            snap.last[t] = last
            snap.chg_24h[t] = last / prev - 1.0
        except Exception as e:                   # noqa: BLE001
            log.debug("asset %s parse fail: %s", t, e)
    snap.ok = len(snap.chg_24h) > 0
    return snap


@dataclass
class Confirmation:
    score: float          # -1 ~ +1 (기대방향 일치 비율)
    agree: list[str]
    disagree: list[str]
    flat: list[str]
    divergence_type: int  # 문서 16장 Type 1~4, 0 = 판정불가

    @property
    def confirmed(self) -> bool:
        return self.score >= 0.5


def confirm(axis: str, event_direction: int, snap: AssetSnapshot) -> Confirmation:
    """
    axis 축이 event_direction(+1/-1) 방향으로 움직였을 때
    각 자산의 실제 24h 방향이 기대와 맞는지 채점.
    """
    exp = C.EXPECTED_DIRECTION.get(axis, {})
    if not exp or not snap.ok or event_direction == 0:
        return Confirmation(0.0, [], [], [], 0)
    agree, disagree, flat = [], [], []
    for tk, d in exp.items():
        s = snap.sign(tk)
        if s == 0:
            flat.append(tk)
        elif s == d * event_direction:
            agree.append(tk)
        else:
            disagree.append(tk)
    n = len(agree) + len(disagree) + len(flat)
    score = (len(agree) - len(disagree)) / n if n else 0.0

    # Divergence Type (문서 16장)
    if len(agree) >= max(1, n // 2):
        dtype = 3            # Confirmation
    elif len(disagree) >= max(1, n // 2):
        dtype = 4            # 해석 불일치
    elif len(flat) >= max(1, n // 2):
        dtype = 1            # 금융시장 미반영 → 알파 후보
    else:
        dtype = 0
    return Confirmation(score, agree, disagree, flat, dtype)


DIVERGENCE_LABEL = {
    0: "판정불가",
    1: "Type1 · 시장 미반영(알파 후보)",
    2: "Type2 · 다른 원인",
    3: "Type3 · Confirmation",
    4: "Type4 · 해석 불일치",
}
