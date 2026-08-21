# -*- coding: utf-8 -*-
"""
차트 데이터 빌더 — 한국 전종목 일봉 JSON 을 orphan 브랜치(chart-data)로 배포.

동작:
  1. 유니버스 = output/퀀트데이터_latest.csv (코드·회사명·시장)
  2. cache/ohlcv_full.parquet 에 OHLCV 일봉 증분 캐싱 (FDR·네이버 소스,
     collect.py 의 종가 캐시와 같은 방식 — 여기는 시가/고가/저가/거래량 포함)
  3. chart_json/ 에 종목별 JSON 생성 (reports/data/*.json 과 같은 포맷,
     chart.html 이 그대로 읽는다): {symbol,name,currency,bars:[[ymd,o,h,l,c,v],..]}
  4. chart_json/ 을 매번 새 git 저장소로 초기화해 chart-data 브랜치로
     강제 푸시 — 커밋이 항상 1개라 저장소 히스토리가 쌓이지 않는다.
     (차트 데이터는 과거 이력이 필요 없음)

사이트 연동: chart.html 이 ######.KS/.KQ 심볼을
  https://raw.githubusercontent.com/pdhman/report-summary/chart-data/<심볼>.json
  에서 불러온다 (raw 는 CORS 허용, CDN 캐시 약 5분).

실행:  python chart_build.py             # 증분 갱신 + 푸시 (신규 데이터 없으면 푸시 생략)
       python chart_build.py --force     # 데이터 변화 없어도 강제 푸시
       python chart_build.py --no-push   # JSON 생성까지만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")
CACHE_DIR = os.path.join(BASE, "cache")
LOG_DIR = os.path.join(BASE, "logs")
OHLCV_PATH = os.path.join(CACHE_DIR, "ohlcv_full.parquet")
STATE_PATH = os.path.join(CACHE_DIR, "chart_push_state.json")
JSON_DIR = os.path.join(BASE, "chart_json")

REMOTE = "https://github.com/pdhman/report-summary.git"
BRANCH = "chart-data"

LOOKBACK_DAYS = 550          # 달력일 기준 약 1.5년(≈370거래일) — MA200 표시 여유 포함
WORKERS = 4
SLEEP = 0.1

log = logging.getLogger("chart-data")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"chart_{dt.datetime.now():%Y%m%d_%H%M%S}.log")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    return path


# ------------------------------------------------------------------ 유니버스
def load_universe() -> pd.DataFrame:
    path = os.path.join(OUT_DIR, "퀀트데이터_latest.csv")
    if not os.path.exists(path):
        raise FileNotFoundError("output/퀀트데이터_latest.csv 없음 — collect.py 먼저 실행")
    df = pd.read_csv(path, dtype={"코드": str}, usecols=["코드", "회사명", "시장"])
    df["코드"] = df["코드"].str.zfill(6)

    # ETF 도 차트 대상에 포함 (rs_build.py 가 주 1회 갱신하는 목록, 심볼은 .KS)
    etf_list = os.path.join(CACHE_DIR, "etf_list.json")
    if os.path.exists(etf_list):
        try:
            with open(etf_list, encoding="utf-8") as f:
                etfs = json.load(f)
            add = pd.DataFrame({"코드": [str(e["code"]).zfill(6) for e in etfs],
                                "회사명": [e["name"] for e in etfs],
                                "시장": "KOSPI"})
            add = add[~add["코드"].isin(set(df["코드"]))]
            df = pd.concat([df, add], ignore_index=True)
            log.info("유니버스에 ETF %d종목 추가", len(add))
        except Exception as ex:
            log.warning("ETF 목록 로드 실패 — 주식만 진행: %s", ex)
    return df


# ------------------------------------------------------------------ OHLCV 캐시
def _fetch_ohlcv(code: str, start: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            time.sleep(SLEEP)
            df = fdr.DataReader(code, start)
            if df is None or df.empty:
                return None
            out = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
            out.columns = ["date", "open", "high", "low", "close", "volume"]
            out.insert(1, "code", code)
            return out
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5 * (attempt + 1))


def update_cache(codes: list[str]) -> tuple[pd.DataFrame, int]:
    """OHLCV 캐시 갱신. (전체 데이터, 신규 수신 행 수) 반환.

    전 구간을 매번 다시 받는다(증분 아님). 액면분할·액면병합·감자가 있으면
    네이버가 과거 시세를 소급 조정하므로, 새 날짜만 덧붙이면 옛 행이 조정 전
    기준으로 남아 차트에 가짜 갭이 생긴다. 종목당 요청 수는 1건으로 같다.
    """
    full_start = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    ref = fdr.DataReader("005930", (dt.date.today() - dt.timedelta(days=14)).isoformat())
    latest = pd.Timestamp(ref.index[-1])

    cached = None
    if os.path.exists(OHLCV_PATH):
        cached = pd.read_parquet(OHLCV_PATH)
        cached["date"] = pd.to_datetime(cached["date"])
        cached = cached[cached["code"].isin(set(codes))]

    plan: dict[str, str] = {c: full_start for c in codes}

    log.info("일봉 수신 %d종목 (기준일 %s) ...", len(plan), latest.date())
    frames, done, fail = [], 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_fetch_ohlcv, c, s): c for c, s in plan.items()}
        for fut in as_completed(futs):
            df = fut.result()
            if df is not None:
                frames.append(df)
            else:
                fail += 1
            done += 1
            if done % 300 == 0:
                log.info("  진행 %d/%d", done, len(plan))

    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    n_new = len(new)
    if not new.empty:
        new["date"] = pd.to_datetime(new["date"])
        # 수신 성공한 종목은 캐시본을 통째로 버린다 (조정 전 기준 잔존 방지)
        if cached is not None:
            cached = cached[~cached["code"].isin(set(new["code"]))]
    merged = pd.concat([cached, new], ignore_index=True) if cached is not None else new
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged[merged["date"] >= pd.Timestamp(full_start)]
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)

    os.makedirs(CACHE_DIR, exist_ok=True)
    merged.to_parquet(OHLCV_PATH, index=False)
    log.info("캐시 저장: %d종목 / %s행 (신규 %s행, 실패 %d)",
             merged["code"].nunique(), f"{len(merged):,}", f"{n_new:,}", fail)
    return merged, n_new


# ------------------------------------------------------------------ 코퍼레이트액션 보정
PRICE_LIMIT = 0.32          # 국내 일일 등락 한도 30% + 여유


def adjust_corporate_actions(df: pd.DataFrame) -> pd.DataFrame:
    """감자·액면병합·액면분할로 생긴 미조정 갭을 소급 보정 (collect.py 와 동일 기준).

    하루 등락이 ±30% 를 넘는 점프는 가격 재편(감자·병합 등)이다. 네이버 일봉은
    이를 소급 조정하지 않는 경우가 있어 보정 없이 그리면 차트에 가짜 갭이 남는다.
    이벤트 배수를 그 이전 봉에 곱해 시계열을 연속으로 만들고, 거래량은 주식 수가
    함께 변하므로 반대로 나눈다. 최신 봉은 이후 이벤트가 없어 그대로 유지된다.
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    ratio = df.groupby("code")["close"].transform(lambda s: s / s.shift(1))
    # 종가 0 데이터 글리치는 ratio 가 0/inf 이 되어 factor 를 0/inf 로 오염시킨다
    # (2026-08-21 volume/0 → inf → OverflowError). 유한 양수 ratio 만 이벤트로 본다.
    ratio = ratio.where(np.isfinite(ratio) & (ratio > 0))
    ev = (ratio > 1 + PRICE_LIMIT) | (ratio < 1 - PRICE_LIMIT)
    if not ev.any():
        return df

    f = ratio.where(ev, 1.0).fillna(1.0)
    rev = f[::-1].groupby(df["code"][::-1]).cumprod()[::-1]
    factor = rev.groupby(df["code"]).shift(-1).fillna(1.0)

    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * factor
    df["volume"] = (df["volume"] / factor).round()
    log.info("미조정 코퍼레이트액션 보정: %d건 / %d종목",
             int(ev.sum()), df.loc[ev, "code"].nunique())
    return df


# ------------------------------------------------------------------ JSON 생성
def _rmtree_force(path: str):
    """읽기 전용 파일(.git 팩 등)도 지우는 rmtree — Windows 에서 git 이
    만든 pack/idx 는 읽기 전용이라 기본 rmtree 가 PermissionError 로 죽는다."""
    shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, 0o777), f(p)))


def write_json(ohlcv: pd.DataFrame, uni: pd.DataFrame) -> int:
    if os.path.isdir(JSON_DIR):
        _rmtree_force(JSON_DIR)
    os.makedirs(JSON_DIR)

    meta = {r["코드"]: (r["회사명"], "KS" if r["시장"] == "KOSPI" else "KQ")
            for _, r in uni.iterrows()}
    n = 0
    for code, g in ohlcv.groupby("code"):
        if code not in meta:
            continue
        name, suffix = meta[code]
        g = g.dropna(subset=["open", "high", "low", "close"])
        if g.empty:
            continue
        bars = [[int(d.strftime("%Y%m%d")), round(float(o), 1), round(float(h), 1),
                 round(float(l), 1), round(float(c), 1),
                 int(v) if pd.notna(v) and np.isfinite(v) else 0]
                for d, o, h, l, c, v in zip(g["date"], g["open"], g["high"],
                                            g["low"], g["close"], g["volume"])]
        payload = {"symbol": f"{code}.{suffix}", "name": name, "currency": "KRW",
                   "note": "네이버 일봉", "bars": bars}
        with open(os.path.join(JSON_DIR, f"{code}.{suffix}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        n += 1

    manifest = {"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "count": n,
                "last_date": str(ohlcv["date"].max().date())}
    with open(os.path.join(JSON_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    size_mb = sum(os.path.getsize(os.path.join(JSON_DIR, f))
                  for f in os.listdir(JSON_DIR)) / 1e6
    log.info("JSON 생성: %d종목, %.0fMB", n, size_mb)
    return n


# ------------------------------------------------------------------ orphan 푸시
def _git(args: list[str]):
    r = subprocess.run(["git", *args], cwd=JSON_DIR, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()[:300]}")


def push_branch():
    """chart_json/ 을 히스토리 없는 단일 커밋으로 chart-data 브랜치에 강제 푸시."""
    git_dir = os.path.join(JSON_DIR, ".git")
    if os.path.isdir(git_dir):
        _rmtree_force(git_dir)
    _git(["init", "-q", "-b", BRANCH])
    _git(["remote", "add", "origin", REMOTE])
    _git(["add", "-A"])
    _git(["-c", "user.name=chart-data-bot",
          "-c", "user.email=chart-data@local",
          "commit", "-q", "-m", f"chart data {dt.date.today()}"])
    _git(["push", "-f", "-q", "origin", BRANCH])
    log.info("푸시 완료: origin/%s (단일 커밋 교체)", BRANCH)


# ------------------------------------------------------------------ 메인
def main():
    ap = argparse.ArgumentParser(description="차트 데이터 빌드·배포")
    ap.add_argument("--force", action="store_true", help="신규 데이터 없어도 푸시")
    ap.add_argument("--no-push", action="store_true", help="JSON 생성까지만")
    args = ap.parse_args()

    log_path = _setup_logging()
    t0 = time.time()

    uni = load_universe()
    codes = uni["코드"].tolist()
    ohlcv, _ = update_cache(codes)
    if ohlcv is None or ohlcv.empty:
        log.error("일봉 데이터 없음 — 중단")
        return 1

    last_date = str(ohlcv["date"].max().date())
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    # 전 구간 재수신이라 '신규 행 수'는 판단 기준이 못 된다 — 최신 거래일이
    # 지난 푸시 이후로 넘어갔는지 본다(휴장일에는 푸시 생략).
    # 단 거래일이 그대로여도 유니버스가 바뀌면(신규 상장·시장 구분 변경 등)
    # 새 종목이 사이트에서 조회되지 않으므로 푸시해야 한다.
    n_uni = len(uni)
    same_day = state.get("last_pushed") == last_date
    same_uni = state.get("universe_size") == n_uni
    if not args.force and same_day and same_uni:
        log.info("최신 거래일·유니버스 변화 없음 (마지막 푸시 %s, %d종목) — 푸시 생략",
                 last_date, n_uni)
        return 0
    if same_day and not same_uni:
        log.info("거래일은 같지만 유니버스 %s → %d종목 — 푸시 진행",
                 state.get("universe_size", "?"), n_uni)

    write_json(adjust_corporate_actions(ohlcv), uni)
    if not args.no_push:
        push_branch()
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_pushed": last_date, "universe_size": n_uni,
                       "at": dt.datetime.now().isoformat(timespec="seconds")}, f)

    log.info("완료 (%.1f분, 로그 %s)", (time.time() - t0) / 60, os.path.basename(log_path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # 스케줄러 실행에서는 stderr 가 유실되므로 예외를 로그 파일에 남긴다
        log.exception("치명적 오류")
        sys.exit(1)
