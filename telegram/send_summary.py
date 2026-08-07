# -*- coding: utf-8 -*-
"""알파노트 대시보드 핵심 지표를 텔레그램으로 요약 발송한다.

데이터는 이미 각 수집기가 만들어 둔 JS 파일에서 읽는다 (재수집 없음):
  reports/market_data.js        시장 건전성 (평일 16:35)
  수급모니터링/dashboard_data.js  투자자별 수급 (평일 15:40)
  reports/rs_data.js            RS 스크리너 (주간)

사용:
  python send_summary.py            발송
  python send_summary.py --dry-run  발송 없이 메시지만 출력
"""
import argparse
import html
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
API = "https://api.telegram.org/bot{token}/{method}"
DASHBOARD_URL = "https://pdhman.github.io/report-summary/index.html"

# market.html 의 SCORE_DEFS 와 동일한 구간·라벨을 쓴다 (대시보드와 문구를 맞춘다)
SCORE_LABELS = {
    "overall": ("종합 체온", ["냉각", "낮음", "중립", "높음", "과열"]),
    "trend": ("추세(폭)", ["폭 매우 좁음", "폭 좁음", "보통", "폭 넓음", "폭 매우 넓음"]),
    "spec": ("투기 열기", ["매우 차분", "차분", "보통", "뜨거움", "과열"]),
    "vol": ("변동성", ["매우 낮음", "낮음", "보통", "높음", "매우 높음"]),
    "lev": ("레버리지", ["매우 가벼움", "가벼움", "보통", "무거움", "과중"]),
}


def zone(v):
    for i, edge in enumerate((20, 40, 60, 80)):
        if v < edge:
            return i
    return 4


def load_js(path):
    """`window.X = {...};` 형태의 JS 데이터 파일을 dict 로 읽는다."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("=") + 1:].strip().rstrip(";"))


def last(seq, n=1):
    """뒤에서 n 번째 유효값. 데이터가 모자라면 None."""
    vals = [v for v in seq if v is not None] if seq else []
    return vals[-n] if len(vals) >= n else None


def num(v, nd=0, sign=False):
    if v is None:
        return "–"
    s = f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"
    return s


# ---------------------------------------------------------------- 섹션 빌더

def section_score(m, lines):
    sc = m.get("scores", {})
    ov = sc.get("overall")
    if ov is None:
        return
    name, labels = SCORE_LABELS["overall"]
    lines.append(f"🌡 <b>{name} {ov:.0f}</b> ({labels[zone(ov)]})")
    parts = []
    for key in ("trend", "spec", "vol", "lev"):
        v = sc.get(key)
        if v is not None:
            short = SCORE_LABELS[key][0].split("(")[0]
            parts.append(f"{short} {v:.0f}")
    if parts:
        lines.append("   " + " · ".join(parts))


def section_index(m, lines):
    idx = m.get("index", {})
    rows = []
    for key, label in (("kospi", "코스피"), ("kosdaq", "코스닥")):
        cur, prev = last(idx.get(key)), last(idx.get(key), 2)
        if cur is None:
            continue
        chg = f" ({(cur - prev) / prev * 100:+.2f}%)" if prev else ""
        rows.append(f"   {label} {num(cur, 2)}{chg}")
    vk = last(m.get("vkospi"))
    if vk is not None:
        rows.append(f"   VKOSPI {num(vk, 1)}")
    if rows:
        lines.append("")
        lines.append("📈 <b>지수</b>")
        lines.extend(rows)


def section_breadth(m, lines):
    M = m.get("markets", {}).get("all")
    if not M:
        return
    lines.append("")
    lines.append("📊 <b>시장 폭</b> (전체)")
    lines.append(f"   상승 {num(last(M.get('adv')))} / 하락 {num(last(M.get('dec')))}")
    lines.append(f"   신고가 {num(last(M.get('nh')))} / 신저가 {num(last(M.get('nl')))}")
    ma20, ma200 = last(M.get("ma20")), last(M.get("ma200"))
    lines.append(f"   20일선 위 {num(ma20, 1)}% · 200일선 위 {num(ma200, 1)}%")


def section_flows(lines):
    path = BASE / "수급모니터링" / "dashboard_data.js"
    if not path.exists():
        return
    fl = load_js(path)
    lines.append("")
    lines.append(f"💰 <b>수급</b> ({fl.get('unit', '억원')})")
    for key in ("kospi", "kosdaq", "futures"):
        mk = fl.get("markets", {}).get(key)
        if not mk:
            continue
        f, i, p = last(mk.get("foreign")), last(mk.get("inst_total")), last(mk.get("individual"))
        name = mk.get("name", key)
        lines.append(
            f"   {name} 외인 {num(f, sign=True)} / 기관 {num(i, sign=True)} / 개인 {num(p, sign=True)}"
        )


def section_rs(lines, top=5, min_n=5):
    # min_n 은 rs.html 의 기본값(state.minN)과 맞춘다. 대시보드 '테마 랭킹'에
    # 보이는 순위와 요약이 달라지면 어느 쪽이 맞는지 헷갈리므로 기준을 하나로 둔다.
    path = BASE / "reports" / "rs_data.js"
    if not path.exists():
        return
    rs = load_js(path)
    themes = [t for t in rs.get("themes", []) if t.get("n", 0) >= min_n and t.get("rs") is not None]
    if not themes:
        return
    themes.sort(key=lambda t: t["rs"], reverse=True)
    lines.append("")
    lines.append(f"🔥 <b>RS 상위 테마</b> ({rs.get('asof', '')} 기준)")
    for t in themes[:top]:
        # 테마 RS 는 시총 가중이 아닌 단순 평균이라 소수 종목이 평균을 끌어올릴 수 있다.
        # 종목 수와 90+ 비율을 같이 붙여 그 왜곡 여부를 한 줄에서 판단하게 한다.
        bits = [f"{t['rs']:.0f}", f"{t['n']}종목"]
        if t.get("p90") is not None:
            bits.append(f"90+ {t['p90']:.0f}%")
        lines.append(f"   {html.escape(t['name'])} {' · '.join(bits)}")


def build_message():
    m = load_js(BASE / "reports" / "market_data.js")
    asof = m.get("asof", "")
    try:
        title_date = datetime.strptime(asof, "%Y-%m-%d").strftime("%m/%d")
    except ValueError:
        title_date = asof

    lines = [f"📌 <b>알파노트 요약</b> · {title_date}", ""]
    section_score(m, lines)
    section_index(m, lines)
    section_breadth(m, lines)
    section_flows(lines)
    section_rs(lines)

    # 오늘 갱신분이 아니면 눈에 띄게 표시한다 (수집기가 실패했는데 모르고 넘어가는 걸 막는다)
    if asof != datetime.now().strftime("%Y-%m-%d"):
        lines.append("")
        lines.append(f"⚠️ 최신 데이터가 {asof} 기준입니다.")

    lines.append("")
    lines.append(f'🔗 <a href="{DASHBOARD_URL}">알파노트 대시보드</a>')

    return "\n".join(lines)


# ---------------------------------------------------------------- 텔레그램

def api_call(token, method, params=None):
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
        return json.loads(r.read().decode())


def resolve_chat_id(cfg):
    """chat_id 가 없으면 getUpdates 로 찾아서 config 에 저장한다."""
    if cfg.get("chat_id"):
        return str(cfg["chat_id"])
    # offset=-1 로 마지막 업데이트를 명시적으로 가져온다. 인자 없는 getUpdates 는
    # 이미 확인 처리된 업데이트를 건너뛰어 빈 배열을 주는 경우가 있다 (2026-08-07 실사례).
    res = api_call(cfg["bot_token"], "getUpdates", {"offset": -1})
    for upd in res.get("result", []):
        chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
        if chat:
            cfg["chat_id"] = str(chat["id"])
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"chat_id 를 찾아 저장했습니다: {cfg['chat_id']} ({chat.get('first_name') or chat.get('title')})")
            return cfg["chat_id"]
    raise SystemExit(
        "chat_id 를 찾지 못했습니다. 텔레그램에서 봇에게 아무 메시지나 한 번 보낸 뒤 다시 실행하세요."
    )


def resolve_targets(cfg, which):
    """--to 값 → 보낼 chat_id 목록.

    dm      개인 대화방 (기본)
    channel config 의 channel_id (예: "@daily_alphanote") — 봇이 채널 관리자여야 한다
    both    둘 다
    """
    out = []
    if which in ("dm", "both"):
        out.append(resolve_chat_id(cfg))
    if which in ("channel", "both"):
        ch = cfg.get("channel_id")
        if not ch:
            raise SystemExit("config.json 에 channel_id 가 없습니다 (예: \"@daily_alphanote\").")
        out.append(str(ch))
    return out


def send(cfg, targets, msg):
    for chat_id in targets:
        res = api_call(cfg["bot_token"], "sendMessage", {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        if not res.get("ok"):
            raise SystemExit(f"발송 실패({chat_id}): {res}")
        print(f"발송 완료 → {chat_id}")


def add_target_arg(ap):
    ap.add_argument("--to", choices=("dm", "channel", "both"), default="dm",
                    help="보낼 곳 (기본 dm). channel 은 봇이 채널 관리자로 등록돼 있어야 한다")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 메시지만 출력")
    add_target_arg(ap)
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    msg = build_message()

    if args.dry_run:
        print(msg)
        return

    if not CONFIG_PATH.exists():
        raise SystemExit(f"설정 파일이 없습니다: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    send(cfg, resolve_targets(cfg, args.to), msg)
    print(f"({datetime.now():%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    main()
