# -*- coding: utf-8 -*-
"""보유종목 급등락 카카오톡 알림 — 메인 진입점.

작업 스케줄러가 평일 09:00~15:30 10분 간격으로 실행한다.
보유종목은 prop-dashboard/data.js 에서 읽고(전일 종가 대비 등락률 기준),
단계별 임계값(기본 ±3/5/7%)을 돌파하면 카카오톡 "나와의 채팅"으로 알린다.
같은 단계·방향은 하루 1회만 알린다 (alert_state.json).

CLI:
  --test     테스트 메시지 1건 발송 후 종료 (장시간 가드 무시)
  --dry-run  전체 파이프라인 실행하되 발송·상태저장 없이 출력만
  --force    장시간 가드 무시하고 실제 실행

Exit code:  0 정상(알림 없음 포함) · 1 오류 · 3 카카오 재인증 필요
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kakao  # noqa: E402
import quote  # noqa: E402

KST = timezone(timedelta(hours=9))


def log(msg):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["data_js"] = os.path.normpath(os.path.join(HERE, cfg["data_js"]))
    cfg["state_file"] = os.path.join(HERE, cfg["state_file"])
    return cfg


def in_market_hours(cfg, now):
    if now.weekday() not in cfg["market_days"]:
        return False
    hhmm = now.strftime("%H:%M")
    return cfg["market_start"] <= hhmm <= cfg["market_end"]


def load_holdings(data_js):
    """감시 종목 목록 → [{name, code}].

    1순위: prop-dashboard/data.js (이 PC의 원본 기록)
    2순위: price-alert/holdings.json — data.js가 없는 다른 PC용 포터블 모드.
           형식: [{"name": "KODEX 200", "code": "069500"}, ...]
    """
    if not os.path.exists(data_js):
        alt = os.path.join(HERE, "holdings.json")
        if not os.path.exists(alt):
            log(f"ERROR: {data_js} 도 {alt} 도 없음 — 감시 종목을 알 수 없습니다")
            return []
        log("포터블 모드: holdings.json 사용")
        with open(alt, encoding="utf-8") as f:
            items = json.load(f)
        return [{"name": i.get("name", i["code"]), "code": str(i["code"])}
                for i in items if i.get("code")]

    sys.path.insert(0, os.path.dirname(data_js))
    import propdata as P  # noqa: N812
    _, data = P.load(data_js)
    day = P.last_portfolios(data)
    if not day:
        return []
    seen, out = set(), []
    for p in day.get("portfolios", []):
        for h in p.get("holdings", []):
            code = h.get("code")
            if not code:
                log(f"WARN: '{h.get('name', '?')}' 에 code 없음 — 이 종목은 감시 제외 "
                    f"(data.js에 code: \"069500\" 형식으로 추가하세요)")
                continue
            if code not in seen:
                seen.add(code)
                out.append({"name": h.get("name", code), "code": str(code)})
    return out


def load_state(path, today):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") == today:
            return state
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 새로 시작
        pass
    return {"date": today, "alerted": {}}


def save_state(state, path):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_thresholds(holdings, quotes, thresholds, state):
    """알림 대상 판정. (알림 줄 목록, 반영할 pending 상태) 반환.

    단계 규칙: |등락률| 이상인 임계값 중 최고 단계(top)가 오늘 이미 알린
    단계보다 높을 때만 알린다. +2%→+8% 점프면 7% 단계 1건, 되돌림 후
    재돌파는 침묵, 상방/하방은 독립 추적.
    """
    lines, pending = [], {}
    for h in holdings:
        q = quotes.get(h["code"])
        if not q:
            log(f"WARN: {h['name']}({h['code']}) 시세 없음 — 스킵")
            continue
        pct = q["change_pct"]
        direction = "up" if pct > 0 else "down"
        top = max((t for t in thresholds if abs(pct) >= t), default=None)
        if top is None:
            continue
        already = state["alerted"].get(h["code"], {}).get(direction, 0.0)
        if top <= already:
            continue
        arrow = "▲" if direction == "up" else "▼"
        lines.append(f"{arrow} {h['name']} {pct:+.1f}% ({top:g}% 단계)\n"
                     f"   현재 {q['price']:,} / 전일 {q['prev_close']:,}")
        pending.setdefault(h["code"], {})[direction] = top
    return lines, pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="테스트 메시지 발송 후 종료")
    ap.add_argument("--dry-run", action="store_true", help="발송·상태저장 없이 출력만")
    ap.add_argument("--force", action="store_true", help="장시간 가드 무시")
    args = ap.parse_args()

    cfg = load_config()
    now = datetime.now(KST)

    if args.test:
        kakao.refresh_access_token()
        kakao.send_message(f"[주가알림 테스트 {now:%H:%M}] 연동 정상 ✅")
        log("테스트 메시지 발송 완료")
        return 0

    if not (args.force or args.dry_run) and not in_market_hours(cfg, now):
        log("장시간 아님 — 종료")
        return 0

    holdings = load_holdings(cfg["data_js"])
    if not holdings:
        log("감시할 보유종목 없음 — 종료")
        return 0
    log(f"보유종목 {len(holdings)}개: " + ", ".join(f"{h['name']}({h['code']})" for h in holdings))

    if not args.dry_run:
        kakao.refresh_access_token()  # 조용한 날에도 토큰 문제를 표면화

    quotes = quote.fetch_quotes([h["code"] for h in holdings], log=log)
    if not quotes:
        log("ERROR: 시세 조회 전부 실패")
        return 1
    if all(q["market_status"] != "OPEN" for q in quotes.values()):
        log("휴장일 또는 장 미개장 — 종료")
        return 0

    today = now.date().isoformat()
    state = load_state(cfg["state_file"], today)
    lines, pending = check_thresholds(holdings, quotes, cfg["thresholds"], state)

    for h in holdings:  # 상태와 무관하게 현재 스냅샷은 로그에 남긴다
        q = quotes.get(h["code"])
        if q:
            log(f"  {h['name']}: {q['change_pct']:+.2f}% (현재 {q['price']:,})")

    if not lines:
        log("알림 조건 미충족")
        return 0

    msg = f"[주가알림 {now:%H:%M}]\n" + "\n".join(lines)
    if args.dry_run:
        log("dry-run — 발송 생략. 메시지:\n" + msg)
        return 0

    kakao.send_message(msg)  # 실패 시 예외 → 상태 미저장 → 다음 틱에 재시도
    for code, dirs in pending.items():
        state["alerted"].setdefault(code, {}).update(dirs)
    save_state(state, cfg["state_file"])
    log(f"알림 발송 완료 ({len(lines)}건)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kakao.ReauthNeeded as e:
        log(f"ERROR: {e}")
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR: {e}")
        sys.exit(1)
