# -*- coding: utf-8 -*-
"""카카오 OAuth 1회성 부트스트랩.

사전 준비 (README.md 참고 — 브라우저에서 직접):
  1. developers.kakao.com 에서 애플리케이션 생성
  2. 앱 키 > REST API 키 복사
  3. 카카오 로그인 활성화 + Redirect URI  http://localhost:8899/callback  등록
  4. 동의항목에서 "카카오톡 메시지 전송(talk_message)" 선택 동의 설정

실행:  python kakao_auth.py
  → 브라우저가 열리면 로그인·동의 → 자동으로 토큰 저장 → 테스트 메시지 발송
"""
import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kakao  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")

AUTH_URL = ("https://kauth.kakao.com/oauth/authorize"
            "?client_id={key}&redirect_uri={redirect}"
            "&response_type=code&scope=talk_message")


def _redirect_port():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return int(json.load(f).get("redirect_port", 8899))
    except Exception:  # noqa: BLE001
        return 8899


def _wait_for_code(port):
    """localhost 원샷 HTTP 서버로 ?code= 수신."""
    result = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = "<h3>인증 완료 — 이 창을 닫으세요.</h3>"
            else:
                result["error"] = qs.get("error_description", qs.get("error", ["?"]))[0]
                body = f"<h3>인증 실패: {result['error']}</h3>"
            data = f"<meta charset='utf-8'>{body}".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            done.set()

        def log_message(self, *a):  # 콘솔 소음 제거
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    done.wait(timeout=300)
    server.shutdown()
    if "code" not in result:
        raise SystemExit(f"인증 코드를 받지 못했습니다: {result.get('error', '5분 타임아웃')}")
    return result["code"]


def main():
    port = _redirect_port()
    redirect = f"http://localhost:{port}/callback"

    # 기존 토큰 파일이 있으면 REST 키 재사용 (재인증 시)
    rest_key = ""
    if os.path.exists(kakao.TOKENS_FILE):
        try:
            rest_key = kakao.load_tokens().get("rest_api_key", "")
        except Exception:  # noqa: BLE001
            pass
    if rest_key:
        print(f"기존 REST API 키 재사용: {rest_key[:6]}...")
    else:
        rest_key = input("카카오 REST API 키를 붙여넣으세요: ").strip()
        if not rest_key:
            raise SystemExit("REST API 키가 비어 있습니다.")

    url = AUTH_URL.format(key=rest_key, redirect=urllib.parse.quote(redirect, safe=""))
    print("\n브라우저에서 카카오 로그인·동의를 진행하세요.")
    print(f"(자동으로 열리지 않으면 직접 접속: {url})\n")
    webbrowser.open(url)
    code = _wait_for_code(port)

    status, resp = kakao._post(kakao.TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": rest_key,
        "redirect_uri": redirect,
        "code": code,
    })
    if status != 200 or "access_token" not in resp:
        err, desc = resp.get("error", "?"), resp.get("error_description", "?")
        print(f"토큰 교환 실패 (HTTP {status}): {err} / {desc}")
        if err == "invalid_client":
            print("→ REST API 키가 맞는지 확인하세요 (JavaScript 키 아님).")
        elif "redirect" in str(desc).lower() or err == "misconfigured":
            print(f"→ 카카오 로그인 > Redirect URI에 {redirect} 가 정확히 등록됐는지 확인하세요.")
        elif "scope" in str(desc).lower():
            print("→ 동의항목에서 talk_message(카카오톡 메시지 전송)를 활성화했는지 확인하세요.")
        raise SystemExit(1)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    kakao.save_tokens({
        "rest_api_key": rest_key,
        "access_token": resp["access_token"],
        "refresh_token": resp["refresh_token"],
        "access_token_at": now,
        "refresh_token_at": now,
    })
    print(f"토큰 저장 완료: {kakao.TOKENS_FILE}")

    kakao.send_message("[주가알림] 카카오 알림 연동 완료 ✅")
    print("테스트 메시지 발송 완료 — 카카오톡 '나와의 채팅'을 확인하세요.")


if __name__ == "__main__":
    main()
