# -*- coding: utf-8 -*-
"""
리서치 종목 새 공시 → 텔레그램 DM 알림 (매일 08:20 · 작업명 리서치공시_0820_알림)

무엇을 보나
  docs/thesis/index.json 에 게시된 종목(종목 리서치 PPT 변환본 + /thesis 심층 리포트) 전부를
  OpenDART list.json 으로 종목별 조회해, 마지막 확인일 이후 새로 뜬 공시를 모은다.

무엇을 보내나
  공시 제목을 종류별로 분류(정기보고서·잠정실적·수주·자본변화·지분·IR·소송·기타)하고
  리서치 본문에서 손봐야 할 STEP 을 붙여 개인 대화방(dm)으로 보낸다. 새 공시가 없으면 아무것도 보내지 않는다.
  같은 공시(rcept_no)는 telegram/dart_alert_state.json 에 기록해 두 번 보내지 않는다.

실행
  python -X utf8 telegram/send_dart_alert.py                 # 마지막 확인일~오늘, dm 발송
  python -X utf8 telegram/send_dart_alert.py --days 7 --dry-run   # 최근 7일을 발송 없이 화면에만
  python -X utf8 telegram/send_dart_alert.py --codes 031980 --days 30 --force   # 특정 종목, 중복 무시

필요한 것: secrets.json 의 dart_api_key, telegram/config.json 의 bot_token·chat_id, 루트의 dart_client.py(고유번호 매핑)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJ))
from send_summary import CONFIG_PATH, resolve_targets, send  # noqa: E402

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

INDEX = PROJ / "docs" / "thesis" / "index.json"
STATE = HERE / "dart_alert_state.json"
SITE = "https://pdhman.github.io/report-summary/thesis.html#c="
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# 공시 제목 → (종류, 손볼 곳). 위에서부터 먼저 맞는 규칙을 쓴다.
# STEP 번호는 종목 리서치(12 STEP) 기준. /thesis 심층 리포트는 번호 대신 항목 이름만 보여준다.
RULES = [
    (r"분기보고서|반기보고서|사업보고서", "정기보고서(실적)", "실적 전달·기대 수준 재점검", "STEP 7·8·11 + 자동 숫자 카드"),
    (r"영업\s*\(잠정\)\s*실적|매출액또는손익구조|손익구조.*변동|실적", "잠정 실적", "실적 전달·기대 수준·철회 조건", "STEP 7·8·11"),
    (r"단일판매|공급계약|수주|계약체결", "수주·공급계약", "증거·미반영·촉매", "STEP 4·6·9·10"),
    (r"유상증자|무상증자|전환사채|신주인수권|교환사채|합병|분할|자기주식|주식소각|감자|유형자산\s*취득|타법인.*취득", "자본·구조 변화", "리스크·논리 철회 조건", "STEP 9·11"),
    (r"최대주주|대량보유|임원ㆍ주요주주|주요주주|특정증권", "지분 변동", "수급·지배구조", "STEP 1·11"),
    (r"기업설명회|IR\b|조회공시|풍문|보도", "IR·조회공시", "다음 촉매", "STEP 10"),
    (r"공정공시|가이던스|전망", "공정공시(가이던스)", "기대 수준·촉매", "STEP 8·10"),
    (r"소송|판결|제재|감리", "소송·제재", "리스크", "STEP 9·11"),
    (r"주주총회|임원\s*선임|대표이사|배당|현금ㆍ현물", "주총·배당·임원", "참고", ""),
]
PRIORITY = {"정기보고서(실적)": 0, "잠정 실적": 0, "수주·공급계약": 1, "자본·구조 변화": 1, "소송·제재": 2,
            "공정공시(가이던스)": 2, "지분 변동": 3, "IR·조회공시": 3, "주총·배당·임원": 4, "기타": 5}


def log(msg: str):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}")


def dart_key() -> str:
    try:
        with open(PROJ / "secrets.json", encoding="utf-8") as f:
            return str(json.load(f).get("dart_api_key") or "").strip()
    except Exception:
        return ""


def corp_map(key: str) -> dict:
    """종목코드 → DART 고유번호. 루트 dart_client.py 의 캐시(.dart_cache/corp_map.json, 하루 1회 갱신)를 그대로 쓴다."""
    os.environ.setdefault("DART_API_KEY", key)
    import dart_client  # noqa: WPS433  (루트 모듈)
    return dart_client._corp_map()


def classify(nm: str):
    for pat, kind, what, steps in RULES:
        if re.search(pat, nm):
            return kind, what, steps
    return "기타", "참고", ""


def fetch_list(key: str, corp_code: str, bgn: str, end: str) -> list[dict]:
    try:
        r = requests.get(DART_LIST, params={"crtfc_key": key, "corp_code": corp_code, "bgn_de": bgn, "end_de": end,
                                            "page_count": 100, "page_no": 1}, timeout=20)
        j = r.json()
    except Exception as e:
        log(f"  DART 조회 실패 {corp_code}: {e}")
        return []
    st = j.get("status")
    if st == "013":            # 조회 결과 없음
        return []
    if st != "000":
        log(f"  DART 오류 {corp_code}: {st} {j.get('message')}")
        return []
    out = []
    for x in j.get("list") or []:
        nm = re.sub(r"\s+", " ", str(x.get("report_nm") or "")).strip()
        out.append({"rcept": x.get("rcept_no"), "d": str(x.get("rcept_dt") or ""), "nm": nm,
                    "corrected": "정정" in nm, "flr": x.get("flr_nm") or "", "rm": x.get("rm") or ""})
    return out


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def fmt_d(ymd: str) -> str:
    return f"{ymd[4:6]}/{ymd[6:8]}" if len(ymd) >= 8 else ymd


def build_blocks(found: list[tuple[dict, list[dict]]]) -> list[str]:
    """종목별 메시지 블록(HTML)."""
    blocks = []
    for meta, items in found:
        items = sorted(items, key=lambda h: (PRIORITY.get(h["kind"], 5), h["d"]), reverse=False)
        is_res = meta.get("src") == "research"
        head = (f"<b>{html.escape(meta['name'])}</b> ({meta['code']}) · "
                f"{'리서치' if is_res else '심층'} {meta.get('date', '')}"
                f"{(' · ' + str(meta['score']) + '/' + str(meta.get('scale') or 30)) if meta.get('score') is not None else ''}")
        lines = [head]
        shown = items[:10]
        for h in shown:
            title = html.escape(h["nm"])
            guide = h["what"] + (f" ({h['steps']})" if is_res and h["steps"] else "")
            lines.append(f"• {fmt_d(h['d'])} <a href=\"{DART_VIEW.format(h['rcept'])}\">{title}</a>")
            lines.append(f"   ↳ {h['kind']} → {guide}")
        if len(items) > len(shown):
            lines.append(f"   … 외 {len(items) - len(shown)}건")
        lines.append(f"<a href=\"{SITE}{meta['code']}\">논리 탭에서 열기</a>")
        blocks.append("\n".join(lines))
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default="dm", help="dm | channel | both | 별칭 (기본 dm)")
    ap.add_argument("--days", type=int, help="마지막 확인일 대신 최근 N일을 본다")
    ap.add_argument("--codes", help="쉼표로 구분한 종목코드만 본다")
    ap.add_argument("--dry-run", action="store_true", help="발송·상태 저장 없이 화면 출력만")
    ap.add_argument("--force", action="store_true", help="이미 보낸 공시도 다시 포함")
    a = ap.parse_args()

    key = dart_key()
    if not key:
        raise SystemExit("secrets.json 에 dart_api_key 가 없습니다")
    if not INDEX.exists():
        raise SystemExit(f"{INDEX} 없음 — 게시된 리서치가 없습니다")
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    codes = [c.strip().zfill(6) for c in a.codes.split(",")] if a.codes else list(idx.keys())

    today = dt.date.today()
    state = load_state()
    sent: list[str] = state.get("sent", [])
    if a.days:
        bgn = today - dt.timedelta(days=a.days)
    elif state.get("last_checked"):
        bgn = dt.datetime.strptime(state["last_checked"], "%Y%m%d").date()   # 확인일 당일도 다시 본다(늦게 올라온 공시)
    else:
        bgn = today - dt.timedelta(days=3)
    bgn_s, end_s = bgn.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    log(f"조회 {bgn_s}~{end_s} · {len(codes)}종목 · 이미 보낸 공시 {len(sent)}건")

    cmap = corp_map(key)
    found: list[tuple[dict, list[dict]]] = []
    new_rcepts: list[str] = []
    for code in codes:
        meta = {"code": code, **(idx.get(code) or {"name": code})}
        corp = cmap.get(code)
        if not corp:
            log(f"  {code} 고유번호 없음 — 건너뜀")
            continue
        lst = fetch_list(key, corp, bgn_s, end_s)
        time.sleep(0.15)
        items = []
        for h in lst:
            if not a.force and h["rcept"] in sent:
                continue
            h["kind"], h["what"], h["steps"] = classify(h["nm"])
            items.append(h)
        log(f"  {meta['name']} ({code}): 공시 {len(lst)}건 중 새 공시 {len(items)}건")
        if items:
            found.append((meta, items))
            new_rcepts += [h["rcept"] for h in items]

    if not found:
        log("새 공시 없음 — 발송하지 않음")
        if not a.dry_run:
            state["last_checked"] = end_s
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return

    title = f"📂 리서치 종목 새 공시 — {today:%m/%d} 기준 (조회 {fmt_d(bgn_s)}~{fmt_d(end_s)})"
    blocks = build_blocks(found)
    # 텔레그램 한 메시지 4096자 제한 → 종목 블록 단위로 나눠 보낸다
    msgs, cur = [], title
    for b in blocks:
        if len(cur) + len(b) + 2 > 3800:
            msgs.append(cur)
            cur = b
        else:
            cur = cur + "\n\n" + b
    msgs.append(cur)

    if a.dry_run:
        for m in msgs:
            print("-" * 60)
            print(re.sub(r"<[^>]+>", "", html.unescape(m)))
        print("-" * 60)
        print(f"(dry-run) 메시지 {len(msgs)}개 · 공시 {len(new_rcepts)}건 — 발송·저장 안 함")
        return

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = resolve_targets(cfg, a.to)
    for m in msgs:
        send(cfg, targets, m)
    state["last_checked"] = end_s
    state["sent"] = (sent + new_rcepts)[-500:]
    state["last_sent"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"발송 완료 · 메시지 {len(msgs)}개 · 공시 {len(new_rcepts)}건")


if __name__ == "__main__":
    main()
