# -*- coding: utf-8 -*-
"""엑셀 팔로잉 리스트 → accounts.json 변환.

엑셀에서 O/OO/OOO 표시를 바꾼 뒤 재실행하면 모니터링 대상이 갱신된다.
기본으로 '카카오톡 받은 파일' 폴더에서 가장 최신 X_팔로잉_리스트*.xlsx 를 찾는다.
사용: python parse_excel.py [엑셀경로]
"""
import glob
import json
import os
import sys

import pandas as pd

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "카카오톡 받은 파일")
CATS = ["단기 트레이딩", "중기 트레이딩", "중장기 가치투자", "코인", "매크로",
        "원자재", "퀀트/퀀트적 통계", "기술적분석", "계절성"]


def latest_excel():
    files = glob.glob(os.path.join(DEFAULT_DIR, "X_팔로잉_리스트*.xlsx"))
    if not files:
        sys.exit(f"엑셀 파일을 찾을 수 없습니다: {DEFAULT_DIR}")
    return max(files, key=os.path.getmtime)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else latest_excel()
    df = pd.read_excel(path, header=2)
    df = df[df["핸들"].notna() & df["핸들"].astype(str).str.startswith("@")]

    accounts = []
    for _, row in df.iterrows():
        cats = {}
        for c in CATS:
            v = row.get(c)
            if pd.notna(v) and set(str(v).strip().upper()) == {"O"}:
                cats[c] = len(str(v).strip())
        grade = max(cats.values(), default=0)
        if grade == 0:
            continue  # 표시(O 이상)된 계정만 모니터링 대상
        handle = str(row["핸들"]).strip().lstrip("@")
        accounts.append({
            "handle": handle,
            "name": str(row["이름"]).strip(),
            "grade": grade,
            "categories": sorted(cats, key=cats.get, reverse=True),
            "note": "" if pd.isna(row.get("비고")) else str(row["비고"]).strip(),
            "url": f"https://x.com/{handle}",
        })

    accounts.sort(key=lambda a: (-a["grade"], a["handle"].lower()))
    out = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "accounts.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": os.path.basename(path), "accounts": accounts}, f,
                  ensure_ascii=False, indent=2)
    print(f"{len(accounts)}개 계정 저장 → {out}")


if __name__ == "__main__":
    main()
