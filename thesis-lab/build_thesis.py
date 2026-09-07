# -*- coding: utf-8 -*-
"""
thesis-lab 빌더 — "현상 → 원인 → 산업 확인 → 병목 → 증거 → 실적 전달 → 상대 비교
→ 주가 위치 → 촉매/반증" 9단계 프레임워크를 데이터로 채운다.

파이프라인
  1. 관찰(Observation)   : 퀀트데이터 최신 스냅샷 → 종목 종합 RS → 업종·테마 집계
                           (RS, 4주 RS 변화, 확산도, 컨센서스 이익 성장, EPS 상향 비율)
  2. 재료(Catalyst)      : 주도 그룹 멤버의 증권사 리포트·뉴스 헤드라인·매크로 텍스트에서
                           키워드 추출 + 증거 계층(회사/애널/산업/고객/데이터) 자동 태깅
  3. 실적 전달(Earnings) : 분기 실적 → 마진 추이·증분마진·영업레버리지·EPS 추정치 변화
  4. 상대 비교(Winner)   : 업종 내 매출 순위·레버리지·가시성·밸류 → Core / Beta 분류
  5. 주가·수급(Price)    : 52주 고점 대비·MA200·외국인+기관 20일 순매수·거래량 비율
  6. 촉매·스코어         : 다음 실적 발표 D-day, 15항목 30점 자동 채점

출력
  ../docs/thesis_data.js        (window.THESIS_DATA = {...})
  output/스코어_YYYYMMDD.csv     종목별 자동 점수 — 실행일 기준 누적(점수 히스토리 원천)
  output/스코어_latest.csv

실행:  python build_thesis.py              # 전체
       python build_thesis.py --offline    # 네트워크 없이 캐시·CSV 만으로
       python build_thesis.py --top 10     # 심화 분석 그룹 수(업종·테마 각각)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys

import pandas as pd

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "quant-data"))
import sources as S                                   # noqa: E402
from scoring import ITEMS, MANUAL_ITEMS, score_company, classify_core_beta  # noqa: E402

BASE = S.BASE
OUT_DIR = os.path.join(BASE, "output")
DOCS = os.path.join(S.PROJ, "docs")
OUT_JS = os.path.join(DOCS, "thesis_data.js")

RS_WEIGHTS = {"RS_1개월": 0.2, "RS_3개월": 0.4, "RS_6개월": 0.2, "RS_1년": 0.2}
IND_MIN = 3            # 업종 최소 종목수
THEME_MIN = 5          # 테마 최소 종목수
THEME_KEEP = 40        # 테마 랭킹 상위 N 만 유지
MEMBERS_MAX = 40       # 그룹당 비교 종목 상한(시총 순)
NEWS_PER_GROUP = 8     # 그룹당 뉴스 수집 종목 수(시총 순)

# 테마 중 분류 성격이 약해 재료 분석에 부적합한 것
THEME_SKIP = ("코리아 밸류업", "IT 대표주", "밸류업(", "S7(", "지주사", "기업인수목적", "SPAC",
              "환율", "우선주", "고령화", "코스닥 대표")

# 업종 → 내러티브 검색 키워드 (없는 업종은 이름을 분해해 사용)
ALIASES = {
    "반도체와반도체장비": ["반도체", "메모리", "HBM", "DRAM", "NAND", "D램", "낸드", "파운드리", "소부장"],
    "전자장비와기기": ["전자부품", "기판", "PCB", "커넥터", "MLCC", "전장"],
    "디스플레이패널": ["디스플레이", "OLED", "패널"],
    "디스플레이장비및부품": ["디스플레이", "OLED", "패널"],
    "전기장비": ["전력기기", "변압기", "전선", "ESS", "전력망", "그리드", "케이블"],
    "조선": ["조선", "선박", "LNG선", "컨테이너선", "수주잔고", "MRO"],
    "기계": ["기계", "공작기계", "로봇", "건설기계"],
    "건설": ["건설", "주택", "분양", "SOC", "원전", "플랜트"],
    "건축자재": ["건자재", "시멘트", "레미콘"],
    "우주항공과국방": ["방산", "우주", "항공", "미사일", "K-방산", "폴란드", "수출 계약"],
    "자동차": ["자동차", "완성차", "현대차", "기아", "전기차", "관세"],
    "자동차부품": ["자동차부품", "전장", "차부품", "전기차"],
    "화학": ["화학", "석유화학", "에틸렌", "NCC", "스프레드"],
    "에너지": ["정유", "정제마진", "유가", "브렌트", "WTI", "석유", "가스"],
    "석유와가스": ["정유", "정제마진", "유가", "브렌트", "WTI", "석유", "가스"],
    "철강": ["철강", "열연", "후판", "철광석", "포스코"],
    "비철금속": ["비철", "구리", "알루미늄", "니켈", "아연", "전기동"],
    "은행": ["은행", "금융지주", "NIM", "밸류업", "배당"],
    "증권": ["증권", "브로커리지", "거래대금", "IB"],
    "보험": ["보험", "손보", "생보", "K-ICS"],
    "제약": ["제약", "신약", "바이오", "임상", "FDA", "기술이전"],
    "생물공학": ["바이오", "신약", "임상", "FDA", "기술이전", "CDMO"],
    "생명과학도구및서비스": ["바이오", "CDMO", "CMO", "진단"],
    "건강관리장비와용품": ["의료기기", "미용기기", "임플란트", "에스테틱"],
    "화장품": ["화장품", "K-뷰티", "뷰티", "ODM", "인디 브랜드"],
    "식품": ["식품", "K-푸드", "라면", "음식료", "수출"],
    "음료": ["음료", "주류", "소주", "맥주"],
    "소프트웨어": ["소프트웨어", "SaaS", "AI 소프트웨어", "보안", "클라우드"],
    "IT서비스": ["IT서비스", "SI", "클라우드", "데이터센터", "AI 전환"],
    "게임엔터테인먼트": ["게임", "신작", "엔터", "K-팝", "콘서트", "앨범"],
    "양방향미디어와서비스": ["플랫폼", "네이버", "카카오", "광고", "커머스"],
    "통신서비스": ["통신", "5G", "SKT", "KT", "LG유플러스", "요금"],
    "무선통신서비스": ["통신", "5G", "SKT", "KT", "LG유플러스"],
    "전기제품": ["2차전지", "배터리", "양극재", "ESS", "전지", "리튬"],
    "해운사": ["해운", "운임", "SCFI", "BDI", "컨테이너", "벌크", "탱커"],
    "항공사": ["항공", "여객", "유류비", "환율"],
    "운송인프라": ["항만", "물류", "인프라"],
    "호텔,레스토랑,레저": ["카지노", "여행", "호텔", "면세", "중국인 관광"],
    "백화점과일반상점": ["유통", "백화점", "면세", "소비"],
    "인터넷과카탈로그소매": ["이커머스", "커머스", "플랫폼"],
    "가정용기기와용품": ["가전", "렌탈"],
    "전자제품": ["전자", "가전", "부품"],
    "통신장비": ["통신장비", "광통신", "트랜시버", "네트워크", "안테나"],
    "컴퓨터와주변기기": ["서버", "PC", "SSD", "스토리지", "데이터센터"],
    "핸드셋": ["스마트폰", "아이폰", "갤럭시", "폴더블"],
    "가스유틸리티": ["도시가스", "LNG", "가스"],
    "전기유틸리티": ["전력", "한전", "전기요금", "SMP"],
    "복합유틸리티": ["전력", "발전", "유틸리티"],
    "종이와목재": ["제지", "펄프", "목재"],
    "포장재": ["포장", "패키징"],
    "상업서비스와공급품": ["폐기물", "환경", "인력"],
    "교육서비스": ["교육", "학원", "에듀테크"],
    "건강관리업체및서비스": ["헬스케어", "병원", "의료"],
    "손해보험": ["손보", "보험", "K-ICS"],
    "생명보험": ["생보", "보험", "K-ICS"],
    "카드": ["카드", "결제"],
    "창업투자": ["벤처", "VC", "IPO"],
    "부동산": ["부동산", "리츠", "임대"],
    "섬유,의류,신발,호화품": ["의류", "OEM", "섬유", "패션"],
    "레저용장비와제품": ["레저", "골프", "캠핑"],
    "판매업체": ["유통", "도매"],
    "무역회사와판매업체": ["무역", "상사", "종합상사"],
    "복합기업": ["지주", "그룹"],
    "광고": ["광고", "미디어"],
    "방송과엔터테인먼트": ["방송", "콘텐츠", "드라마", "OTT"],
    "출판": ["출판", "웹툰", "만화"],
    "화학(신)": ["화학"],
    "전문소매": ["소매", "유통"],
    "식품과기본식료품소매": ["마트", "편의점", "유통"],
    "다각화된소비자서비스": ["소비자 서비스", "장례", "결혼"],
    "다각화된통신서비스": ["통신"],
    "생활용품": ["생활용품", "위생"],
    "가구": ["가구", "인테리어"],
    "담배": ["담배", "KT&G"],
    "사무용전자제품": ["사무기기"],
}


def _r(v, nd=1):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def composite_rs(df: pd.DataFrame) -> pd.Series:
    cols = list(RS_WEIGHTS)
    pct = df[cols].apply(pd.to_numeric, errors="coerce")
    w = pd.Series(RS_WEIGHTS)
    wsum = pct.notna().mul(w, axis=1).sum(axis=1)
    score = pct.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, math.nan)
    score[pct["RS_3개월"].isna()] = math.nan
    return score.round(1)


def aliases_for(name: str, kind: str) -> list[str]:
    if kind == "업종" and name in ALIASES:
        return ALIASES[name]
    toks = [t for t in __import__("re").split(r"[와과,/()\s]+", name) if len(t) >= 2]
    return toks or [name]


# ------------------------------------------------------------------ 그룹 집계
def group_stats(sub: pd.DataFrame, prior_rs: pd.Series | None) -> dict | None:
    s = sub[sub["RS"].notna()]
    if len(s) == 0:
        return None
    cov = s[s["covered"]]
    out = {
        "rs": _r(s["RS"].mean()), "rs_med": _r(s["RS"].median()),
        "r1": _r(s["RS_1개월"].mean()), "r3": _r(s["RS_3개월"].mean()),
        "r6": _r(s["RS_6개월"].mean()), "r12": _r(s["RS_1년"].mean()),
        "n": int(len(s)), "n_cov": int(len(cov)),
        "breadth70": _r((s["RS"] >= 70).mean() * 100),
        "p90": _r((s["RS"] >= 90).mean() * 100),
        "ret1m_med": _r(pd.to_numeric(s["1개월수익률(%)"], errors="coerce").median()),
        "op_yoy_e_med": _r(pd.to_numeric(cov["영업이익YoY(E,%)"], errors="coerce").median()) if len(cov) >= 2 else None,
        "per_e_med": _r(pd.to_numeric(cov["PER(E)"], errors="coerce").where(lambda x: x > 0).median()) if len(cov) >= 2 else None,
        "eps_up_share": None, "margin_up_share": None, "rs_chg4w": None,
        "n_rev": int(pd.to_numeric(s["매출액(억)"], errors="coerce").notna().sum()),
    }
    rev = pd.to_numeric(cov["EPS추정변화(%)"], errors="coerce").dropna()
    if len(rev) >= 2:
        out["eps_up_share"] = _r((rev > 0).mean() * 100)
    m = pd.to_numeric(s["opm_yoy_pp"], errors="coerce").dropna()
    if len(m) >= 2:
        out["margin_up_share"] = _r((m > 0).mean() * 100)
    if prior_rs is not None:
        pr = prior_rs.reindex(s["코드"]).dropna()
        if len(pr) >= max(2, len(s) * 0.5):
            out["rs_chg4w"] = _r(s.set_index("코드").loc[pr.index, "RS"].mean() - pr.mean())
    return out


# ------------------------------------------------------------------ 점수 히스토리
HIST_KEEP = 12         # 종목별 보관 스냅샷 수


def load_score_history(exclude_ymd: str) -> dict[str, list]:
    """output/스코어_YYYYMMDD.csv (실행일 기준) → 코드별 [(ymd, 총점, [항목점수 15]) ...] 오름차순.

    오늘자 파일은 지금 다시 쓰므로 제외한다. 항목 열이 없는 옛 파일은 항목을 None 으로 둔다.
    """
    import glob as _glob
    hist: dict[str, list] = {}
    for p in sorted(_glob.glob(os.path.join(OUT_DIR, "스코어_2*.csv"))):
        m = __import__("re").fullmatch(r"스코어_(\d{8})\.csv", os.path.basename(p))
        if not m or m.group(1) == exclude_ymd:
            continue
        ymd = m.group(1)
        try:
            d = pd.read_csv(p, dtype={"코드": str}, encoding="utf-8-sig")
        except Exception:
            continue
        item_cols = [f"항목{i + 1}" for i in range(len(ITEMS))]
        has_items = all(c in d.columns for c in item_cols)
        for _, r in d.iterrows():
            tot = pd.to_numeric(r.get("자동점수"), errors="coerce")
            if pd.isna(tot):
                continue
            items = None
            if has_items:
                items = [None if pd.isna(r[c]) else int(r[c]) for c in item_cols]
            hist.setdefault(str(r["코드"]).zfill(6), []).append((ymd, int(tot), items))
    S.log(f"점수 히스토리: {len(hist)}종목 · 스냅샷 {len({h[0] for v in hist.values() for h in v})}개")
    return hist


# ------------------------------------------------------------------ 메인
def build(top: int = 15, offline: bool = False) -> str:
    today = dt.date.today()
    cur, cur_ymd, pri, pri_ymd = S.load_current_and_prior()
    df = cur.copy()
    df["RS"] = composite_rs(df)
    df["covered"] = pd.to_numeric(df["EPS(E,원)"], errors="coerce").notna()
    df["is_spac"] = df["회사명"].str.contains("스팩", na=False)
    df["is_pref"] = df["결산기"].isna() & df["코드"].str[-1].ne("0")
    df = df[~df["is_spac"]].copy()

    # 추정치 변화(4주)
    rev = S.revision_table(cur, pri).set_index("코드")
    for c in ("EPS추정변화(%)", "OP추정변화(%)", "매출추정변화(%)"):
        df[c] = df["코드"].map(rev[c])

    prior_rs = None
    if pri is not None:
        p = pri.copy()
        p["RS"] = composite_rs(p)
        prior_rs = p.set_index("코드")["RS"]

    # ---------- 1차 그룹 랭킹(RS 기준)으로 심화 분석 대상 선정
    ind_groups = {n: sub for n, sub in df[df["업종"].notna()].groupby("업종") if len(sub) >= IND_MIN}
    theme_map: dict[str, list[str]] = {}
    for code, raw in zip(df["코드"], df["테마"]):
        if not isinstance(raw, str):
            continue
        for t in raw.split(";"):
            t = t.strip()
            if not t or any(k in t for k in THEME_SKIP):
                continue
            theme_map.setdefault(t, []).append(code)
    idx = df.set_index("코드")
    theme_groups = {t: idx.loc[cs].reset_index() for t, cs in theme_map.items() if len(cs) >= THEME_MIN}

    def rank_by_rs(groups):
        rows = [(n, sub["RS"].mean()) for n, sub in groups.items() if sub["RS"].notna().sum() >= 3]
        return [n for n, _ in sorted(rows, key=lambda x: -(x[1] if not math.isnan(x[1]) else -1))]

    top_ind = rank_by_rs(ind_groups)[:top]
    theme_rank = rank_by_rs(theme_groups)[:THEME_KEEP]
    top_theme = theme_rank[:top]
    theme_rank_set = set(theme_rank)
    S.log(f"업종 {len(ind_groups)}개 · 테마 {len(theme_groups)}개 → 심화 분석 업종 {len(top_ind)} · 테마 {len(top_theme)}")

    # ---------- 심화 종목 집합: 커버리지 종목 ∪ 주도 그룹 멤버(시총 상위)
    deep = set(df.loc[df["covered"], "코드"])
    news_codes = set()
    for name in top_ind:
        sub = ind_groups[name].sort_values("시가총액(억)", ascending=False)
        deep |= set(sub["코드"].head(MEMBERS_MAX))
        news_codes |= set(sub["코드"].head(NEWS_PER_GROUP))
    for name in top_theme:
        sub = theme_groups[name].sort_values("시가총액(억)", ascending=False)
        deep |= set(sub["코드"].head(MEMBERS_MAX))
        news_codes |= set(sub["코드"].head(NEWS_PER_GROUP))
    deep -= set(df.loc[df["is_pref"], "코드"])
    deep = sorted(deep)
    S.log(f"심화 분석 종목 {len(deep)} (뉴스 수집 {len(news_codes)})")

    # ---------- 외부 데이터
    if offline:
        quarters = {c: S._cache_get("quarter", c, 9999) for c in deep}
        trends = {c: S._cache_get("trend", c, 9999) for c in deep}
        news = {c: S._cache_get("news", c, 9999) for c in news_codes}
    else:
        quarters = S.fetch_quarters(deep)
        trends = S.fetch_trends(deep)
        news = S.fetch_news(sorted(news_codes))
    reports = S.load_reports(days=60, today=today)
    narratives = S.load_narratives(days=14, today=today)
    try:
        contracts = S.load_dart_contracts(days=60, today=today)
    except Exception as ex:
        S.log(f"경고: DART 공시 수집 실패 — 생략: {ex}")
        contracts = []
    dart_by_code: dict[str, list[dict]] = {}
    for h in contracts:
        dart_by_code.setdefault(h["code"], []).append(h)

    def dart_metrics(code):
        items = dart_by_code.get(code, [])
        cons = [h for h in items if h["kind"] == "공급계약"]
        ratios = [h["rev_ratio"] for h in cons if h.get("rev_ratio") is not None and not h.get("corrected")]
        amt = [h["amount"] for h in cons if h.get("amount") is not None and not h.get("corrected")]
        return {
            "dart_n60": len(items), "dart_contracts60": len(cons),
            "dart_ratio60": round(sum(ratios), 1) if ratios else None,      # 정정공시 제외 합계
            "dart_amt60": round(sum(amt), 1) if amt else None,
            "dart_max_ratio": max(ratios) if ratios else None,
            "dart": [{"d": h["d"], "nm": h["nm"][:40], "kind": h["kind"], "corr": h.get("corrected", False),
                      "content": h.get("content"), "amount": h.get("amount"), "ratio": h.get("rev_ratio"),
                      "cp": h.get("counterpart"), "start": h.get("start"), "end": h.get("end"),
                      "url": h["url"]} for h in items[:8]],
        }

    # 분기 지표를 df 에 붙인다 (그룹 마진 확대 비중 계산용)
    qm = {c: S.quarter_metrics(quarters.get(c)) for c in deep}
    df["opm_yoy_pp"] = df["코드"].map({c: m["opm_yoy_pp"] for c, m in qm.items()})

    # 업종 내 매출 순위
    df["rev_rank"] = df.groupby("업종")["매출액(억)"].rank(ascending=False, method="min")

    # 열이 추가됐으므로 그룹 서브프레임을 다시 만든다 (테마는 코드 목록으로 재구성)
    idx = df.set_index("코드")
    ind_groups = {n: sub for n, sub in df[df["업종"].notna()].groupby("업종") if len(sub) >= IND_MIN}
    theme_groups = {t: idx.loc[[c for c in cs if c in idx.index]].reset_index()
                    for t, cs in theme_map.items() if len(cs) >= THEME_MIN}

    # ---------- 그룹 통계 (모든 업종 + 상위 테마)
    groups: list[dict] = []
    gstats: dict[tuple, dict] = {}
    for name, sub in ind_groups.items():
        st = group_stats(sub, prior_rs)
        if st:
            st.update({"kind": "업종", "name": name, "deep": name in top_ind})
            gstats[("업종", name)] = st
            groups.append(st)
    for name in theme_rank:
        st = group_stats(theme_groups[name], prior_rs)
        if st:
            st.update({"kind": "테마", "name": name, "deep": name in top_theme})
            gstats[("테마", name)] = st
            groups.append(st)

    # ---------- 종목 리포트 요약
    rep_by_code: dict[str, list[dict]] = {}
    if not reports.empty:
        R = reports.sort_values("날짜", ascending=False)
        for code, sub in R.groupby("코드"):
            items = []
            for _, r in sub.iterrows():
                lvl, lname = S.tag_evidence(r["제목"] + " " + r["요약"])
                items.append({"d": r["날짜"], "br": r["증권사"], "op": r["투자의견"],
                              "tp": _r(r["목표주가"], 0), "px": _r(r["리포트주가"], 0),
                              "t": r["제목"][:80], "s": r["요약"][:220], "lv": lvl, "ln": lname})
            rep_by_code[code] = items

    def rep_metrics(code):
        items = rep_by_code.get(code, [])
        d30 = [i for i in items if (today - dt.date.fromisoformat(i["d"])).days <= 30]
        # 목표가 상향: 같은 증권사의 60일 내 이전 목표가와 비교
        up = 0
        by_br: dict[str, list] = {}
        for i in sorted(items, key=lambda x: x["d"]):
            if i["tp"]:
                by_br.setdefault(i["br"], []).append(i["tp"])
        for tps in by_br.values():
            if len(tps) >= 2 and tps[-1] > tps[0]:
                up += 1
        days_since = (today - dt.date.fromisoformat(items[0]["d"])).days if items else None
        return {"rep_n30": len(d30), "rep_n60": len(items), "rep_tp_up": up,
                "rep_days_since": days_since, "reports": items[:6]}

    # ---------- 종목 딕셔너리
    companies: dict[str, dict] = {}
    for _, row in df.iterrows():
        code = row["코드"]
        g = gstats.get(("업종", row["업종"])) if isinstance(row["업종"], str) else None
        c = {
            "code": code, "name": row["회사명"], "mkt": row["시장"], "sector": row["섹터"],
            "ind": row["업종"] if isinstance(row["업종"], str) else None,
            "themes": [t.strip() for t in str(row["테마"]).split(";")
                       if t.strip() in theme_rank_set][:10],
            "price": _r(row["주가"], 0), "mcap": _r(row["시가총액(억)"], 0), "amt": _r(row["거래대금(억)"]),
            "rs": _r(row["RS"]), "r1": _r(row["RS_1개월"]), "r3": _r(row["RS_3개월"]),
            "r6": _r(row["RS_6개월"]), "r12": _r(row["RS_1년"]),
            "ret1m": _r(row["1개월수익률(%)"]), "ret3m": _r(row["3개월수익률(%)"]),
            "off_high": _r(row["52주고점대비(%)"]), "ma200": _r(row["MA200대비(%)"]),
            "vol60": _r(row["변동성60일(%)"]),
            "fy": row["결산기"] if isinstance(row["결산기"], str) else None,
            "rev0": _r(row["매출액(억)"], 0), "op0": _r(row["영업이익(억)"], 0),
            "opm0": _r(row["영업이익률(%)"]), "roe": _r(row["ROE(%)"]), "debt": _r(row["부채비율(%)"]),
            "eps0": _r(row["EPS(원)"], 0), "eps_e": _r(row["EPS(E,원)"], 0),
            "rev_e": _r(row["매출액(E,억)"], 0), "op_e": _r(row["영업이익(E,억)"], 0),
            "rev_yoy_e": _r(row["매출YoY(E,%)"]), "op_yoy_e": _r(row["영업이익YoY(E,%)"]),
            "roe_e": _r(row["ROE(E,%)"]),
            "per": _r(row["PER"]), "per_e": _r(row["PER(E)"]), "pbr": _r(row["PBR"], 2),
            "peg_e": _r(row["PEG(E)"], 2), "dy": _r(row["배당수익률(%)"]),
            "covered": bool(row["covered"]),
            "eps_rev4w": _r(row["EPS추정변화(%)"]), "op_rev4w": _r(row["OP추정변화(%)"]),
            "rev_rev4w": _r(row["매출추정변화(%)"]),
            "rev_rank": int(row["rev_rank"]) if not pd.isna(row["rev_rank"]) else None,
            "deep": code in qm,
        }
        if code in qm:
            c.update(qm[code])
            c.update(S.trend_metrics(trends.get(code)))
            last_actual = c["q_periods"][-1] if c["q_periods"] else None
            lbl, dn = S.next_earnings_window(last_actual, today)
            c["next_earn_label"], c["next_earn_days"] = lbl, dn
        c.update(rep_metrics(code))
        c.update(dart_metrics(code))
        if code in news and news[code]:
            cutoff = (today - dt.timedelta(days=7)).strftime("%Y%m%d")
            c["news"] = [{"d": n["dt"][:8], "src": n["src"], "t": n["title"][:90], "u": n["url"]}
                         for n in news[code] if (n["dt"] or "")[:8] >= cutoff][:10]
        c["score"] = score_company(c, g or {})
        companies[code] = c

    # 점수 히스토리 (실행일 기준 누적 CSV) → 전회 대비 변화·항목별 변화
    today_ymd = today.strftime("%Y%m%d")
    hist = load_score_history(exclude_ymd=today_ymd)
    for code, c in companies.items():
        h = hist.get(code, [])
        tot = c["score"]["total"]
        c["hist"] = [[ymd, t] for ymd, t, _ in h][-(HIST_KEEP - 1):] + [[today_ymd, tot]]
        if h:
            p_ymd, p_tot, p_items = h[-1]
            c["prev_ymd"], c["score_prev"], c["score_chg"] = p_ymd, p_tot, tot - p_tot
            if p_items:
                c["item_chg"] = [[i, (it[0] or 0) - (p_items[i] or 0)]
                                 for i, it in enumerate(c["score"]["items"])
                                 if (it[0] or 0) != (p_items[i] or 0)]
            # 첫 등장일(히스토리 시작)과 최고점
            c["hist_max"] = max(t for _, t, _ in h + [(today_ymd, tot, None)])
        else:
            c["prev_ymd"], c["score_prev"], c["score_chg"] = None, None, None

    # Core/Beta (업종 피어 대비)
    for name, sub in ind_groups.items():
        peers = [companies[k] for k in sub["코드"] if k in companies]
        for p in peers:
            p["cls"] = classify_core_beta(p, peers)

    # ---------- 그룹 심화: 멤버 목록 · 증거 · 키워드 · 내러티브 · 인과사슬 초안
    def build_group_detail(g: dict, sub: pd.DataFrame):
        kind, name = g["kind"], g["name"]
        sub = sub[~sub["is_pref"]].sort_values("시가총액(억)", ascending=False)
        members = []
        for code in sub["코드"].head(MEMBERS_MAX):
            c = companies.get(code)
            if not c:
                continue
            if kind == "테마":
                sc = score_company(c, g)          # 테마 맥락으로 산업 항목 재채점
                total = sc["total"]
            else:
                total = c["score"]["total"]
            members.append({"code": code, "total": total})
        members.sort(key=lambda m: -m["total"])
        g["members"] = members
        if members:
            g["score_avg"] = round(sum(m["total"] for m in members) / len(members), 1)
            chg = [companies[m["code"]]["score_chg"] for m in members
                   if companies[m["code"]].get("score_chg") is not None]
            g["score_avg_chg"] = round(sum(chg) / len(chg), 1) if len(chg) >= max(2, len(members) * 0.5) else None
            g["n_up"] = sum(1 for x in chg if x > 0)
            g["n_down"] = sum(1 for x in chg if x < 0)
        if not g.get("deep"):
            return
        codes = set(sub["코드"])
        # 증거: 멤버 리포트(30일) 계층 태그 순 → 최신 순
        ev = []
        for code in codes:
            for r in rep_by_code.get(code, []):
                if (today - dt.date.fromisoformat(r["d"])).days <= 30:
                    ev.append({**r, "code": code, "name": companies[code]["name"] if code in companies else code})
        g["rep30"] = len(ev)
        g["rep_codes30"] = len({e["code"] for e in ev})
        # DART 수주·공급계약 공시(60일) → L5 데이터 증거로 합류
        dart_ev = []
        for code in codes:
            for h in dart_by_code.get(code, []):
                if h["kind"] == "공급계약":
                    amt = f"계약금액 {h['amount']:,.0f}억" if h.get("amount") is not None else "계약금액 -"
                    ratio = f" · 최근 매출 대비 {h['rev_ratio']:.1f}%" if h.get("rev_ratio") is not None else ""
                    per = f" · 기간 {h['start']}~{h['end']}" if h.get("start") else ""
                    title = f"{'[정정] ' if h.get('corrected') else ''}단일판매ㆍ공급계약 — {h.get('content') or '-'}"
                    summ = f"{amt}{ratio}{per}" + (f" · 계약상대 {h['cp']}" if h.get("cp") else "")
                else:
                    title = h["nm"][:80]
                    summ = "투자판단 관련 주요경영사항(수주·공급·계약 관련)"
                dart_ev.append({"d": h["d"], "br": "DART", "op": "공시", "tp": None, "px": None,
                                "t": title[:110], "s": summ[:220], "lv": 5, "ln": "데이터·공시",
                                "src": "DART", "u": h["url"], "code": code,
                                "name": companies[code]["name"] if code in companies else h["name"]})
        g["dart60"] = len(dart_ev)
        g["dart_codes60"] = len({e["code"] for e in dart_ev})
        ev = ev + dart_ev
        ev.sort(key=lambda x: x["d"], reverse=True)
        g["evidence"] = ev[:40]
        # 뉴스
        nw = []
        for code in codes:
            for n in companies.get(code, {}).get("news", []) or []:
                nw.append({**n, "code": code, "name": companies[code]["name"]})
        nw.sort(key=lambda x: x["d"], reverse=True)
        g["news"] = nw[:24]
        g["news7"] = len(nw)
        # 키워드 — 리포트 본문 우선. 뉴스 헤드라인은 시황 노이즈가 많아 리포트가 적을 때만 보조로 쓴다.
        texts = [e["t"] + " " + e["s"] for e in ev if e.get("src") != "DART"]
        if len(texts) < 5:
            texts += [n["t"] for n in nw]
        member_names = {companies[c]["name"] for c in codes if c in companies}
        g["keywords"] = S.extract_keywords(texts, top=14, exclude=member_names)
        # 내러티브
        al = aliases_for(name, kind)
        g["aliases"] = al
        g["narratives"] = S.search_narratives(narratives, al, limit=8)
        # 증거 계층 분포
        lv = {}
        for e in ev:
            lv[e["lv"]] = lv.get(e["lv"], 0) + 1
        g["ev_levels"] = lv
        # 인과사슬 초안
        top3 = [companies[m["code"]]["name"] + f"({m['total']})" for m in members[:3]]
        chain = [
            f"현상: {kind} '{name}' 종합 RS {g['rs']} (1M {g['r1']} / 3M {g['r3']}), 4주 변화 "
            f"{'+' if (g['rs_chg4w'] or 0) >= 0 else ''}{g['rs_chg4w'] if g['rs_chg4w'] is not None else '-'}, "
            f"RS≥70 종목 {g['breadth70']}% ({g['n']}종목).",
            f"확인: 최근 30일 리포트 {g['rep30']}건/{g['rep_codes30']}종목 · 60일 수주·공급계약 공시 {g['dart60']}건/{g['dart_codes60']}종목 · 7일 뉴스 {len(nw)}건 · "
            f"증거 계층 {', '.join(f'L{k} {v}건' for k, v in sorted(lv.items(), reverse=True)) or '-'}.",
            f"재료 키워드: {', '.join(w for w, _ in g['keywords'][:8]) or '-'}.",
            f"실적 전달: 컨센서스 영업이익 YoY(E) 중앙값 {g['op_yoy_e_med'] if g['op_yoy_e_med'] is not None else '-'}% · "
            f"EPS(E) 4주 상향 비중 {g['eps_up_share'] if g['eps_up_share'] is not None else '-'}% · "
            f"OPM 확대 종목 비중 {g['margin_up_share'] if g['margin_up_share'] is not None else '-'}%.",
            f"상대 수혜: 자동 점수 상위 {', '.join(top3) or '-'}.",
            "반증 조건(자동): 그룹 RS 50 하회 · EPS(E) 하향 종목 비중 50% 초과 · OPM 전년동기 대비 축소 전환 · "
            "리포트 목표가 하향 다수 → 이 중 2개 이상이면 논리 재검토.",
        ]
        g["chain"] = chain

    for g in groups:
        sub = ind_groups[g["name"]] if g["kind"] == "업종" else theme_groups[g["name"]]
        build_group_detail(g, sub)

    groups.sort(key=lambda x: -(x["rs"] or 0))

    # ---------- 저장
    payload = {
        "asof": f"{cur_ymd[:4]}-{cur_ymd[4:6]}-{cur_ymd[6:]}",
        "prior_asof": f"{pri_ymd[:4]}-{pri_ymd[4:6]}-{pri_ymd[6:]}" if pri_ymd else None,
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": [{"k": k, "cat": cat, "q": q, "how": how} for k, cat, q, how in ITEMS],
        "manual_items": [{"k": k, "cat": cat, "q": q} for k, cat, q in MANUAL_ITEMS],
        "groups": groups,
        "companies": companies,
    }
    os.makedirs(DOCS, exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.THESIS_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")

    # 점수 CSV (히스토리)
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for c in companies.values():
        if not c["deep"]:
            continue
        rows.append({"코드": c["code"], "회사명": c["name"], "업종": c["ind"], "시가총액(억)": c["mcap"],
                     "RS": c["rs"], "자동점수": c["score"]["total"], "전회점수": c.get("score_prev"),
                     "점수변화": c.get("score_chg"), "자료없음항목": c["score"]["na"],
                     "판정": c["score"]["grade"], "Core/Beta": c.get("cls"),
                     "EPS추정변화4주(%)": c["eps_rev4w"], "OPM_YoY(pp)": c.get("opm_yoy_pp"),
                     "증분마진(%)": c.get("incr_margin"), "PER(E)": c["per_e"], "PEG(E)": c["peg_e"],
                     "52주고점대비(%)": c["off_high"], "MA200대비(%)": c["ma200"],
                     "외인기관순매수일": c.get("fi_netbuy_days"), "리포트30일": c["rep_n30"],
                     "공급계약공시60일": c.get("dart_contracts60"), "공급계약매출대비합(%)": c.get("dart_ratio60"),
                     **{f"항목{i + 1}": it[0] for i, it in enumerate(c["score"]["items"])}})
    sc = pd.DataFrame(rows).sort_values("자동점수", ascending=False)
    # 실행일 기준으로 저장해 평일 수동 실행도 히스토리로 쌓인다 (같은 날 재실행은 덮어씀)
    for p in (os.path.join(OUT_DIR, f"스코어_{today_ymd}.csv"), os.path.join(OUT_DIR, "스코어_latest.csv")):
        sc.to_csv(p, index=False, encoding="utf-8-sig")

    S.log(f"완료: 그룹 {len(groups)} (심화 {sum(1 for g in groups if g.get('deep'))}) · 종목 {len(companies)} "
          f"(심화 {len(deep)}) → {OUT_JS} ({os.path.getsize(OUT_JS) / 1024:.0f}KB)")
    S.log("자동점수 상위 10:\n" + sc.head(10)[["회사명", "업종", "자동점수", "점수변화", "판정", "Core/Beta"]].to_string(index=False))
    up = sc[sc["점수변화"].notna()].sort_values("점수변화", ascending=False)
    if len(up):
        S.log("점수 상승 상위 5:\n" + up.head(5)[["회사명", "업종", "전회점수", "자동점수", "점수변화"]].to_string(index=False))
    return OUT_JS


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15, help="심화 분석 그룹 수(업종·테마 각각)")
    ap.add_argument("--offline", action="store_true", help="네트워크 없이 캐시만 사용")
    a = ap.parse_args()
    build(top=a.top, offline=a.offline)
