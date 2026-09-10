"""
Polymarket Macro Watch — 설정
PMSS(Polymarket Macro Signal System) 기준값 + 시장 선별 규칙.
자주 손보는 곳: GROUPS(선별 정규식·극성), KEY_SERIES(장기 차트), TAGS.
"""
from __future__ import annotations

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DATA_DIR = os.path.join(DOCS_DIR, "data")

# ─────────────────────────────────────────────
# 1. API 엔드포인트 / 프록시
#    국내 ISP 에서는 gamma/clob 이 HTTP 451(지역 차단)을 돌려준다. 그 경우
#    r.jina.ai 리더 프록시(분당 20회)로 자동 전환한다. PMW_PROXY=always|never|auto
# ─────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
HTTP_TIMEOUT = 20
PROXY_MODE = os.getenv("PMW_PROXY", "auto")
PROXY_URL = "https://r.jina.ai/"
PROXY_MIN_INTERVAL = 3.1          # 초 — 분당 20회 제한 준수
PROXY_TIMEOUT = 60

# ─────────────────────────────────────────────
# 2. 시장 선별
#    Gamma /events?tag_slug= 로 태그별 활성 이벤트(거래량순)를 모은 뒤
#    GROUPS 의 정규식(slug|title 소문자)으로 분류한다. 어느 그룹에도 안 걸리면 버린다.
# ─────────────────────────────────────────────
TAGS = ["economy", "fed-rates", "macro-indicators", "Global-Rates", "inflation",
        "treasuries", "geopolitics", "oil", "tariffs", "trade-war",
        "government-shutdown", "jobs-report", "gdp", "china"]
EVENTS_PER_TAG = 100
MANUAL_EVENTS: list[str] = []     # 자동선별이 놓친 이벤트 slug 를 직접 등록

# 어떤 그룹이든 무조건 제외 (스포츠·연예·기업가치·주택 등 비매크로)
GLOBAL_EXCLUDE = (r"parlay|combo|temperature|emmy|oscar|nobel|net-worth|largest-company|"
                  r"home-value|median-home|best-ai|ai-model|ai-company|bitcoin|crypto|ethereum|"
                  r"stripe|acquire|ipo-valuation|prime-minister|election|sports|rials|"
                  r"companies-will-the-us-take|eggs")

# 그룹 정의 (검사 순서 = 아래 순서. GEOPOLITICS 는 범위가 넓어 마지막)
#   axis        : Regime 축 (easing / recession / inflation / risk / none)
#   importance  : PMSS Event Importance (0~10)
#   include     : slug 또는 title(소문자)에 하나라도 매치되면 후보
#   exclude     : 매치되면 제외
#   max_events  : 거래량 상위 N 개만
#   polarity    : [(정규식, ±1)] 순서대로 검사, 첫 매치 채택 — 확률↑ 가 축↑(+1) / 축↓(-1)
#                 (binary·ladder 시장 질문에 적용)
#   ev_sign     : [(정규식, ±1)] 분포형(dist) 이벤트의 숫자 라벨: 값이 클수록 축↑(+1)/축↓(-1)
GROUPS = {
    "FED": {
        "title": "연준 금리 경로", "icon": "🏛️", "axis": "easing", "importance": 10,
        "desc": "FOMC 회의별 결정 확률 분포 · 2026년 인하 횟수 · 인상 시점",
        "include": [r"^fed-decision-in-", r"^how-many-fed-rate-(cuts|hikes)", r"^fed-rate-(hike|cut)-by",
                    r"^fed-rate-(hike|cut)-in-", r"^fed-emergency-rate-cut", r"^what-will-fed-rate-hit",
                    r"^how-many-dissent-at-the-.*fed", r"warsh-out-as-fed-chair", r"fire-warsh"],
        "exclude": [r"^fed-decisions-"],
        "max_events": 10,
        "polarity": [(r"decrease|cut|lower|ease", +1), (r"increase|hike|raise", -1)],
        "ev_sign": [(r"how-many-fed-rate-cuts", +1), (r"how-many-fed-rate-hikes", -1),
                    (r"fed-decision-in", -1), (r"what-will-fed-rate-hit", -1), (r"dissent", 0)],
    },
    "INFLATION": {
        "title": "물가 (CPI · PCE · PPI)", "icon": "🔥", "axis": "inflation", "importance": 8,
        "desc": "미국 월간·연간 인플레이션 발표치 구간별 확률 — 내재 기대값과 24시간 변화",
        "include": [r"inflation-us-(monthly|annual)", r"^core-cpi-", r"^core-pce-", r"^pce-", r"^ppi-",
                    r"^how-high-will-inflation-get", r"^us-cpi", r"^cpi-"],
        "exclude": [],
        "max_events": 8,
        "polarity": [(r"above|over|higher|exceed|increase|rise|\bup\b", +1),
                     (r"below|under|lower|decrease|fall|\bdown\b", -1)],
        "ev_sign": [(r".", +1)],
    },
    "GROWTH": {
        "title": "성장 · 고용", "icon": "📉", "axis": "recession", "importance": 9,
        "desc": "침체 확률 · GDP · 실업률 · 고용 · ISM PMI — 값이 높을수록 침체 축↑ 로 해석되는 지표는 부호 반전",
        "include": [r"^us-recession", r"^us-gdp-growth", r"^gdp-growth-in-20", r"^(september|october|november|december|january|february|march|april|may|june|july|august)-unemployment-rate",
                    r"^unemployment-rate", r"^how-many-jobs-added", r"^ism-(manufacturing|services)-pmi",
                    r"^us-economic-state", r"jobless-claims", r"^nonfarm", r"^nfp-"],
        "exclude": [r"^(uk|canada|china|brazil|eurozone|germany|mexico|japan|india|australia)-"],
        "max_events": 8,
        "polarity": [(r"recession|contract|negative|higher|above", +1), (r"below|lower", -1)],
        "ev_sign": [(r"unemployment|jobless|recession", +1), (r"gdp|jobs-added|ism-|nonfarm|nfp", -1),
                    (r"economic-state", 0)],
    },
    "RATES": {
        "title": "시장금리 (국채 · 모기지)", "icon": "📈", "axis": "none", "importance": 6,
        "desc": "국채 수익률 고점·저점 도달 확률 사다리 — 수치가 클수록 금리 상승 베팅",
        "include": [r"treasury-yield", r"mortgage-rate"],
        "exclude": [],
        "max_events": 6,
        "polarity": [],
        "ev_sign": [(r".", 0)],
    },
    "GLOBAL_CB": {
        "title": "글로벌 중앙은행", "icon": "🌐", "axis": "none", "importance": 5,
        "desc": "ECB · BOJ · BOE · 한국은행 등 회의별 최유력 결정",
        "include": [r"^(bank-of-|ecb|reserve-bank-|central-bank-|banxico|riksbank|norges-bank|swiss-national-bank|people-?s-bank)",
                    r"^(boe|boj|boc|rba|rbnz|bok)-"],
        "exclude": [r"^fed", r"lagarde|out-as|resign|unban"],
        "max_events": 12,
        "polarity": [(r"decrease|cut|lower", +1), (r"increase|hike|raise", -1)],
        "ev_sign": [(r".", 0)],
    },
    "TRADE": {
        "title": "관세 · 무역", "icon": "🚢", "axis": "risk", "importance": 7,
        "desc": "관세 부과·완화, 무역합의, 수출통제 — 확률↑ 가 리스크↑ 인지(+) 리스크↓ 인지(−) 극성 표시",
        "include": [r"tariff", r"trade-deal|trade-war|trade-deficit", r"sec-?232|section-232", r"export-control|chip-export"],
        "exclude": [r"apple|cxmt|cuba|carney|dividend"],
        "max_events": 8,
        "polarity": [(r"lower|remove|lift|agreement|deal|exempt|pause|reduce", -1),
                     (r"tariff|increase|hit|impose|raise|deficit|control", +1)],
        "ev_sign": [(r".", +1)],
    },
    "FISCAL": {
        "title": "재정 · 정부", "icon": "🏦", "axis": "risk", "importance": 6,
        "desc": "셧다운 · 세출 공백 · 부채한도 · 세제",
        "include": [r"shutdown|appropriations-lapse|debt-ceiling|debt-limit|stimulus|corporate-tax|capital-gains"],
        "exclude": [r"california|mamdani|crypto|gambling|millionaire|billionaire"],
        "max_events": 6,
        "polarity": [(r"shutdown|lapse|default", +1)],
        "ev_sign": [(r".", 0)],
    },
    "ENERGY": {
        "title": "에너지", "icon": "🛢️", "axis": "inflation", "importance": 5,
        "desc": "WTI 도달 가격 사다리 · 원유 사상최고 · 휘발유",
        "include": [r"^what-price-will-wti", r"^will-wti-hit", r"^crude-oil-all-time-high", r"^will-gas-hit", r"brent", r"opec"],
        "exclude": [r"closes-above-on|week-of"],
        "max_events": 4,
        "polarity": [(r"all time high|above|hit", +1)],
        "ev_sign": [(r".", +1)],
    },
    "GEOPOLITICS": {
        "title": "지정학", "icon": "⚔️", "axis": "risk", "importance": 8,
        "desc": "이란·호르무즈 · 러시아-우크라이나 · 대만 — 확률↑ 가 리스크↑(+) / 완화(−)",
        "include": [r"iran", r"israel", r"hormuz", r"kharg", r"russia|ukraine|putin", r"taiwan",
                    r"china-x-|x-china", r"philippines", r"bab-el-mandeb", r"houthi", r"north-korea",
                    r"nato", r"venezuela", r"invade|invasion|ceasefire|blockade|military-clash|nuclear"],
        "exclude": [r"president-of|visit|meets|speak|out-as|nobel|award|airspace|litani|charges-hormuz-fees|hormuz-management|oman"],
        "max_events": 14,
        "polarity": [(r"end of (the )?(iranian )?blockade|returns? to normal|ceasefire|peace|truce|agreement|deal|withdraw|reopen|lift|talks|normal", -1),
                     (r"0 ships|closed|closes|invade|invasion|strike|attack|\bwar\b|blockade|escalat|clash|seize|sanction|nuclear test|launch|no longer", +1)],
        "ev_sign": [(r"ships", -1), (r".", 0)],
    },
}
GROUP_ORDER = list(GROUPS)

# 장기(일봉, interval=max) 히스토리를 받아 추이 차트를 그리는 핵심 시장.
# (이벤트 slug 정규식, 시장 라벨/질문 정규식, 표시 이름). 첫 매치 시장 하나만.
KEY_SERIES = [
    (r"^fed-decision-in-september", r"^no change", "9월 FOMC 동결"),
    (r"^fed-decision-in-september", r"^25 bps increase", "9월 FOMC 25bp 인상"),
    (r"^fed-decision-in-september", r"^25 bps decrease", "9월 FOMC 25bp 인하"),
    (r"^fed-decision-in-october", r"^no change", "10월 FOMC 동결"),
    (r"^fed-rate-hike-in-2026", r".", "2026년 내 연준 인상"),
    (r"^how-many-fed-rate-cuts-in-2026", r"^0 ", "2026년 인하 0회"),
    (r"^us-recession-by-end-of-2026", r".", "2026년 미국 침체"),
    (r"^strait-of-hormuz-traffic-returns-to-normal-by-december", r".", "호르무즈 정상화(12월까지)"),
    (r"^will-the-us-invade-iran", r".", "미국의 이란 침공(2026)"),
    (r"^us-iran-final-nuclear-deal", r"december", "미-이란 핵합의(12월까지)"),
    (r"^will-china-invade-taiwan-before-2027", r".", "중국의 대만 침공(2026)"),
    (r"^russia-x-ukraine-ceasefire-by", r"december", "러-우 휴전(12월까지)"),
]
MAX_SIGNAL_HISTORY = 70      # 1주 60분봉 히스토리를 받을 시장 수 상한 (프록시 분당 20회 고려)
SIGNAL_MIN_PROB = 0.03       # 분포형 이벤트에서 이 확률 미만 구간은 히스토리 생략

# ─────────────────────────────────────────────
# 3. Liquidity Filter (문서 5장)
# ─────────────────────────────────────────────
LIQUIDITY = {
    "A": {"volume24h": 1_000_000, "spread": 0.02},
    "B": {"volume24h": 250_000, "spread": 0.04},
}
MIN_LIQUIDITY_USD = 50_000
MAX_SPREAD = 0.05
MIN_EVENT_LIQUIDITY = 3_000     # 이벤트 유동성 하한(표시 자체를 생략)

# ─────────────────────────────────────────────
# 4. Alert 조건 (문서 17장)
# ─────────────────────────────────────────────
ALERT = {
    "dp_1h": 0.08, "dp_24h": 0.15, "z": 2.0, "pmss_min": 55, "cooldown_hours": 6,
}
DP_BANDS = [(0.05, "Noise"), (0.10, "Watch"), (0.15, "Meaningful"), (0.25, "Strong"), (9.99, "Event Shock")]
PMSS_BANDS = [(40, "Ignore"), (55, "Watch"), (70, "Interest"), (80, "Tactical Signal"),
              (90, "Strong Signal"), (101, "Event Shock")]

# ─────────────────────────────────────────────
# 5. Cross-Asset Confirmation (문서 6~7장)
# ─────────────────────────────────────────────
ASSETS = {
    "ZT=F": "US 2Y Note Fut", "^TNX": "US 10Y Yield", "TLT": "TLT", "DX-Y.NYB": "DXY",
    "^VIX": "VIX", "NQ=F": "NASDAQ Fut", "SMH": "SMH", "GLD": "Gold", "CL=F": "WTI",
    "HYG": "HY Credit", "KRW=X": "USDKRW", "EWY": "Korea ETF",
}
EXPECTED_DIRECTION = {
    "easing":    {"ZT=F": +1, "TLT": +1, "DX-Y.NYB": -1, "^TNX": -1},
    "recession": {"ZT=F": +1, "TLT": +1, "^VIX": +1, "NQ=F": -1, "HYG": -1, "EWY": -1},
    "inflation": {"^TNX": +1, "TLT": -1, "GLD": +1, "DX-Y.NYB": +1},
    "risk":      {"^VIX": +1, "GLD": +1, "CL=F": +1, "KRW=X": +1, "EWY": -1, "NQ=F": -1},
    "none":      {},
}
ASSET_FLAT_THRESHOLD = {"default": 0.003, "^VIX": 0.03, "^TNX": 0.01, "CL=F": 0.01}

# ─────────────────────────────────────────────
# 6. 알림 채널 / 저장소 / 출력
#    텔레그램: 환경변수 → 없으면 telegram/config.json(bot_token, chat_id) 재사용
# ─────────────────────────────────────────────
def _telegram_fallback() -> tuple[str, str]:
    p = os.path.join(REPO_DIR, "telegram", "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        return str(cfg.get("bot_token", "")), str(cfg.get("chat_id", ""))
    except Exception:            # noqa: BLE001
        return "", ""


TELEGRAM_BOT_TOKEN = os.getenv("PMW_TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("PMW_TG_CHAT", "")
# 알파노트 봇(telegram/config.json) 재사용은 명시적으로 켰을 때만 (PMW_TG_FALLBACK=1).
# 기본은 꺼짐 — 대시보드만 갱신하고 알림은 로그로만 남긴다.
if not TELEGRAM_BOT_TOKEN and os.getenv("PMW_TG_FALLBACK", "") == "1":
    TELEGRAM_BOT_TOKEN, _c = _telegram_fallback()
    TELEGRAM_CHAT_ID = TELEGRAM_CHAT_ID or _c

TEMPLATE_HTML = os.path.join(BASE_DIR, "template.html")
HTML_OUT = os.getenv("PMW_HTML", os.path.join(DOCS_DIR, "polymarket.html"))
SUMMARY_JSON = os.path.join(DATA_DIR, "polymarket_summary.json")
HISTORY_JSON = os.path.join(DATA_DIR, "polymarket_history.json")   # 실행마다 누적(레짐·핵심 확률)
STATE_JSON = os.path.join(DATA_DIR, "polymarket_state.json")       # 알림 cooldown
HISTORY_MAX_ROWS = 4000
