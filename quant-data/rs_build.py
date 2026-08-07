# -*- coding: utf-8 -*-
"""
RS 스크리너 데이터 빌더 — 주간 퀀트데이터 CSV 기반.

입력: output/퀀트데이터_latest.csv (없으면 최신 날짜본, --csv 로 지정 가능)
  - 섹터·업종·테마 열 필수 (collect.py 가 네이버 분류를 CSV 에 포함)
  - 테마는 '; ' 구분 다중값

계산:
  - 종합 RS = 기간별 RS 백분위의 가중평균 (1M 20% · 3M 40% · 6M 20% · 12M 20%,
    결측 기간은 가중치 재배분, 3M 필수). 0~100 척도.
  - 그룹(섹터/업종/테마) 집계 = 종합 RS 평균, 기간별 RS 평균, 종목수,
    RS90+ 비중. 테마는 3종목 이상만.
  - 테마에서 SPAC 제외: '기업인수목적' 테마 + 종목명에 '스팩' 포함 종목

출력: ../reports/rs_data.js  (window.RS_DATA = {...})

실행:  python rs_build.py
       python rs_build.py --csv output/퀀트데이터_20260805_new.csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import sys

import pandas as pd

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")
REPORTS_DIR = os.path.normpath(os.path.join(BASE, "..", "reports"))
OUT_JS = os.path.join(REPORTS_DIR, "rs_data.js")

CLASS_COLS = ("섹터", "업종", "테마")
THEME_MIN_MEMBERS = 3

# 종합 RS 가중치 (IBD 방식 변형: 최근 3개월 2배)
#
# 가중 대상은 '기간별 RS 백분위'이지 원시 수익률이 아니다. 원시 수익률을
# 가중평균하면 구간별 스케일 차이 때문에 장기 구간이 결과를 지배한다
# (예: 1개월 수익률은 ±30% 범위인데 1년 수익률은 +900%까지 나온다 →
#  1년 항이 1개월 항보다 30배 커서 가중치를 어떻게 줘도 뒤집히지 않는다).
# 그 결과 "1년 급등 후 고점에서 40% 빠진" 종목이 RS 99로 남는 문제가 있었다.
# 백분위를 먼저 매기면 모든 구간이 0~100 같은 척도가 된다(IBD 방식).
RS_WEIGHTS = {"RS_1개월": 0.2, "RS_3개월": 0.4,
              "RS_6개월": 0.2, "RS_1년": 0.2}
RS_REQUIRED = "RS_3개월"      # 이 값이 없으면 종합 RS 를 내지 않는다


def _log(msg: str):
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


# ------------------------------------------------------------------ 데이터 로드
def load_quant_csv(path: str | None = None) -> tuple[pd.DataFrame, str]:
    if not path:
        latest = os.path.join(OUT_DIR, "퀀트데이터_latest.csv")
        dated = sorted(glob.glob(os.path.join(OUT_DIR, "퀀트데이터_2*.csv")))
        path = latest if os.path.exists(latest) else (dated[-1] if dated else None)
        if not path:
            raise FileNotFoundError("output/퀀트데이터_*.csv 가 없습니다. collect.py 먼저 실행하세요.")
    df = pd.read_csv(path, dtype={"코드": str})
    df["코드"] = df["코드"].str.zfill(6)

    missing = [c for c in (*CLASS_COLS, *RS_WEIGHTS) if c not in df.columns]
    if missing:
        raise ValueError(f"{os.path.basename(path)} 에 필요한 열이 없습니다: {missing} "
                         "(collect.py 를 최신 버전으로 실행하세요)")

    m = re.search(r"_(\d{8})", os.path.basename(path))
    asof = (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m
            else dt.date.fromtimestamp(os.path.getmtime(path)).isoformat())
    _log(f"퀀트데이터 로드: {len(df)}종목 (기준 {asof}, {os.path.basename(path)})")
    return df, asof


def composite_rs(df: pd.DataFrame) -> pd.Series:
    """기간별 RS 백분위의 가중평균 (0~100). 3개월 RS 없는 종목은 제외.

    이미 0~100 척도라 결과에 다시 rank 를 매기지 않는다 — 그래야 "RS 80"이
    '상위 20%'가 아니라 '기간별 상대강도의 가중평균'이라는 뜻으로 안정적이다.
    """
    cols = list(RS_WEIGHTS)
    pct = df[cols].apply(pd.to_numeric, errors="coerce")
    w = pd.Series(RS_WEIGHTS)
    wsum = pct.notna().mul(w, axis=1).sum(axis=1)
    score = pct.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, math.nan)
    score[pct[RS_REQUIRED].isna()] = math.nan
    return score.round(1)


# ------------------------------------------------------------------ 집계·출력
def _r(v):
    """NaN → None, 그 외 float 1자리."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else round(f, 1)


def group_stats(sub: pd.DataFrame) -> dict | None:
    sub = sub[sub["RS"].notna()]
    if len(sub) == 0:
        return None
    return {
        "rs": _r(sub["RS"].mean()),
        "r1": _r(sub["RS_1개월"].mean()), "r3": _r(sub["RS_3개월"].mean()),
        "r6": _r(sub["RS_6개월"].mean()), "r12": _r(sub["RS_1년"].mean()),
        "n": int(len(sub)),
        "p90": _r((sub["RS"] >= 90).mean() * 100),
    }


def build(csv_path: str | None = None) -> str:
    df, asof = load_quant_csv(csv_path)
    df["RS"] = composite_rs(df)

    # ---- 섹터/업종 집계 (CSV 열 기준)
    sectors, industries, themes = [], [], []
    for name, sub in df[df["섹터"].notna()].groupby("섹터"):
        st = group_stats(sub)
        if st:
            sectors.append({"name": str(name), **st})
    for name, sub in df[df["업종"].notna()].groupby("업종"):
        st = group_stats(sub)
        if st:
            industries.append({"name": str(name), **st})

    # ---- 테마 멤버십 (테마 열 '; ' 분리, SPAC 제외)
    spac_codes = set(df.loc[df["회사명"].str.contains("스팩", na=False), "코드"])
    theme_codes: dict[str, list[str]] = {}
    for code, name, raw in zip(df["코드"], df["회사명"], df["테마"]):
        if code in spac_codes or not isinstance(raw, str) or not raw.strip():
            continue
        for t in raw.split(";"):
            t = t.strip()
            if not t or "기업인수목적" in t or "SPAC" in t.upper():
                continue
            theme_codes.setdefault(t, []).append(code)

    idx = df.set_index("코드")
    theme_members = [(t, cs) for t, cs in theme_codes.items()
                     if len(cs) >= THEME_MIN_MEMBERS]
    for name, members in theme_members:
        st = group_stats(idx.loc[members])
        if st:
            themes.append({"name": name, **st})

    sectors.sort(key=lambda x: -(x["rs"] or 0))
    industries.sort(key=lambda x: -(x["rs"] or 0))
    themes.sort(key=lambda x: -(x["rs"] or 0))

    sec_idx = {s["name"]: i for i, s in enumerate(sectors)}
    ind_idx = {s["name"]: i for i, s in enumerate(industries)}
    thm_idx = {s["name"]: i for i, s in enumerate(themes)}

    themes_of: dict[str, list[int]] = {}
    for name, members in theme_members:
        ti = thm_idx.get(name)
        if ti is None:
            continue
        for c in members:
            themes_of.setdefault(c, []).append(ti)

    # ---- 종목 배열 (RS 내림차순, RS 없는 종목 제외)
    out_stocks = []
    view = df[df["RS"].notna()].sort_values("RS", ascending=False)
    for _, row in view.iterrows():
        code = row["코드"]
        out_stocks.append([
            code, row["회사명"], 0 if row["시장"] == "KOSPI" else 1,
            None if pd.isna(row["시가총액(억)"]) else round(float(row["시가총액(억)"])),
            None if pd.isna(row["거래대금(억)"]) else round(float(row["거래대금(억)"]), 1),
            _r(row["RS"]), _r(row["RS_1개월"]), _r(row["RS_3개월"]),
            _r(row["RS_6개월"]), _r(row["RS_1년"]),
            _r(row["52주고점대비(%)"]), _r(row["MA200대비(%)"]),
            sec_idx.get(row["섹터"], -1),
            ind_idx.get(row["업종"], -1),
            themes_of.get(code, []),
        ])

    payload = {
        "asof": asof,
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sectors": sectors, "industries": industries, "themes": themes,
        "stocks": out_stocks,
    }
    js = "window.RS_DATA = " + json.dumps(payload, ensure_ascii=False,
                                          separators=(",", ":")) + ";\n"
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    _log(f"완료: 종목 {len(out_stocks)} · 섹터 {len(sectors)} · 업종 {len(industries)}"
         f" · 테마 {len(themes)} → {OUT_JS} ({os.path.getsize(OUT_JS) / 1024:.0f}KB)")
    return OUT_JS


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RS 스크리너 데이터 빌드")
    ap.add_argument("--csv", help="퀀트데이터 CSV 경로 (기본: latest → 최신 날짜본)")
    args = ap.parse_args()
    build(csv_path=args.csv)
