"""'시장의 시선' 보조 입력 — X 모니터링 수집 원문의 테마별 주목도.

x-monitor/data/YYYY-MM-DD.json 을 읽어 시장 테마별 점유율을 JSON 으로 출력한다.
웨이트에서 X 자료의 몫은 20% 뿐이다(나머지 80% 는 뉴스·가격 반응·폴리마켓).

Kobeissi 한 계정이 조회수 대부분을 차지하므로 조회수 점유율만 쓰지 않고
'그 테마를 언급한 계정 수' 점유율과 평균 낸 값을 x_share 로 쓴다.

사용:  python market-gaze/x_themes.py            # 최근 5개 파일
       python market-gaze/x_themes.py --days 3
"""
import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "x-monitor" / "data"

# 시장 변수 테마(키는 docs/data/gaze/*.json 의 items[].key 와 맞춘다). 새 변수가 생기면 여기에 추가.
THEMES = {
    "fed_rates": r"\bfed\b|fomc|warsh|rate hike|\bhike|dot plot|10[- ]?y|treasur|yield|\bbond|mortgage|tightening|\bcpi\b|\bpce\b|inflation|payroll",
    "oil_mideast": r"\boil\b|crude|brent|\bwti\b|diesel|gasoline|refiner|iran|hormuz|saudi|houthi|israel|strait|ceasefire|tanker",
    "ai_semis_earnings": r"\bai\b|nvda|nvidia|semis?\b|semiconductor|memory|dram|nand|\bhbm\b|micron|\$mu\b|capex|openai|anthropic|data ?center|\bgpu|optical|earnings|guidance|\beps\b",
    "china_trade": r"\bxi\b|china|tariff|trade deal|truce|rare earth|taiwan|summit|bessent",
    "election_politics": r"midterm|election|democrat|republican|congress|shutdown|sweep",
    "crypto": r"bitcoin|\bbtc\b|\beth\b|crypto|ethereum|solana",
    "gold_metals": r"\bgold\b|silver|copper|miners",
}


def analyze(path):
    posts = json.loads(path.read_text(encoding="utf-8")).get("posts", [])
    raw = {}
    for key, rx in THEMES.items():
        hit = [p for p in posts if re.search(rx, p.get("text") or "", re.I)]
        raw[key] = {
            "posts": len(hit),
            "views": sum(p.get("views") or 0 for p in hit),
            "accounts": len({p.get("handle") for p in hit}),
        }
    tv = sum(v["views"] for v in raw.values()) or 1
    ta = sum(v["accounts"] for v in raw.values()) or 1
    out = {}
    for key, v in raw.items():
        view_share = v["views"] / tv * 100
        acct_share = v["accounts"] / ta * 100
        out[key] = {
            **v,
            "view_share": round(view_share, 1),
            "account_share": round(acct_share, 1),
            "x_share": round((view_share + acct_share) / 2, 1),   # 테마 간 합 = 100
        }
    return {"file": path.name, "n_posts": len(posts), "themes": out}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="최근 파일 몇 개를 볼지")
    args = ap.parse_args()
    files = sorted(DATA_DIR.glob("20??-??-??.json"))[-args.days:]
    print(json.dumps([analyze(f) for f in files], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
