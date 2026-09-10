"""
JSON 저장소 (docs/data/ — 저장소 추적 대상이라 GitHub Actions 러너와 로컬이 같은 상태를 공유)
- polymarket_history.json : 실행마다 레짐·축 점수·핵심 시장 확률 누적 (추이 차트용)
- polymarket_state.json   : 알림 발송 이력 (cooldown 판정)
"""
from __future__ import annotations

import json
import os
import time

import config as C


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:            # noqa: BLE001
        return default


def _save(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ── 히스토리 ──
def load_history() -> list[dict]:
    h = _load(C.HISTORY_JSON, [])
    return h if isinstance(h, list) else []


def append_history(row: dict) -> list[dict]:
    hist = load_history()
    # 같은 시각(분 단위) 중복 실행은 덮어쓴다
    key = int(row["ts"]) // 60
    hist = [r for r in hist if int(r.get("ts", 0)) // 60 != key]
    hist.append(row)
    hist.sort(key=lambda r: r["ts"])
    hist = hist[-C.HISTORY_MAX_ROWS:]
    _save(C.HISTORY_JSON, hist)
    return hist


# ── 알림 상태 ──
def _state() -> dict:
    s = _load(C.STATE_JSON, {})
    return s if isinstance(s, dict) else {}


def in_cooldown(market_id: str, direction: int, hours: float) -> bool:
    ts = _state().get("alerts", {}).get(f"{market_id}:{direction}")
    return bool(ts and time.time() - ts < hours * 3600)


def log_alert(market_id: str, pmss: float, direction: int):
    s = _state()
    s.setdefault("alerts", {})[f"{market_id}:{direction}"] = int(time.time())
    # 30일 지난 항목 정리
    cutoff = time.time() - 30 * 86400
    s["alerts"] = {k: v for k, v in s["alerts"].items() if v >= cutoff}
    s.setdefault("log", []).append({"ts": int(time.time()), "market_id": market_id,
                                    "pmss": pmss, "direction": direction})
    s["log"] = s["log"][-200:]
    _save(C.STATE_JSON, s)


def recent_alerts(n: int = 20) -> list[dict]:
    return list(reversed(_state().get("log", [])[-n:]))
