# -*- coding: utf-8 -*-
"""
심층 투자 논리 리포트(마크다운) 게시 — /thesis 스킬의 마지막 단계.

  python publish_thesis.py 000660 path/to/report.md [--score 26] [--class "Core Holding"]

동작
  1. 마크다운을 docs/thesis/<code>.md 로 복사(맨 위에 frontmatter 없으면 붙인다)
  2. docs/thesis/index.json 갱신: {code: {name, ind, date, score, cls, auto_score, file}}
     - score/cls 를 안 주면 본문의 "**XX / 30**" 과 "Core Holding|Tactical Buy|High Beta Trade|Watchlist|Avoid" 를 찾는다
  3. 결과 경로를 출력. 커밋은 하지 않는다(스킬이 처리).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_THESIS = os.path.normpath(os.path.join(BASE, "..", "docs", "thesis"))
DATA_JS = os.path.normpath(os.path.join(BASE, "..", "docs", "thesis_data.js"))
INDEX = os.path.join(DOCS_THESIS, "index.json")
CLASSES = ["Core Holding", "Tactical Buy", "High Beta Trade", "Watchlist", "Avoid"]


def load_company(code: str) -> dict:
    try:
        with open(DATA_JS, encoding="utf-8") as f:
            D = json.loads(f.read().split("=", 1)[1].rstrip().rstrip(";"))
        return D["companies"].get(code) or {}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("md")
    ap.add_argument("--score", type=int)
    ap.add_argument("--cls", dest="cls")
    a = ap.parse_args()
    code = a.code.zfill(6)
    with open(a.md, encoding="utf-8") as f:
        body = f.read()

    score = a.score
    if score is None:
        m = re.search(r"\*\*\s*(\d{1,2})\s*/\s*30\s*\*\*", body)
        score = int(m.group(1)) if m else None
    cls = a.cls
    if not cls:
        for c in CLASSES:
            if re.search(rf"\*\*{re.escape(c)}\*\*|분류[^\n]*{re.escape(c)}", body):
                cls = c
                break

    co = load_company(code)
    name = co.get("name") or re.search(r"^#\s*(.+?)\s*\(", body, re.M).group(1) if re.search(r"^#\s*(.+?)\s*\(", body, re.M) else code
    today = dt.date.today().isoformat()
    if not body.startswith("---"):
        fm = (f"---\ncode: {code}\nname: {name}\ndate: {today}\nscore: {score if score is not None else ''}\n"
              f"class: {cls or ''}\nauto_score: {co.get('score', {}).get('total', '')}\n---\n\n")
        body = fm + body

    os.makedirs(DOCS_THESIS, exist_ok=True)
    out = os.path.join(DOCS_THESIS, f"{code}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    # 날짜별 사본(히스토리)
    hist_dir = os.path.join(DOCS_THESIS, "history")
    os.makedirs(hist_dir, exist_ok=True)
    shutil.copyfile(out, os.path.join(hist_dir, f"{code}_{today.replace('-', '')}.md"))

    idx = {}
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            idx = json.load(f)
    prev = idx.get(code, {})
    idx[code] = {"name": name, "ind": co.get("ind"), "date": today, "score": score, "cls": cls,
                 "auto_score": co.get("score", {}).get("total"), "file": f"thesis/{code}.md",
                 "prev_score": prev.get("score") if prev.get("date") != today else prev.get("prev_score"),
                 "prev_date": prev.get("date") if prev.get("date") != today else prev.get("prev_date")}
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print(f"게시: {out} · 점수 {score} · 분류 {cls} · index {len(idx)}종목")


if __name__ == "__main__":
    main()
