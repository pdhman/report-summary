# -*- coding: utf-8 -*-
"""알파노트 대시보드 핵심 지표를 텔레그램으로 요약 발송한다.

데이터는 이미 각 수집기가 만들어 둔 JS 파일에서 읽는다 (재수집 없음):
  docs/market_data.js        시장 건전성 (평일 16:35)
  수급모니터링/dashboard_data.js  투자자별 수급 (평일 15:40)
  docs/rs_data.js            RS 스크리너 (주간)

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
    path = BASE / "docs" / "rs_data.js"
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


def section_etf(lines, top=5):
    """RS 상위 ETF — rs.html ETF 모드 기본값과 동일하게 레버리지·인버스 제외.

    etfs 배열 필드: [코드, 이름, 시장, 시총, 대금, RS, 1M, 3M, 6M, 12M,
                    고점대비, MA200, 유형idx, -1, [], 레버플래그]
    """
    path = BASE / "docs" / "rs_data.js"
    if not path.exists():
        return
    rs = load_js(path)
    cats = rs.get("etfCats", [])
    etfs = [e for e in rs.get("etfs", [])
            if e[5] is not None and not e[15]]      # RS 있음 + 레버·인버스 제외
    if not etfs:
        return
    etfs.sort(key=lambda e: e[5], reverse=True)
    lines.append("")
    lines.append("🧺 <b>RS 상위 ETF</b> (레버리지·인버스 제외)")
    for e in etfs[:top]:
        cat = cats[e[12]]["name"] if 0 <= e[12] < len(cats) else ""
        bits = [f"{e[5]:.0f}"]
        if cat:
            bits.append(cat)
        if e[10] is not None:                       # 52주 고점대비
            bits.append(f"고점 {e[10]:.0f}%")
        lines.append(f"   {html.escape(e[1])} {' · '.join(bits)}")


def build_message():
    m = load_js(BASE / "docs" / "market_data.js")
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
    section_etf(lines)

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
    """--to 값 → 보낼 chat_id 목록. 쉼표로 여러 개를 지정할 수 있다.

    dm       개인 대화방
    channel  config 의 channel_id (기본 채널 @daily_alphanote)
    both     dm + channel
    <별칭>   config 의 channels 맵에 등록한 이름 (예: rapha)
    @이름    채널 주소를 그대로

    메시지 종류마다 보낼 곳이 다르므로(시장 요약은 기본 채널만, X 모니터링은
    두 채널) 이렇게 조합할 수 있게 뒀다. 예: --to channel,rapha
    """
    channels = cfg.get("channels") or {}
    out = []

    def add(v):
        if v not in out:      # 같은 곳에 두 번 보내지 않는다
            out.append(v)

    for token in [t.strip() for t in str(which).split(",") if t.strip()]:
        if token in ("dm", "both"):
            add(resolve_chat_id(cfg))
        if token in ("channel", "both"):
            ch = cfg.get("channel_id")
            if not ch:
                raise SystemExit('config.json 에 channel_id 가 없습니다 (예: "@daily_alphanote").')
            add(str(ch))
        elif token in channels:
            add(str(channels[token]))
        elif token.startswith("@") or token.lstrip("-").isdigit():
            add(token)
        elif token not in ("dm", "channel", "both"):
            known = ", ".join(["dm", "channel", "both", *channels])
            raise SystemExit(f"--to 값을 알 수 없습니다: {token} (가능: {known}, 또는 @채널이름)")
    if not out:
        raise SystemExit("--to 에 보낼 곳이 지정되지 않았습니다.")
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
    ap.add_argument("--to", default="dm",
                    help="보낼 곳 (기본 dm). dm/channel/both/별칭/@채널이름 을 "
                         "쉼표로 여러 개 지정 가능. 예: --to channel,rapha")


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
