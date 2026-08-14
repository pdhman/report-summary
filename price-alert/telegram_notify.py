# -*- coding: utf-8 -*-
"""텔레그램 발송 — 기존 알파노트 봇(telegram/config.json) 재활용.

봇이 보내는 메시지는 카카오 '나와의 채팅'과 달리 푸시 알림이 정상적으로 울린다.
설정 탐색 순서:
  1. ../telegram/config.json  (이 PC의 기존 봇 설정: bot_token, chat_id)
  2. ./telegram.json          (포터블용 — 다른 PC에서는 이 파일에 같은 형식으로 작성)
"""
import json
import os
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_CANDIDATES = [
    os.path.normpath(os.path.join(HERE, "..", "telegram", "config.json")),
    os.path.join(HERE, "telegram.json"),
]


def _config():
    for path in CONFIG_CANDIDATES:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("bot_token") and cfg.get("chat_id"):
                return cfg
    raise RuntimeError(
        "텔레그램 설정 없음 — telegram/config.json 또는 price-alert/telegram.json에 "
        '{"bot_token": ..., "chat_id": ...} 를 넣으세요')


def send_message(text):
    """개인 채팅(chat_id)으로 텍스트 발송. 실패 시 예외."""
    cfg = _config()
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"텔레그램 발송 실패 (HTTP {e.code}): {e.read().decode('utf-8', 'replace')[:200]}")
    if not data.get("ok"):
        raise RuntimeError(f"텔레그램 발송 실패: {data}")
