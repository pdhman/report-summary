# -*- coding: utf-8 -*-
"""분석 탭(차트·계절성)이 서버 없이도 동작하도록 유니버스 일봉을 정적 파일로 굽는다.

산출물 (docs/data/):
    <티커>.json   {"symbol","name","group","currency","bars":[[yyyymmdd,o,h,l,c,v,adj], ...]}
                  o/h/l/c/v = 무조정 실거래가(차트), adj = 수정종가(계절성)
                  한 줄에 하루씩 기록해 매일 갱신 시 git diff 가 몇 줄만 바뀐다.
    index.js      const TICKER_INDEX = [...]  — 종목 목록(이름·그룹·기간). 화면 목록용.

종목이 100개가 넘으므로 한 파일에 몰지 않고 종목별로 나눈다. 페이지는 선택한
종목의 파일만 내려받으므로 첫 로딩이 가볍다.

기본은 증분 수집이다. 기존 파일이 있으면 마지막 날짜 부근부터만 받아 뒤에
붙인다(전체 이력 재다운로드 대비 실행 시간이 크게 줄어든다). 단 수정종가는
배당·분할 때 과거 값이 소급 변경되므로, 티커별로 7일에 한 번 전체를 다시
받는다 — 어떤 날 어떤 티커를 전체로 받을지는 날짜에서 계산되므로 별도 상태
파일이 필요 없고, 매 실행의 부하가 고르게 퍼진다.

사용법:
    python seasonality/fetch_data.py             # 유니버스 전체 갱신(증분)
    python seasonality/fetch_data.py SPY QQQ     # 지정 종목만 갱신
    python seasonality/fetch_data.py --missing   # 파일이 없는 종목만 (중단 후 이어받기)
    python seasonality/fetch_data.py --full      # 전체 이력 강제 재다운로드

조회에 실패한 종목은 기존 파일을 그대로 두므로, 야후의 일시적 차단이
사이트의 데이터를 지우지 않는다.
"""
import datetime
import json
import math
import sys
import zlib
from pathlib import Path

import yfinance as yf

from universe import UNIVERSE, flat

BASE = Path(__file__).parent
OUT_DIR = BASE.parent / "docs" / "data"
INDEX_FILE = OUT_DIR / "index.js"

# 야후 데이터에 공백이 있는 종목을, 그 종목이 추종하는 지수로 메운다.
# (KODEX 200 은 2009-04-17 이전이 거의 비어 있다 — 2007년 8일·2008년 0일)
# FinanceDataReader 심볼 / 메우기 시작일 / 화면 표기용 설명
BACKFILL = {
    "069500.KS": {"fdr": "KS200", "start": "2002-10-14",   # KODEX 200 상장일
                  "label": "코스피200 지수"},
}
GAP_DAYS = 30       # 이보다 긴 공백이 있으면 그 이전 구간은 신뢰하지 않는다

FULL_CYCLE = 7      # 티커별 전체 재수집 주기(일)
OVERLAP_DAYS = 7    # 증분 시 겹쳐 받는 기간 — 최근 봉 정정·장중봉 확정을 반영


def wants_full(symbol: str, today: datetime.date) -> bool:
    """오늘 이 티커를 전체로 다시 받을 차례인가.

    수정종가는 배당·분할 시 과거 값까지 바뀌므로 주기적 전체 재수집이 필요하다.
    티커를 FULL_CYCLE 개 그룹으로 나눠 하루에 한 그룹만 받는다(날짜만으로 결정 →
    상태 파일 불필요, 부하 분산). crc32 는 실행마다 값이 같아야 하므로 hash() 대신 쓴다.
    """
    return zlib.crc32(symbol.encode()) % FULL_CYCLE == today.toordinal() % FULL_CYCLE


def read_existing(symbol: str) -> dict | None:
    path = OUT_DIR / f"{fname(symbol)}.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return d if d.get("bars") else None


def merge(old_bars: list, new_bars: list) -> list:
    """같은 날짜는 새 값으로 교체하고 날짜순으로 정렬해 합친다."""
    m = {b[0]: b for b in old_bars}
    for b in new_bars:
        m[b[0]] = b
    return [m[k] for k in sorted(m)]


def fetch(symbol: str, start: datetime.date | None = None):
    """(bars, currency) 또는 None. start 를 주면 그 날짜부터만 받는다."""
    tk = yf.Ticker(symbol)
    if start is None:
        hist = tk.history(period="max", auto_adjust=False)
    else:
        hist = tk.history(start=start.isoformat(), auto_adjust=False)
    if hist.empty:
        return None
    try:
        currency = (tk.history_metadata or {}).get("currency") or ""
    except Exception:
        currency = ""
    adj_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
    cols = hist[["Open", "High", "Low", "Close", "Volume", adj_col]]
    bars = []
    for ts, (o, h, l, c, vol, adj) in zip(cols.index, cols.values):
        if any(math.isnan(v) for v in (o, h, l, c, adj)):
            continue
        bars.append([
            int(ts.strftime("%Y%m%d")),
            round(float(o), 4), round(float(h), 4), round(float(l), 4), round(float(c), 4),
            0 if math.isnan(vol) else int(vol), round(float(adj), 4),
        ])
    return (bars, currency) if bars else None


def _date(n: int) -> datetime.date:
    return datetime.date(n // 10000, n // 100 % 100, n % 100)


def backfill(sym: str, bars: list):
    """야후 데이터의 앞쪽 공백을 추종 지수로 메운다. (메운 봉수, 시작일) 반환."""
    cfg = BACKFILL.get(sym)
    if not cfg:
        return bars, None

    # 마지막 큰 공백 지점(J) 이후만 신뢰한다. 그 앞은 지수로 대체.
    j = 0
    for i in range(1, len(bars)):
        if (_date(bars[i][0]) - _date(bars[i - 1][0])).days > GAP_DAYS:
            j = i
    if j == 0:
        return bars, None

    junction = bars[j]
    try:
        import FinanceDataReader as fdr
        idx = fdr.DataReader(cfg["fdr"], cfg["start"], str(_date(junction[0])))
    except Exception as e:
        print(f"    [{sym}] 지수 백필 실패({e.__class__.__name__}) — 야후 데이터만 사용")
        return bars, None
    if idx.empty or junction[0] not in [int(t.strftime("%Y%m%d")) for t in idx.index]:
        print(f"    [{sym}] 지수 백필: 접합일 데이터 없음 — 야후 데이터만 사용")
        return bars, None

    # 접합일에서 수준을 맞춰 이어 붙인다(수익률 연속). 종가용/수정종가용 배율을 따로 둔다.
    base = float(idx.loc[idx.index[[int(t.strftime("%Y%m%d")) == junction[0]
                                    for t in idx.index]][0], "Close"])
    k_px, k_adj = junction[4] / base, junction[6] / base

    filled = []
    for ts, row in idx.iterrows():
        ymd = int(ts.strftime("%Y%m%d"))
        if ymd >= junction[0]:
            break
        c = float(row["Close"])
        if not c or math.isnan(c):
            continue
        o, h, l = (float(row.get(k, c) or c) for k in ("Open", "High", "Low"))
        if any(math.isnan(v) or v <= 0 for v in (o, h, l)):
            o = h = l = c
        filled.append([ymd, round(o * k_px, 4), round(h * k_px, 4), round(l * k_px, 4),
                       round(c * k_px, 4), 0, round(c * k_adj, 4)])
    if not filled:
        return bars, None
    return filled + bars[j:], (len(filled), filled[0][0], junction[0])


def fname(sym: str) -> str:
    """티커 → 파일명. 지수 심볼의 '^'는 URL 에서 번거로우므로 '_'로 바꾼다."""
    return sym.replace("^", "_")


def write_ticker(sym: str, name: str, group: str, bars: list, currency: str,
                 note: str = "") -> None:
    meta = {"symbol": sym, "name": name, "group": group, "currency": currency}
    if note:
        meta["note"] = note
    head = json.dumps(meta, ensure_ascii=False)[:-1]
    lines = [head + ', "bars": [']
    lines.extend(json.dumps(b, separators=(",", ":")) + "," for b in bars[:-1])
    lines.append(json.dumps(bars[-1], separators=(",", ":")))
    lines.append("]}")
    (OUT_DIR / f"{fname(sym)}.json").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index() -> None:
    """디스크에 실제로 존재하는 종목만 모아 목록 파일을 만든다."""
    entries = []
    for group, items in UNIVERSE:
        for sym, name in items.items():
            path = OUT_DIR / f"{fname(sym)}.json"
            if not path.exists():
                continue
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            bars = d.get("bars") or []
            if not bars:
                continue
            entries.append({"s": sym, "n": name, "g": group,
                            "c": d.get("currency", ""),
                            "f": bars[0][0], "l": bars[-1][0], "k": len(bars)})
    lines = ["const TICKER_INDEX = ["]
    lines.extend(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + ","
                 for e in entries[:-1])
    if entries:
        lines.append(json.dumps(entries[-1], ensure_ascii=False, separators=(",", ":")))
    lines.append("];")
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"목록 갱신: {INDEX_FILE.name} ({len(entries)}종목)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    known = flat()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_missing = "--missing" in sys.argv
    force_full = "--full" in sys.argv
    today = datetime.date.today()

    if args:
        targets = [(s.upper(), *known.get(s.upper(), (s.upper(), "기타"))) for s in args]
    else:
        targets = [(sym, name, group) for group, items in UNIVERSE
                   for sym, name in items.items()]
    if only_missing:
        targets = [t for t in targets if not (OUT_DIR / f"{fname(t[0])}.json").exists()]

    ok, failed, n_inc, n_full, added = 0, [], 0, 0, 0
    for i, (sym, name, group) in enumerate(targets, 1):
        prev = None if force_full else read_existing(sym)
        # 전체 재수집 조건: 기존 파일 없음 / --full / 오늘이 이 티커의 전체 갱신 차례
        full = prev is None or force_full or wants_full(sym, today)
        start = None
        if not full:
            last = _date(prev["bars"][-1][0])
            start = last - datetime.timedelta(days=OVERLAP_DAYS)

        try:
            got = fetch(sym, start)
        except Exception as e:
            print(f"[{i}/{len(targets)}] {sym} 실패: {str(e)[:60]}")
            failed.append(sym)
            continue
        if not got:
            # 증분에서 빈 응답은 '새 거래일이 없음'일 뿐이라 실패가 아니다
            if full:
                print(f"[{i}/{len(targets)}] {sym} 데이터 없음")
                failed.append(sym)
            else:
                print(f"[{i}/{len(targets)}] {sym} 신규 없음")
            continue

        bars, currency = got
        note = ""
        if full:
            bars, bf = backfill(sym, bars)
            if bf:
                ymd = str(bf[2])
                note = (f"{ymd[:4]}-{ymd[4:6]} 이전 구간은 {BACKFILL[sym]['label']}로 대체 "
                        f"(야후 원본 데이터 공백)")
            n_full += 1
            tail = f" · 지수로 {bf[0]}일 백필({bf[1]}~)" if bf else " · 전체"
        else:
            before = len(prev["bars"])
            bars = merge(prev["bars"], bars)
            # 백필 구간처럼 기존 파일이 들고 있던 설명은 유지한다
            note = prev.get("note", "")
            currency = currency or prev.get("currency", "")
            n_inc += 1
            added += len(bars) - before
            tail = f" · 증분 +{len(bars) - before}일"

        write_ticker(sym, name, group, bars, currency, note)
        ok += 1
        print(f"[{i}/{len(targets)}] {sym} {len(bars)}일 ({bars[0][0]}~{bars[-1][0]}){tail}")

    write_index()
    total_mb = sum(f.stat().st_size for f in OUT_DIR.glob("*.json")) / 1024 / 1024
    print(f"완료: 갱신 {ok}종목(증분 {n_inc} +{added}일 / 전체 {n_full}) · "
          f"실패 {len(failed)} · 전체 {total_mb:,.1f} MB")
    if failed:
        print(f"실패 목록(기존 파일 유지): {failed}")


if __name__ == "__main__":
    main()
