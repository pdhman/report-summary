# -*- coding: utf-8 -*-
"""카카오 "나에게 보내기" — 토큰 수명 관리 + 메시지 발송.

토큰 파일(kakao_tokens.json, gitignore됨):
  {"rest_api_key": ..., "access_token": ..., "refresh_token": ...,
   "access_token_at": ISO시각, "refresh_token_at": ISO시각}

수명 규칙:
  · access_token ~6시간 → 매 실행 시작 시 무조건 refresh.
  · refresh_token ~2개월. 남은 수명이 1개월 미만이면 refresh 응답에
    새 refresh_token이 실려 오므로 즉시 저장한다(로테이션).
  · refresh 실패(invalid_grant 등) → ReauthNeeded 예외. 호출부는
    exit code 3으로 종료해 래퍼가 재인증 필요를 구분할 수 있게 한다.
"""
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENS_FILE = os.path.join(HERE, "kakao_tokens.json")

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TEXT_LIMIT = 200  # 텍스트 템플릿 최대 길이


class ReauthNeeded(Exception):
    """refresh token 만료 — kakao_auth.py 재실행 필요."""


def load_tokens(path=TOKENS_FILE):
    if not os.path.exists(path):
        raise ReauthNeeded(f"토큰 파일 없음: {path} — kakao_auth.py 를 먼저 실행하세요")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_tokens(tokens, path=TOKENS_FILE):
    """원자적 저장 (임시 파일 → os.replace)."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


def refresh_access_token(tokens=None, path=TOKENS_FILE):
    """access token 갱신. 갱신된 tokens dict 반환. 실패 시 ReauthNeeded."""
    tokens = tokens or load_tokens(path)
    status, resp = _post(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": tokens["rest_api_key"],
        "refresh_token": tokens["refresh_token"],
    })
    if status != 200 or "access_token" not in resp:
        raise ReauthNeeded(
            f"토큰 갱신 실패 (HTTP {status}: {resp.get('error', '?')}"
            f"/{resp.get('error_description', '?')}) — kakao_auth.py 를 다시 실행하세요")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    tokens["access_token"] = resp["access_token"]
    tokens["access_token_at"] = now
    if "refresh_token" in resp:  # 로테이션 — 새 토큰 즉시 보존
        tokens["refresh_token"] = resp["refresh_token"]
        tokens["refresh_token_at"] = now
    save_tokens(tokens, path)
    return tokens


def _chunks(text, limit=TEXT_LIMIT):
    """줄 단위로 limit 이하 덩어리로 분할. 한 줄이 limit를 넘으면 강제 절단."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def send_message(text, tokens=None, path=TOKENS_FILE, _retry=True):
    """나와의 채팅으로 텍스트 발송. 200자 초과 시 분할 발송."""
    tokens = tokens or load_tokens(path)
    for chunk in _chunks(text):
        template = json.dumps({
            "object_type": "text",
            "text": chunk,
            "link": {"web_url": "https://m.stock.naver.com",
                     "mobile_web_url": "https://m.stock.naver.com"},
        }, ensure_ascii=False)
        status, resp = _post(
            SEND_URL, {"template_object": template},
            headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if status == 401 and _retry:
            tokens = refresh_access_token(tokens, path)
            return send_message(text, tokens, path, _retry=False)
        if status != 200 or resp.get("result_code") != 0:
            raise RuntimeError(f"카카오 발송 실패 (HTTP {status}): {resp}")
    return tokens
