# -*- coding: utf-8 -*-
"""
종목탐색_TOP30.xlsx 에 OpenDART 펀더멘털 참고 열을 채운다.

추가 열 (참고용 — 종목 선정 로직에는 사용하지 않음):
  매출YoY(%)      : 최신 보고서 기준 매출 성장률 (금융사는 공란)
  영업이익흑자     : O / X / - (데이터 없음)
  부채비율300%미만 : O / X / 금융(기준 제외) / - (데이터 없음)

- 값이 비어 있는 행만 조회해 채운다(이미 채운 행은 건드리지 않음).
- run_screener.ps1 이 스크리너 실행 직후 호출한다. 실패해도 종료코드 0
  (참고 열이 없어도 파이프라인은 계속 가야 한다).
"""
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(BASE, "종목탐색_TOP30.xlsx")
# 본 파일이 엑셀에서 열려 있던 회차는 스크리너가 pending 에 전체본을 써둔다.
# 그 경우 참고 열도 pending 쪽에 채워야 리포트에 반영된다.
PENDING = os.path.join(BASE, "종목탐색_TOP30.pending.xlsx")


def _target():
    return PENDING if os.path.exists(PENDING) else XLSX

COL_YOY = "매출YoY(%)"
COL_OP = "영업이익흑자"
COL_DEBT = "부채비율300%미만"


def main():
    if not os.path.exists(XLSX):
        print(f"[펀더멘털] {XLSX} 없음 — 건너뜀")
        return 0
    import dart_client
    if not dart_client._api_key():
        print("[펀더멘털] DART API 키 없음 — 건너뜀")
        return 0

    path = _target()
    df = pd.read_excel(path)
    if df.empty or "ticker" not in df.columns:
        print("[펀더멘털] 데이터 없음 — 건너뜀")
        return 0

    for col in (COL_YOY, COL_OP, COL_DEBT):
        if col not in df.columns:
            df[col] = None

    todo = df[df[COL_OP].isna() | (df[COL_OP].astype(str).str.strip() == "")]
    codes = sorted({str(int(t)).zfill(6) for t in todo["ticker"].dropna()})
    if not codes:
        print("[펀더멘털] 채울 행 없음")
        return 0

    filled = 0
    for code in codes:
        f = dart_client.fundamentals(code)
        if f:
            yoy = f.get("sales_yoy")
            op = f.get("op_profit")
            debt = f.get("debt_ratio")
            yoy_v = round(yoy, 1) if yoy is not None else None
            op_v = "-" if op is None else ("O" if op > 0 else "X")
            if f.get("financial"):
                debt_v = "금융"
            elif debt is None:
                debt_v = "-"
            else:
                debt_v = "O" if debt < 300 else "X"
        else:
            yoy_v, op_v, debt_v = None, "-", "-"
        mask = df["ticker"].apply(
            lambda t: pd.notna(t) and str(int(t)).zfill(6) == code) & (
            df[COL_OP].isna() | (df[COL_OP].astype(str).str.strip() == ""))
        df.loc[mask, COL_YOY] = yoy_v
        df.loc[mask, COL_OP] = op_v
        df.loc[mask, COL_DEBT] = debt_v
        filled += int(mask.sum())

    df.to_excel(path, index=False)
    print(f"[펀더멘털] {len(codes)}종목 조회, {filled}행 채움")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[펀더멘털] 오류(건너뜀): {e}")
        sys.exit(0)
