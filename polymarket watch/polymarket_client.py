"""
Polymarket 공개 데이터 API 클라이언트
- Gamma API : 이벤트/시장 목록(태그별), 현재 가격, 호가, 거래량, 유동성, 1h/24h 변화
- CLOB API  : 가격 히스토리 (1주 60분봉 = 신호용, 전기간 일봉 = 추이 차트용)
인증 불필요. 거래 기능은 일절 포함하지 않는다.

국내 ISP 에서는 세 API 모두 HTTP 451 을 돌려주므로 r.jina.ai 리더 프록시로
자동 전환한다(PMW_PROXY=auto). 프록시는 분당 20회 제한이 있어 호출 간격을 둔다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests

import config as C

log = logging.getLogger("pmw.client")

_session = requests.Session()
_session.headers.update({"User-Agent": "polymarket-macro-watch/2.0"})

_proxy_on = C.PROXY_MODE == "always"
_last_proxy_call = 0.0
STATS = {"direct": 0, "proxy": 0, "fail": 0}


def using_proxy() -> bool:
    return _proxy_on


def _parse_json_text(text: str):
    """프록시(text 포맷)는 본문 앞에 메타 줄이 붙을 수 있어 첫 JSON 시작점부터 파싱."""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("no json in body")
    return json.loads(text[min(starts):])


def _get(url: str, params: dict | None = None, retries: int = 3):
    global _proxy_on, _last_proxy_call
    full = url + ("?" + urlencode(params) if params else "")
    for i in range(retries):
        try:
            if not _proxy_on:
                r = _session.get(full, timeout=C.HTTP_TIMEOUT)
                if r.status_code == 451 and C.PROXY_MODE != "never":
                    log.warning("HTTP 451 (지역 차단) → 프록시 경로로 전환: %s", url)
                    _proxy_on = True
                    continue
                if r.status_code == 429:
                    time.sleep(2 * (i + 1))
                    continue
                r.raise_for_status()
                STATS["direct"] += 1
                return r.json()
            # ── 프록시 경로 ──
            wait = C.PROXY_MIN_INTERVAL - (time.time() - _last_proxy_call)
            if wait > 0:
                time.sleep(wait)
            _last_proxy_call = time.time()
            r = _session.get(C.PROXY_URL + full, headers={"X-Return-Format": "text"},
                             timeout=C.PROXY_TIMEOUT)
            if r.status_code == 429:
                log.warning("proxy 429 — 20초 대기")
                time.sleep(20)
                continue
            r.raise_for_status()
            STATS["proxy"] += 1
            return _parse_json_text(r.text)
        except (requests.RequestException, ValueError) as e:
            log.warning("GET %s failed (%s/%s): %s", url, i + 1, retries, e)
            time.sleep(1.5 * (i + 1))
    STATS["fail"] += 1
    return None


def _jsonlist(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return []


def _f(m: dict, k: str, default=None):
    v = m.get(k)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


@dataclass
class Market:
    market_id: str
    condition_id: str
    slug: str
    question: str
    yes_token: str
    prob: float                       # YES 가격 (midpoint 우선)
    volume24h: float
    liquidity: float
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    end_date: str = ""
    label: str = ""                   # groupItemTitle (분포형 이벤트의 구간 라벨)
    gamma_d1h: float | None = None    # Gamma 가 주는 1h / 24h 변화 (히스토리 없을 때 폴백)
    gamma_d24: float | None = None
    event_id: str = ""
    event_slug: str = ""
    event_title: str = ""
    neg_risk: bool = False
    category: str = ""                # GROUPS 키
    polarity: int = 0                 # +1 / -1 / 0
    value: float | None = None        # 구간 라벨의 숫자값 (분포형)
    history: list[tuple[int, float]] = field(default_factory=list)   # 1주 60분봉
    long_history: list[tuple[int, float]] = field(default_factory=list)  # 전기간 일봉

    @property
    def url(self) -> str:
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        return f"https://polymarket.com/market/{self.slug}"


# ─────────────────────────────────────────────
# Gamma
# ─────────────────────────────────────────────
def parse_gamma_market(m: dict, ev: dict | None = None) -> Market | None:
    outcomes = [str(o).lower() for o in _jsonlist(m.get("outcomes"))]
    prices = _jsonlist(m.get("outcomePrices"))
    tokens = _jsonlist(m.get("clobTokenIds"))
    if not outcomes or not prices or not tokens:
        return None
    try:
        yi = outcomes.index("yes")
    except ValueError:
        yi = 0
    try:
        prob = float(prices[yi])
        yes_token = str(tokens[yi])
    except (IndexError, ValueError):
        return None
    if not m.get("active", True) or m.get("closed", False):
        return None

    bid, ask = _f(m, "bestBid"), _f(m, "bestAsk")
    spread = _f(m, "spread")
    if bid is not None and ask is not None and ask >= bid:
        if spread is None:
            spread = ask - bid
        if ask - bid <= 0.1:                  # 호가가 비정상으로 벌어진 경우는 outcomePrices 유지
            prob = (ask + bid) / 2
    ev = ev or (m.get("events") or [{}])[0] or {}
    return Market(
        market_id=str(m.get("id")),
        condition_id=str(m.get("conditionId", "")),
        slug=str(m.get("slug", "")),
        question=str(m.get("question", "")),
        yes_token=yes_token,
        prob=prob,
        volume24h=_f(m, "volume24hr", 0.0) or 0.0,
        liquidity=_f(m, "liquidityNum", None) or _f(m, "liquidity", 0.0) or 0.0,
        best_bid=bid, best_ask=ask, spread=spread,
        end_date=str(m.get("endDate", "") or ""),
        label=str(m.get("groupItemTitle", "") or ""),
        gamma_d1h=_f(m, "oneHourPriceChange"),
        gamma_d24=_f(m, "oneDayPriceChange"),
        event_id=str(ev.get("id", "") or ""),
        event_slug=str(ev.get("slug", "") or ""),
        event_title=str(ev.get("title", "") or ""),
        neg_risk=bool(ev.get("negRisk", False)),
    )


def fetch_events_by_tag(tag: str, limit: int = C.EVENTS_PER_TAG) -> list[dict]:
    data = _get(f"{C.GAMMA_API}/events", {
        "active": "true", "closed": "false", "archived": "false",
        "limit": limit, "order": "volume24hr", "ascending": "false", "tag_slug": tag,
    })
    if not isinstance(data, list):
        return []
    # tag_slug 가 무시된 경우(모르는 태그) 방어: 응답 이벤트가 실제로 그 태그를 갖는지 확인
    out = [e for e in data if tag.lower() in [str(t.get("slug", "")).lower() for t in (e.get("tags") or [])]]
    log.info("Gamma events tag=%s: %d (tagged %d)", tag, len(data), len(out))
    return out


def fetch_event_by_slug(slug: str) -> dict | None:
    data = _get(f"{C.GAMMA_API}/events", {"slug": slug})
    if isinstance(data, list) and data:
        return data[0]
    return None


def event_markets(ev: dict) -> list[Market]:
    out = []
    for m in ev.get("markets") or []:
        mk = parse_gamma_market(m, ev)
        if mk:
            out.append(mk)
    return out


# ─────────────────────────────────────────────
# CLOB
# ─────────────────────────────────────────────
def fetch_price_history(token_id: str, interval: str = "1w", fidelity_min: int = 60) -> list[tuple[int, float]]:
    """
    /prices-history : interval ∈ {1h, 6h, 1d, 1w, 1m, max}, fidelity=분 단위 해상도
    신호용은 1주일치 60분봉(168개), 추이 차트용은 max + 1440(일봉).
    """
    data = _get(f"{C.CLOB_API}/prices-history",
                {"market": token_id, "interval": interval, "fidelity": fidelity_min})
    if not data or not isinstance(data, dict):
        return []
    hist = data.get("history", [])
    out = []
    for h in hist:
        try:
            out.append((int(h["t"]), float(h["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_book(token_id: str) -> dict | None:
    d = _get(f"{C.CLOB_API}/book", {"token_id": token_id})
    if not d:
        return None
    bids = sorted(((float(b["price"]), float(b["size"])) for b in d.get("bids", [])), reverse=True)
    asks = sorted(((float(a["price"]), float(a["size"])) for a in d.get("asks", [])))
    return {
        "best_bid": bids[0][0] if bids else None,
        "best_ask": asks[0][0] if asks else None,
        "bid_depth_5": sum(s for _, s in bids[:5]),
        "ask_depth_5": sum(s for _, s in asks[:5]),
    }


def enrich(mk: Market, with_long: bool = False) -> Market:
    """히스토리 채우기. 실패해도 기본값으로 진행. 호가는 Gamma 값을 그대로 쓴다."""
    mk.history = fetch_price_history(mk.yes_token, "1w", 60)
    if with_long:
        mk.long_history = fetch_price_history(mk.yes_token, "max", 1440)
    return mk


# ─────────────────────────────────────────────
# 구간 라벨 파서: "25 bps decrease" → -25, "≥0.5%" → 0.5, "4.2-4.3%" → 4.25, "$80" → 80
# ─────────────────────────────────────────────
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def bucket_value(label: str) -> float | None:
    s = (label or "").strip().lower().replace(",", "")
    if not s:
        return None
    if "no change" in s or s in ("unchanged", "hold", "flat"):
        return 0.0
    nums = [float(x) for x in _NUM.findall(s)]
    if not nums:
        return None
    # "4.2-4.3%" 같은 범위는 중앙값, "25-49 bps" 도 중앙값
    if len(nums) >= 2 and ("-" in s[1:] or " to " in s or "–" in s) and "increase" not in s and "decrease" not in s:
        v = (nums[0] + nums[1]) / 2
    else:
        v = nums[0]
    # "25 bps decrease" 처럼 방향 단어가 있을 때만 부호 반전 ("≤3.8%" 는 3.8 그대로)
    if any(k in s for k in ("decrease", " cut", "cuts", "lower")) and v > 0:
        v = -v
    return v
