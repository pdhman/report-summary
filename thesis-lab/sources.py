# -*- coding: utf-8 -*-
"""
thesis-lab 데이터 소스 — 기존 수집 자산을 한 곳에서 읽는다.

  퀀트데이터 스냅샷 (quant-data/output/퀀트데이터_YYYYMMDD.csv)
      → 현재 + 4주 전 스냅샷으로 EPS(E)·영업이익(E) 추정치 변화(revision)
  네이버 모바일 API
      → 분기 실적 6개(실적 5 + 컨센서스 1) : 마진 추이·증분마진·실적 레버리지
      → 투자자별 순매수 20일 : 외국인·기관 수급, 거래량 비율
      → 종목 뉴스 헤드라인 7일 : 재료 키워드
  리포트서머리.xlsx (증권사 리포트, 일별 시트)
      → 60일 리포트 : 건수·목표가 변화·요약문 → 증거 계층 자동 태깅
  x-monitor/reports/*.md · briefs/*.md · blog/*.html
      → 최근 14일 매크로/주도섹터 텍스트에서 산업 키워드 문장 추출

모든 네트워크 호출은 cache/ 아래 JSON 으로 캐시(TTL 별도).
"""
from __future__ import annotations

import datetime as dt
import glob
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.normpath(os.path.join(BASE, ".."))
QUANT_OUT = os.path.join(PROJ, "quant-data", "output")
CACHE = os.path.join(BASE, "cache")
REPORT_XLSX = os.path.join(PROJ, "리포트서머리.xlsx")
TEXT_DIRS = {
    "X모니터": (os.path.join(PROJ, "x-monitor", "reports"), "*.md"),
    "시황브리핑": (os.path.join(PROJ, "briefs"), "*.md"),
    "주도섹터블로그": (os.path.join(PROJ, "blog"), "*.html"),
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}


def log(msg: str):
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "N/A", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ 캐시
def _cache_path(kind: str, key: str) -> str:
    d = os.path.join(CACHE, kind)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{key}.json")


def _cache_get(kind: str, key: str, ttl_days: float):
    p = _cache_path(kind, key)
    if not os.path.exists(p):
        return None
    if (time.time() - os.path.getmtime(p)) > ttl_days * 86400:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(kind: str, key: str, obj):
    with open(_cache_path(kind, key), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _get_json(url: str, retries: int = 2):
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (404, 400):
                return None
        except Exception:
            pass
        time.sleep(0.6 * (i + 1))
    return None


def _fetch_many(codes, fn, kind, ttl, workers=6, label=""):
    """codes 각각에 fn(code) 를 적용해 dict 반환. 캐시 우선, 미스만 병렬 수신."""
    out, todo = {}, []
    for c in codes:
        v = _cache_get(kind, c, ttl)
        if v is not None:
            out[c] = v
        else:
            todo.append(c)
    if todo:
        log(f"  {label} 수신 {len(todo)}종목 (캐시 {len(out)})")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fn, c): c for c in todo}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    v = fut.result()
                except Exception:
                    v = None
                if v is not None:
                    out[c] = v
                    _cache_put(kind, c, v)
                done += 1
                if done % 200 == 0:
                    log(f"    {label} 진행 {done}/{len(todo)}")
    return out


# ------------------------------------------------------------------ 퀀트데이터 스냅샷
def list_snapshots() -> list[tuple[str, str]]:
    """[(YYYYMMDD, path)] 오름차순. '_new' 등 변형 파일은 제외."""
    out = []
    for p in glob.glob(os.path.join(QUANT_OUT, "퀀트데이터_2*.csv")):
        m = re.fullmatch(r"퀀트데이터_(\d{8})\.csv", os.path.basename(p))
        if m:
            out.append((m.group(1), p))
    return sorted(out)


def load_snapshot(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"코드": str}, encoding="utf-8-sig")
    df["코드"] = df["코드"].str.zfill(6)
    return df


def load_current_and_prior(min_gap_days: int = 26):
    """최신 스냅샷과 그로부터 min_gap_days 이상 이전의 가장 가까운 스냅샷."""
    snaps = list_snapshots()
    if not snaps:
        raise FileNotFoundError("quant-data/output/퀀트데이터_YYYYMMDD.csv 가 없습니다.")
    cur_ymd, cur_path = snaps[-1]
    cur_date = dt.datetime.strptime(cur_ymd, "%Y%m%d").date()
    prior = None
    for ymd, p in reversed(snaps[:-1]):
        d = dt.datetime.strptime(ymd, "%Y%m%d").date()
        if (cur_date - d).days >= min_gap_days:
            prior = (ymd, p)
            break
    if prior is None and len(snaps) > 1:
        prior = snaps[0]
    cur = load_snapshot(cur_path)
    pri = load_snapshot(prior[1]) if prior else None
    log(f"스냅샷: 현재 {cur_ymd} ({len(cur)}종목)"
        + (f" · 비교 {prior[0]} ({len(pri)}종목)" if pri is not None else " · 비교본 없음"))
    return cur, cur_ymd, pri, (prior[0] if prior else None)


def revision_table(cur: pd.DataFrame, pri: pd.DataFrame | None) -> pd.DataFrame:
    """종목별 컨센서스 변화율(%): EPS(E), 영업이익(E), 매출액(E). 비교본 없으면 NaN."""
    cols = {"EPS(E,원)": "EPS추정변화(%)", "영업이익(E,억)": "OP추정변화(%)",
            "매출액(E,억)": "매출추정변화(%)"}
    out = pd.DataFrame({"코드": cur["코드"]})
    if pri is None:
        for v in cols.values():
            out[v] = float("nan")
        return out
    p = pri.set_index("코드")
    for src, dst in cols.items():
        a = pd.to_numeric(cur[src], errors="coerce").values
        b = pd.to_numeric(cur["코드"].map(p[src]) if src in p.columns else pd.Series(index=cur.index), errors="coerce").values
        chg = pd.Series((a - b) / pd.Series(b).abs().values * 100).replace([float("inf"), float("-inf")], float("nan"))
        # 부호가 바뀌는 경우(적자→흑자)는 변화율이 의미 없어 부호만 남긴다
        sign_flip = (pd.Series(a) > 0) & (pd.Series(b) <= 0)
        chg[sign_flip] = 999.0
        sign_flip2 = (pd.Series(a) <= 0) & (pd.Series(b) > 0)
        chg[sign_flip2] = -999.0
        out[dst] = chg.round(1).values
    return out


# ------------------------------------------------------------------ 네이버: 분기 실적
def _fetch_quarter(code: str):
    j = _get_json(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter")
    if not j or "financeInfo" not in j:
        return None
    fi = j["financeInfo"]
    titles = [(t["key"], t["title"].rstrip("."), t.get("isConsensus") == "Y")
              for t in fi.get("trTitleList", [])]
    rows = {}
    for row in fi.get("rowList", []):
        rows[row["title"]] = {k: _num(v.get("value")) for k, v in row["columns"].items()}
    return {"periods": titles, "rows": rows}


def fetch_quarters(codes, ttl_days=5.0):
    return _fetch_many(codes, _fetch_quarter, "quarter", ttl_days, label="분기실적")


def quarter_metrics(q: dict | None) -> dict:
    """분기 데이터 → 마진 추이·증분마진·실적 레버리지·차기 실적 시점.

    분기 키 예: 202506..202606 (실적), 202609 (컨센서스).
    """
    r = {"q_periods": [], "q_rev": [], "q_op": [], "q_opm": [],
         "opm_now": None, "opm_yoy_pp": None, "opm_qoq_pp": None,
         "rev_yoy": None, "op_yoy": None, "incr_margin": None, "op_leverage": None,
         "next_q": None, "next_q_op": None, "next_q_op_yoy": None,
         "op_accel": None}
    if not q:
        return r
    rows = q["rows"]
    rev = rows.get("매출액", {}); op = rows.get("영업이익", {}); opm = rows.get("영업이익률", {})
    actual = [(k, t) for k, t, isc in q["periods"] if not isc]
    cons = [(k, t) for k, t, isc in q["periods"] if isc]
    actual.sort()
    keys = [k for k, _ in actual]
    r["q_periods"] = [t for _, t in actual]
    r["q_rev"] = [rev.get(k) for k in keys]
    r["q_op"] = [op.get(k) for k in keys]
    r["q_opm"] = [opm.get(k) for k in keys]
    if not keys:
        return r
    last = keys[-1]
    r["opm_now"] = opm.get(last)
    # 전년동기: YYYYMM - 100
    ya = f"{int(last[:4]) - 1}{last[4:]}"
    if ya in keys:
        if opm.get(last) is not None and opm.get(ya) is not None:
            r["opm_yoy_pp"] = round(opm[last] - opm[ya], 2)
        rv, rp, ov, opv = rev.get(last), rev.get(ya), op.get(last), op.get(ya)
        if rv is not None and rp not in (None, 0):
            r["rev_yoy"] = round((rv - rp) / abs(rp) * 100, 1)
        if ov is not None and opv not in (None, 0):
            r["op_yoy"] = round((ov - opv) / abs(opv) * 100, 1) if opv > 0 else None
        if None not in (rv, rp, ov, opv) and (rv - rp) != 0:
            r["incr_margin"] = round((ov - opv) / (rv - rp) * 100, 1)
        if r["rev_yoy"] not in (None, 0) and r["op_yoy"] is not None and r["rev_yoy"] > 0:
            r["op_leverage"] = round(r["op_yoy"] / r["rev_yoy"], 2)
    if len(keys) >= 2:
        prev = keys[-2]
        if opm.get(last) is not None and opm.get(prev) is not None:
            r["opm_qoq_pp"] = round(opm[last] - opm[prev], 2)
    # 영업이익 YoY 가속: 최근 분기 YoY vs 직전 분기 YoY (둘 다 있어야)
    if len(keys) >= 2:
        prev = keys[-2]
        pya = f"{int(prev[:4]) - 1}{prev[4:]}"
        if pya in keys and op.get(pya) not in (None, 0) and op.get(prev) is not None and op[pya] > 0 \
                and r["op_yoy"] is not None:
            prev_yoy = (op[prev] - op[pya]) / abs(op[pya]) * 100
            r["op_accel"] = round(r["op_yoy"] - prev_yoy, 1)
    if cons:
        ck, ct = cons[0]
        r["next_q"] = ct
        r["next_q_op"] = op.get(ck)
        cya = f"{int(ck[:4]) - 1}{ck[4:]}"
        if op.get(ck) is not None and op.get(cya) not in (None, 0) and op[cya] > 0:
            r["next_q_op_yoy"] = round((op[ck] - op[cya]) / abs(op[cya]) * 100, 1)
    return r


def next_earnings_window(last_actual_period: str | None, today: dt.date) -> tuple[str | None, int | None]:
    """마지막 실적 분기(예: '2026.06') → 다음 분기 실적 발표 예상 구간과 남은 일수.

    분기 종료 후 약 30~45일(잠정실적·분기보고서)로 근사한다.
    """
    if not last_actual_period:
        return None, None
    try:
        y, m = int(last_actual_period[:4]), int(last_actual_period[5:7])
    except ValueError:
        return None, None
    # 다음 분기 말
    m2 = m + 3; y2 = y
    if m2 > 12:
        m2 -= 12; y2 += 1
    q_end = dt.date(y2, m2, 28)
    start = q_end + dt.timedelta(days=30)
    end = q_end + dt.timedelta(days=45)
    # 이미 지났으면 그 다음 분기
    while end < today:
        m2 += 3
        if m2 > 12:
            m2 -= 12; y2 += 1
        q_end = dt.date(y2, m2, 28)
        start = q_end + dt.timedelta(days=30); end = q_end + dt.timedelta(days=45)
    label = f"{y2}년 {(m2 - 1) // 3 + 1}Q 실적 ({start:%m/%d}~{end:%m/%d})"
    return label, (start - today).days


# ------------------------------------------------------------------ 네이버: 투자자 수급
def _fetch_trend(code: str):
    j = _get_json(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=20")
    if not isinstance(j, list) or not j:
        return None
    out = []
    for x in j:
        out.append({"d": x.get("bizdate"),
                    "f": _num(x.get("foreignerPureBuyQuant")),
                    "i": _num(x.get("organPureBuyQuant")),
                    "p": _num(x.get("individualPureBuyQuant")),
                    "v": _num(x.get("accumulatedTradingVolume")),
                    "c": _num(x.get("closePrice")),
                    "fr": _num(str(x.get("foreignerHoldRatio", "")).replace("%", ""))})
    return out


def fetch_trends(codes, ttl_days=1.0):
    return _fetch_many(codes, _fetch_trend, "trend", ttl_days, label="투자자수급")


def trend_metrics(t: list | None) -> dict:
    r = {"fi_netbuy_days": None, "fi_net_share": None, "vol_ratio": None,
         "foreign_ratio": None, "foreign_ratio_chg": None, "fi_days": 0}
    if not t:
        return r
    t = sorted(t, key=lambda x: x["d"] or "")
    fi = [(x["f"] or 0) + (x["i"] or 0) for x in t]
    vols = [x["v"] for x in t if x["v"]]
    r["fi_days"] = len(fi)
    r["fi_netbuy_days"] = sum(1 for v in fi if v > 0)
    tot_vol = sum(vols) if vols else 0
    if tot_vol:
        r["fi_net_share"] = round(sum(fi) / tot_vol * 100, 2)    # 순매수/누적거래량 (%)
    if len(vols) >= 10:
        recent = vols[-5:]
        r["vol_ratio"] = round((sum(recent) / len(recent)) / (sum(vols) / len(vols)), 2)
    frs = [x["fr"] for x in t if x["fr"] is not None]
    if frs:
        r["foreign_ratio"] = frs[-1]
        r["foreign_ratio_chg"] = round(frs[-1] - frs[0], 2)
    return r


# ------------------------------------------------------------------ 네이버: 종목 뉴스
def _fetch_news(code: str):
    j = _get_json(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=30&page=1")
    if not isinstance(j, list):
        return None
    out = []
    for grp in j:
        for it in grp.get("items", []):
            out.append({"dt": it.get("datetime"), "src": it.get("officeName"),
                        "title": html.unescape(str(it.get("title") or "")).strip(),
                        "url": it.get("mobileNewsUrl")})
    return out


def fetch_news(codes, ttl_days=1.0):
    return _fetch_many(codes, _fetch_news, "news", ttl_days, workers=4, label="종목뉴스")


# ------------------------------------------------------------------ 증권사 리포트
def load_reports(days: int = 60, today: dt.date | None = None) -> pd.DataFrame:
    """리포트서머리.xlsx 의 dt_YYYYMMDD 시트를 days 일 범위로 합친다."""
    today = today or dt.date.today()
    if not os.path.exists(REPORT_XLSX):
        log("리포트서머리.xlsx 없음 — 리포트 증거 생략")
        return pd.DataFrame(columns=["코드", "기업명", "투자의견", "목표주가", "리포트주가",
                                     "제목", "요약", "증권사", "날짜"])
    xl = pd.ExcelFile(REPORT_XLSX)
    frames = []
    for s in xl.sheet_names:
        m = re.fullmatch(r"dt_(\d{8})", s)
        if not m:
            continue
        d = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        if (today - d).days > days:
            continue
        df = pd.read_excel(xl, s)
        if df.empty or "기업명" not in df.columns:
            continue
        df["날짜"] = d.isoformat()
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["코드", "기업명", "투자의견", "목표주가", "리포트주가",
                                     "제목", "요약", "증권사", "날짜"])
    R = pd.concat(frames, ignore_index=True)
    R["코드"] = R["기업명"].astype(str).str.extract(r"\((\d{6})\)")[0]
    R["기업명"] = R["기업명"].astype(str).str.replace(r"\s*\(\d{6}\)", "", regex=True).str.strip()
    R["요약"] = R["요약"].fillna("").astype(str).map(html.unescape).str.replace(r"\s+", " ", regex=True)
    R["증권사"] = R["요약"].str.extract(r"^\[([^\]]+)\]")[0].fillna("")
    R["요약"] = R["요약"].str.replace(r"^\[[^\]]+\]\s*", "", regex=True)
    R["목표주가"] = pd.to_numeric(R["목표주가"], errors="coerce")
    R["리포트주가"] = pd.to_numeric(R.get("전일수정주가"), errors="coerce")
    R["제목"] = R["제목"].fillna("").astype(str).map(html.unescape)
    R["투자의견"] = R["투자의견"].fillna("").astype(str)
    R = R[R["코드"].notna()].copy()
    R = R.drop_duplicates(subset=["코드", "증권사", "제목", "날짜"])
    log(f"리포트: {len(R)}건 / {R['코드'].nunique()}종목 (최근 {days}일)")
    return R[["코드", "기업명", "투자의견", "목표주가", "리포트주가", "제목", "요약", "증권사", "날짜"]]


# 증거 계층 자동 태깅 (제목+요약 키워드) — 높은 계층이 우선
EVIDENCE_RULES = [
    (5, "데이터", ["수주", "계약", "수출", "판가", "ASP", "가격 인상", "가격 상승", "단가",
                 "리드타임", "가동률", "출하", "증설", "CAPEX", "capex", "설비투자", "백로그",
                 "수주잔고", "점유율", "출하량", "판매량", "물량", "운임", "마진 상승", "스프레드"]),
    (4, "고객사", ["고객사", "고객", "공급 계약", "납품", "채택", "벤더", "승인", "퀄", "인증",
                 "발주", "주문"]),
    (3, "산업·경쟁사", ["업황", "산업", "업계", "공급 부족", "쇼티지", "타이트", "경쟁사",
                     "피어", "사이클", "업사이클", "슈퍼사이클", "구조적"]),
    (1, "회사 주장", ["가이던스", "회사는", "컨퍼런스콜", "IR", "경영진", "목표를 제시"]),
]


def tag_evidence(text: str) -> tuple[int, str]:
    t = text or ""
    for lvl, name, kws in EVIDENCE_RULES:
        if any(k in t for k in kws):
            return lvl, name
    return 2, "애널리스트"


# 재료 키워드 추출용 불용어
_STOP = set("""
영업이익 매출액 매출 실적 전망 예상 기대 목표주가 투자의견 유지 상향 하향 매수 Buy BUY 컨센서스 부합 상회 하회
기준 대비 증가 감소 성장 개선 확대 지속 전년 동기 분기 반기 연간 이익 순이익 가치 밸류에이션 주가 기업 회사 시장
수준 규모 전환 진입 시점 구간 관련 통해 위한 대한 따라 위해 따른 이후 현재 향후 최근 올해 내년 하반기 상반기 억원 조원 억 원
것으로 있다 있는 하는 된다 되는 한다 이는 그리고 또한 다만 하지만 경우 때문 가능 필요 주요 핵심 중심 판단 평가 반영 감안 제시
YoY QoQ OPM OP EPS PER PBR ROE 1Q 2Q 3Q 4Q 1Q26 2Q26 3Q26 4Q26 2026 2027 2026년 2027년 26E 27E
분석 리서치 리포트 증권 코멘트 업데이트 신규 커버리지 개시 종목 사업 부문 실적은 실적이 영업이익은 매출은 이익은
목표주가 목표가 코스피 코스닥 Review Preview 기록 상승 하락 강세 약세 출발 주목 추가 많은 넘어 기반 업체 개선세 저평가
서울데이터랩 거래상위 마감 급등 급락 특징주 마감시황 시황 장중 오전 오후 이날 지난 전날 오늘 내일 이번 다음 대표 최대 최고
Q25 Q26 Q27 1Q25 2Q25 3Q25 4Q25 FY26 FY27 YTD MoM yoy qoq 억원 천억 조원 만원 자회사 지분 관련주 종목들 투자 매매 순매수 순매도 외국인 기관 개인
별도 연결 긍정적 부정적 견조 동반 효과 기대치 국내 해외 레벨 올라간 역대급 사업부 불확실성 회복 본업 업종 섹터 모멘텀 방향 흐름 상황 결과 영향
삼전닉스 삼전 닉스 하이닉스 삼성전자 대형주 중소형주 시총 시가총액 거래대금 거래량 신고가 신저가 상한가 하한가 매수세 매도세 차익실현 반등 조정
""".split())


_PARTICLE = re.compile(r"(에서|으로|에게|까지|부터|보다|한다|했다|하며|하고|되며|은|는|이|가|을|를|의|에|로|과|와|도|만|께|한|된|될)$")


def extract_keywords(texts: list[str], top: int = 12, exclude: set[str] | None = None) -> list[tuple[str, int]]:
    """한글·영문 명사성 토큰 빈도 상위. 단순 규칙 기반(형태소 분석기 없음).

    exclude 에는 멤버 종목명 등 '재료'가 아닌 고유명사를 넣어 제거한다.
    """
    cnt: dict[str, int] = {}
    ex = {e for e in (exclude or set()) if len(e) >= 2}
    for t in texts:
        for w in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9\-]+|[가-힣]{2,}", t or ""):
            if w in _STOP:
                continue
            w0 = _PARTICLE.sub("", w) if len(w) > 2 else w
            if len(w0) < 2 or w0 in _STOP or w0.isdigit() or w0 in ex:
                continue
            if any(w0 in e or e in w0 for e in ex):
                continue
            cnt[w0] = cnt.get(w0, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])[:top]


# ------------------------------------------------------------------ 텍스트 소스(내러티브)
def _strip_html(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(re.sub(r"\s+", " ", s))


def load_narratives(days: int = 14, today: dt.date | None = None) -> list[dict]:
    """최근 days 일의 X모니터·시황브리핑·주도섹터블로그 본문을 문장 단위로."""
    today = today or dt.date.today()
    docs = []
    for src, (d, pat) in TEXT_DIRS.items():
        for p in glob.glob(os.path.join(d, pat)):
            m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", os.path.basename(p))
            if not m:
                continue
            date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if (today - date).days > days:
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                continue
            text = _strip_html(raw) if p.endswith(".html") else raw
            text = re.sub(r"\[원문\]\([^)]*\)|https?://\S+|[*#>`]", " ", text)
            for sent in re.split(r"(?<=[.。!?다])\s+|\n+", text):
                s = sent.strip()
                if 20 <= len(s) <= 400:
                    docs.append({"src": src, "date": date.isoformat(), "text": s})
    log(f"내러티브 문장: {len(docs)}개 (최근 {days}일, {len(TEXT_DIRS)}개 소스)")
    return docs


def _alias_pattern(keywords: list[str]):
    """짧은(≤3자) 한글 키워드는 앞에 한글이 붙지 않을 때만 매치('보유가' 의 '유가' 방지)."""
    parts = []
    for k in keywords:
        e = re.escape(k)
        parts.append(f"(?<![가-힣]){e}" if len(k) <= 3 and re.search(r"[가-힣]", k) else e)
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!x)x")


def search_narratives(docs: list[dict], keywords: list[str], limit: int = 8) -> list[dict]:
    pat = _alias_pattern(keywords)
    hits = []
    for d in docs:
        if pat.search(d["text"]):
            hits.append(d)
    hits.sort(key=lambda x: x["date"], reverse=True)
    # 같은 문장 중복 제거
    seen, out = set(), []
    for h in hits:
        key = h["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ DART 공시 (수주·공급계약 → L5 증거)
DART_API = "https://opendart.fss.or.kr/api"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}"
# 거래소 수시공시 중 '실제 주문'에 해당하는 것만 고른다
_CONTRACT_PAT = re.compile(r"공급계약|수주|판매ㆍ공급|판매·공급")
_MGMT_PAT = re.compile(r"주요경영사항|기타\s*경영사항")
_MGMT_KW = re.compile(r"수주|공급|계약|납품|발주|주문|채택|승인")


def dart_key() -> str:
    p = os.path.join(PROJ, "secrets.json")
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("dart_api_key") or "").strip()
    except Exception:
        return ""


def _dart_list_day(key: str, ymd: str) -> list[dict]:
    """하루치 거래소공시 목록(list.json, pblntf_ty=I). 100건씩 페이지 순회."""
    out, page = [], 1
    while page <= 20:
        try:
            r = requests.get(f"{DART_API}/list.json",
                             params={"crtfc_key": key, "bgn_de": ymd, "end_de": ymd, "pblntf_ty": "I",
                                     "page_count": 100, "page_no": page}, timeout=20)
            j = r.json()
        except Exception:
            break
        if j.get("status") not in ("000", "013"):        # 013 = 조회 결과 없음
            log(f"  DART list {ymd} 오류: {j.get('status')} {j.get('message')}")
            break
        items = j.get("list") or []
        out.extend({"code": str(x.get("stock_code") or "").strip(), "name": x.get("corp_name"),
                    "nm": re.sub(r"\s+", " ", str(x.get("report_nm") or "")).strip(),
                    "rcept": x.get("rcept_no"), "d": x.get("rcept_dt")} for x in items)
        if page >= int(j.get("total_page") or 1):
            break
        page += 1
        time.sleep(0.15)
    return out


def _dart_doc_text(key: str, rcept: str) -> str | None:
    import io
    import zipfile
    try:
        r = requests.get(f"{DART_API}/document.xml", params={"crtfc_key": key, "rcept_no": rcept}, timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception:
        return None
    t = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(t))


def _won(s):
    v = _num(s)
    return None if v is None else round(v / 1e8, 1)          # 원 → 억원


def parse_contract(text: str) -> dict:
    """단일판매ㆍ공급계약체결 본문 → 내용·금액·매출대비·상대방·기간. 정정공시는 마지막 표 기준."""
    r = {"content": None, "amount": None, "recent_rev": None, "rev_ratio": None,
         "counterpart": None, "start": None, "end": None, "order_date": None, "conditional": None}
    if not text:
        return r
    # 코스닥 양식: "1. 판매ㆍ공급계약 내용 X 2. 계약내역 ... 계약금액 총액(원) N 최근 매출액(원) N 매출액 대비(%) N 3. 계약상대방 Y"
    # 코스피 양식: "1. 판매ㆍ공급계약 구분 공사수주 - 체결계약명 X 2. 계약내역 계약금액(원) N 최근매출액(원) N 매출액대비(%) N ... 3. 계약상대 Y"
    # 정정공시는 정정 전·후 표가 반복되므로 마지막 표(rfind) 기준.
    i = max(text.rfind("1. 판매ㆍ공급계약 내용"), text.rfind("1. 판매ㆍ공급계약 구분"))
    if i < 0:
        i = max(text.rfind("판매ㆍ공급계약 내용"), text.rfind("체결계약명"))
    body = text[i:] if i >= 0 else text
    m = re.search(r"판매ㆍ공급계약 내용\s*(.+?)\s*2\. 계약내역", body)
    if m:
        r["content"] = m.group(1).strip()[:120]
    else:
        m = re.search(r"판매ㆍ공급계약 구분\s*(.+?)\s*-?\s*체결계약명\s*(.+?)\s*2\. 계약내역", body)
        if m:
            r["content"] = f"[{m.group(1).strip(' -')}] {m.group(2).strip()}"[:120]
    m = re.search(r"계약금액\s*(?:총액)?\s*\(원\)\s*([\d,]+)", body)
    if m:
        r["amount"] = _won(m.group(1))
    m = re.search(r"최근\s*매출액\s*\(원\)\s*([\d,]+)", body)
    if m:
        r["recent_rev"] = _won(m.group(1))
    m = re.search(r"매출액\s*대비\s*\(%\)\s*([\d.,]+)", body)
    if m:
        r["rev_ratio"] = _num(m.group(1))
    m = re.search(r"3\. 계약상대(?:방)?\s*(.+?)\s*(?:-\s*)?(?:최근 매출액|주요사업|회사와의 관계|4\. 판매ㆍ공급지역)", body)
    if m:
        cp = m.group(1).strip(" -")
        r["counterpart"] = cp[:60] if cp else None
    m = re.search(r"5\. 계약기간\s*시작일\s*(\d{4}-\d{2}-\d{2})\s*종료일\s*(\d{4}-\d{2}-\d{2})", body)
    if m:
        r["start"], r["end"] = m.group(1), m.group(2)
    m = re.search(r"계약\(수주\)일자\s*(\d{4}-\d{2}-\d{2})", body)
    if m:
        r["order_date"] = m.group(1)
    m = re.search(r"조건부 계약여부\s*(해당|미해당)", body)
    if m:
        r["conditional"] = m.group(1) == "해당"
    return r


def load_dart_contracts(days: int = 60, today: dt.date | None = None) -> list[dict]:
    """최근 days 일 거래소공시 중 수주·공급계약 공시. 일자별 목록·건별 본문을 캐시한다.

    반환 항목: code, name, d(YYYY-MM-DD), rcept, nm(공시명), kind('공급계약'|'경영사항'),
              corrected(기재정정), url, + parse_contract 필드
    """
    today = today or dt.date.today()
    key = dart_key()
    if not key:
        log("DART 키 없음(secrets.json dart_api_key) — 공시 증거 생략")
        return []
    hits = []
    fetched_days = 0
    for k in range(days + 1):
        d = today - dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        ymd = d.strftime("%Y%m%d")
        ttl = 0.25 if k <= 1 else 3650          # 오늘·어제는 6시간, 과거는 영구
        lst = _cache_get("dart_list", ymd, ttl)
        if lst is None:
            lst = _dart_list_day(key, ymd)
            _cache_put("dart_list", ymd, lst)
            fetched_days += 1
            time.sleep(0.1)
        for x in lst:
            nm = x["nm"]
            if not x["code"]:
                continue
            if _CONTRACT_PAT.search(nm):
                kind = "공급계약"
            elif _MGMT_PAT.search(nm) and _MGMT_KW.search(nm):
                kind = "경영사항"
            else:
                continue
            hits.append({**x, "kind": kind, "corrected": "정정" in nm,
                         "d": f"{x['d'][:4]}-{x['d'][4:6]}-{x['d'][6:]}",
                         "url": DART_VIEW.format(rcept=x["rcept"])})
    # 본문 파싱 (공급계약만) — 원문 텍스트를 건별 영구 캐시하고 파싱은 매번 한다(파서 개선 즉시 반영)
    n_doc = 0
    for h in hits:
        if h["kind"] != "공급계약":
            continue
        raw = _cache_get("dart_doc", h["rcept"], 3650)
        if raw is None or not isinstance(raw, dict) or "text" not in raw:
            txt = _dart_doc_text(key, h["rcept"]) or ""
            raw = {"text": txt[:6000]}
            _cache_put("dart_doc", h["rcept"], raw)
            n_doc += 1
            time.sleep(0.1)
        h.update(parse_contract(raw["text"]))
    hits.sort(key=lambda x: x["d"], reverse=True)
    log(f"DART 수주·공급계약 공시: {len(hits)}건 / {len({h['code'] for h in hits})}종목 "
        f"(최근 {days}일, 목록 수신 {fetched_days}일 · 본문 수신 {n_doc}건)")
    return hits
