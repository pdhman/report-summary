# -*- coding: utf-8 -*-
"""
심층 투자 논리 리포트(마크다운) 게시 — /thesis 스킬의 마지막 단계, 그리고 종목 리서치 PPT 를 옮긴
마크다운의 게시에도 같이 쓴다.

  python publish_thesis.py 000660 path/to/report.md [--score 26] [--class "Core Holding"]
  python publish_thesis.py 031980 docs/thesis/031980.md          # 이미 제자리에 있는 파일도 그대로 게시 가능

동작
  1. 마크다운을 docs/thesis/<code>.md 로 복사(맨 위에 frontmatter 없으면 붙인다)
  2. docs/thesis/index.json 갱신: {code: {name, ind, date, score, scale, cls, src, verdict, title, auto_score, file}}
     - frontmatter 가 있으면 date·score·scale·class·src·verdict·title 을 거기서 읽는다(리서치 PPT 변환본)
     - score/cls 를 못 찾으면 본문의 "**XX / 30**" 과 "Core Holding|Tactical Buy|High Beta Trade|Watchlist|Avoid" 를 찾는다
  3. 결과 경로를 출력. 커밋은 하지 않는다(스킬이 처리).

가격 레벨(목표가·진입가·손절)은 docs/thesis/private/<code>.md 에 따로 두며 git 에서 제외된다.
이 스크립트는 그 파일을 건드리지 않는다(있는지만 알려준다).
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


def parse_frontmatter(body: str) -> dict:
    """맨 위 '---' 블록의 key: value 를 dict 로. 숫자는 숫자로."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---", body, re.S)
    fm: dict = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v == "":
            fm[k] = None
        elif re.fullmatch(r"-?\d+", v):
            fm[k] = int(v)
        elif re.fullmatch(r"-?\d+\.\d+", v):
            fm[k] = float(v)
        else:
            fm[k] = v
    return fm


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
    fm = parse_frontmatter(body)

    score = a.score
    if score is None and isinstance(fm.get("score"), int):
        score = fm["score"]
    if score is None:
        m = re.search(r"\*\*\s*(\d{1,2})\s*/\s*30\s*\*\*", body)
        score = int(m.group(1)) if m else None
    scale = fm.get("scale") if isinstance(fm.get("scale"), int) else 30
    cls = a.cls or fm.get("class")
    if not cls:
        for c in CLASSES:
            if re.search(rf"\*\*{re.escape(c)}\*\*|분류[^\n]*{re.escape(c)}", body):
                cls = c
                break

    co = load_company(code)
    mt = re.search(r"^#\s*(.+?)\s*\(", body, re.M)
    name = fm.get("name") or co.get("name") or (mt.group(1) if mt else code)
    today = dt.date.today().isoformat()
    date = str(fm.get("date") or today)
    src = fm.get("src") or "thesis"
    if not body.startswith("---"):
        fmtxt = (f"---\ncode: {code}\nname: {name}\ndate: {today}\nscore: {score if score is not None else ''}\n"
                 f"class: {cls or ''}\nauto_score: {co.get('score', {}).get('total', '')}\n---\n\n")
        body = fmtxt + body

    os.makedirs(DOCS_THESIS, exist_ok=True)
    out = os.path.join(DOCS_THESIS, f"{code}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    # 날짜별 사본(히스토리)
    hist_dir = os.path.join(DOCS_THESIS, "history")
    os.makedirs(hist_dir, exist_ok=True)
    shutil.copyfile(out, os.path.join(hist_dir, f"{code}_{date.replace('-', '')}.md"))

    idx = {}
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            idx = json.load(f)
    prev = idx.get(code, {})
    auto_score = fm.get("auto_score") if isinstance(fm.get("auto_score"), int) else co.get("score", {}).get("total")
    idx[code] = {"name": name, "ind": co.get("ind"), "date": date, "score": score, "scale": scale, "cls": cls,
                 "src": src, "verdict": fm.get("verdict"), "title": fm.get("title"),
                 "auto_score": auto_score, "auto_scale": co.get("score", {}).get("max", 75), "file": f"thesis/{code}.md",
                 "prev_score": prev.get("score") if prev.get("date") != date else prev.get("prev_score"),
                 "prev_date": prev.get("date") if prev.get("date") != date else prev.get("prev_date")}
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    priv = os.path.join(DOCS_THESIS, "private", f"{code}.md")
    print(f"게시: {out} · {date} · 점수 {score}/{scale} · 분류 {cls} · 출처 {src} · index {len(idx)}종목"
          f" · 가격 레벨(비공개) {'있음' if os.path.exists(priv) else '없음'}")


if __name__ == "__main__":
    main()
