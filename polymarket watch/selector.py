"""
시장 자동선별: 태그별 활성 이벤트 → GROUPS 정규식 분류 → 이벤트 종류(kind) 판정
→ 시장별 극성(polarity)·구간값(value) 부여.

kind
  binary : 이벤트에 시장이 하나 (예: "US recession by end of 2026?")
  dist   : negRisk 이벤트 — 상호배타 구간, 확률 합 ≈ 1 (예: FOMC 결정, CPI 구간)
  ladder : 독립 시장 묶음 — 날짜별/임계값별 (예: "휴전 by 9/14, 9/21, 9/30", "WTI hit $80/$90")
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import config as C
from polymarket_client import Market, bucket_value, event_markets, fetch_event_by_slug, fetch_events_by_tag

log = logging.getLogger("pmw.selector")


@dataclass
class Event:
    event_id: str
    slug: str
    title: str
    group: str
    kind: str                       # binary / dist / ladder
    volume24h: float
    liquidity: float
    end_date: str
    ev_sign: int                    # dist: 라벨 숫자↑ = 축↑(+1)/축↓(-1)/무관(0)
    markets: list[Market] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"


def _match_any(pats: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in pats)


def classify_event(slug: str, title: str) -> str | None:
    text = f"{slug} {title}".lower()
    if re.search(C.GLOBAL_EXCLUDE, text):
        return None
    for g in C.GROUP_ORDER:
        spec = C.GROUPS[g]
        if not _match_any(spec["include"], text):
            continue
        if _match_any(spec["exclude"], text):
            continue
        return g
    return None


def _rule_sign(rules: list[tuple[str, int]], text: str, default: int = 0) -> int:
    for pat, sign in rules:
        if re.search(pat, text):
            return sign
    return default


def _polarity(spec: dict, mk: Market) -> int:
    q = mk.question.lower()
    return _rule_sign(spec["polarity"], q, 0)


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
_DATE_RE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
                      r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b")


def _label_date(label: str, end_date: str) -> date | None:
    m = _DATE_RE.search((label or "").lower())
    if not m:
        return None
    year = int(m.group(3)) if m.group(3) else (int(end_date[:4]) if end_date[:4].isdigit() else datetime.now(timezone.utc).year)
    try:
        return date(year, _MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def _stale(mk: Market) -> bool:
    """마감일이 지났는데 정산 대기 중이라 아직 active 로 남은 시장(예: 'by September 4')은 뺀다."""
    today = datetime.now(timezone.utc).date()
    d = _label_date(mk.label, mk.end_date)
    if d and (today - d).days > 1:
        return True
    if mk.end_date[:10].replace("-", "").isdigit():
        try:
            ed = date.fromisoformat(mk.end_date[:10])
            if (today - ed).days > 2:
                return True
        except ValueError:
            pass
    return False


def build_event(ev: dict, group: str) -> Event | None:
    spec = C.GROUPS[group]
    mks = [m for m in event_markets(ev) if not _stale(m)]
    if not mks:
        return None
    slug = str(ev.get("slug", ""))
    title = str(ev.get("title", ""))
    text = f"{slug} {title}".lower()
    neg = bool(ev.get("negRisk", False))
    kind = "binary" if len(mks) == 1 else ("dist" if neg else "ladder")
    ev_sign = _rule_sign(spec["ev_sign"], text, 0)

    for mk in mks:
        mk.category = group
        mk.value = bucket_value(mk.label) if mk.label else bucket_value(mk.question)

    if kind == "dist":
        # 구간 정렬(숫자값 있으면 오름차순) + 극성: 내재 평균 μ 기준 위(+)/아래(-) × ev_sign
        vals = [(m.value, m.prob) for m in mks if m.value is not None]
        if vals and ev_sign != 0:
            tot = sum(p for _, p in vals) or 1.0
            mu = sum(v * p for v, p in vals) / tot
            for m in mks:
                if m.value is None:
                    m.polarity = 0
                else:
                    m.polarity = (1 if m.value > mu else -1 if m.value < mu else 0) * ev_sign
        else:
            for m in mks:
                m.polarity = _polarity(spec, m) if spec["axis"] != "none" else 0
        if all(m.value is not None for m in mks):
            mks.sort(key=lambda m: m.value)
    else:
        for m in mks:
            m.polarity = _polarity(spec, m) if spec["axis"] != "none" else 0
        if kind == "ladder":
            # 날짜 사다리는 Gamma 순서(대개 시간순), 숫자 사다리는 값 오름차순
            if all(m.value is not None for m in mks) and len({m.value for m in mks}) == len(mks):
                mks.sort(key=lambda m: m.value)
        else:
            mks.sort(key=lambda m: -m.volume24h)

    return Event(
        event_id=str(ev.get("id", "")), slug=slug, title=title, group=group, kind=kind,
        volume24h=float(ev.get("volume24hr") or 0.0), liquidity=float(ev.get("liquidity") or 0.0),
        end_date=str(ev.get("endDate", "") or ""), ev_sign=ev_sign, markets=mks,
    )


def select_events() -> list[Event]:
    raw: dict[str, dict] = {}
    for slug in C.MANUAL_EVENTS:
        ev = fetch_event_by_slug(slug)
        if ev:
            raw[str(ev.get("id"))] = ev
    for tag in C.TAGS:
        for ev in fetch_events_by_tag(tag):
            raw.setdefault(str(ev.get("id")), ev)
    log.info("raw events: %d", len(raw))

    by_group: dict[str, list[Event]] = {g: [] for g in C.GROUP_ORDER}
    for ev in raw.values():
        if float(ev.get("liquidity") or 0) < C.MIN_EVENT_LIQUIDITY:
            continue
        g = classify_event(str(ev.get("slug", "")), str(ev.get("title", "")))
        if not g:
            continue
        e = build_event(ev, g)
        if e:
            by_group[g].append(e)

    out: list[Event] = []
    for g in C.GROUP_ORDER:
        evs = sorted(by_group[g], key=lambda e: -e.volume24h)[: C.GROUPS[g]["max_events"]]
        out.extend(evs)
        log.info("group %s: %d events (%d markets)", g, len(evs), sum(len(e.markets) for e in evs))
    return out


def signal_markets(events: list[Event]) -> list[Market]:
    """1주 히스토리를 받아 PMSS 를 계산할 시장: binary·ladder 전부 + dist 는 확률 ≥ 하한 구간.
    거래량순 상한(MAX_SIGNAL_HISTORY) 적용."""
    cand: list[Market] = []
    for e in events:
        for m in e.markets:
            if e.kind == "dist" and m.prob < C.SIGNAL_MIN_PROB:
                continue
            if e.kind != "dist" and m.prob < 0.01 and m.volume24h < 1000:
                continue
            cand.append(m)
    cand.sort(key=lambda m: -(m.volume24h + 0.05 * m.liquidity))
    return cand[: C.MAX_SIGNAL_HISTORY]


def key_series_markets(events: list[Event]) -> list[tuple[str, Market]]:
    out: list[tuple[str, Market]] = []
    seen: set[str] = set()
    for ev_pat, mk_pat, name in C.KEY_SERIES:
        for e in events:
            if not re.search(ev_pat, e.slug.lower()):
                continue
            for m in e.markets:
                if re.search(mk_pat, (m.label or m.question).lower()) and m.market_id not in seen:
                    out.append((name, m))
                    seen.add(m.market_id)
                    break
            else:
                continue
            break
    return out
