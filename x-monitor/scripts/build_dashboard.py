# -*- coding: utf-8 -*-
"""data/*.json 을 병합해 dashboard/data.js 를 생성한다.

수집 데이터 파일 형식 (data/YYYY-MM-DD.json):
  {"date": "2026-07-29", "collected_at": "...", "posts": [{handle, time, text, url, ...}]}
"""
import glob
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(BASE, "accounts.json"), encoding="utf-8") as f:
        accounts = json.load(f)["accounts"]

    posts, seen = [], set()
    for path in sorted(glob.glob(os.path.join(BASE, "data", "*.json"))):
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
        for p in day.get("posts", []):
            key = p.get("url") or (p.get("handle"), p.get("time"), p.get("text", "")[:50])
            if key in seen:
                continue
            seen.add(key)
            posts.append(p)

    posts.sort(key=lambda p: p.get("time", ""), reverse=True)
    out = os.path.join(BASE, "dashboard", "data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.XMON_DATA = ")
        json.dump({"accounts": accounts, "posts": posts}, f, ensure_ascii=False)
        f.write(";")
    print(f"{len(posts)}개 포스트 → {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
