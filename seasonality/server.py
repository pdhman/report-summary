# -*- coding: utf-8 -*-
"""알파노트 + 분석(차트·계절성) 서버 (포트 8766).

reports/(알파노트 전체)를 정적으로 서빙하고, yfinance 프록시 API를 함께 제공한다.
    GET /api/ohlcv?symbol=AAPL   → 일봉 OHLCV (차트용, 무조정 실거래가)
    GET /api/closes?symbol=AAPL  → 수정종가 (계절성용, 배당·분할 반영)
둘 다 10분 캐시. API에는 CORS를 허용해 file:// 나 8778(정적 서버)로 연
알파노트에서도 호출할 수 있다.

실행: python seasonality/server.py  (launch.json의 seasonality 항목이 이걸 실행)
"""
import math
import time
from pathlib import Path

import yfinance as yf
from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
REPORTS = BASE.parent / "reports"
app = Flask(__name__)

_cache = {}
TTL = 600  # 초


@app.after_request
def allow_cors(resp):
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/")
def root():
    return send_from_directory(REPORTS, "index.html")


@app.get("/<path:name>")
def static_files(name):
    return send_from_directory(REPORTS, name)


@app.get("/api/ohlcv")
def ohlcv():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol 파라미터가 필요합니다"}), 400

    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < TTL:
        return jsonify(hit[1])

    tk = yf.Ticker(symbol)
    try:
        # auto_adjust=False: 액면분할만 반영된 실거래가 (일반 차트 사이트와 동일)
        hist = tk.history(period="max", auto_adjust=False)
    except Exception as e:
        return jsonify({"error": f"{symbol} 조회 실패: {e}"}), 502
    if hist.empty:
        return jsonify({"error": f"{symbol}: 데이터를 찾을 수 없습니다"}), 404

    bars = []
    for row in hist.itertuples():
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        if any(math.isnan(v) for v in (o, h, l, c)):
            continue
        v = 0 if math.isnan(row.Volume) else int(row.Volume)
        bars.append({
            "t": row.Index.strftime("%Y-%m-%d"),
            "o": round(o, 4), "h": round(h, 4), "l": round(l, 4), "c": round(c, 4),
            "v": v,
        })

    meta = {}
    try:
        meta = tk.history_metadata or {}
    except Exception:
        pass
    payload = {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "currency": meta.get("currency") or "",
        "bars": bars,
    }
    _cache[symbol] = (time.time(), payload)
    return jsonify(payload)


@app.get("/api/closes")
def closes():
    """계절성 분석용 수정종가 시계열 (배당·분할 반영, SEASONALITY_DATA 항목과 동일 형식)."""
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol 파라미터가 필요합니다"}), 400

    key = f"closes:{symbol}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return jsonify(hit[1])

    tk = yf.Ticker(symbol)
    try:
        hist = tk.history(period="max", auto_adjust=True)
    except Exception as e:
        return jsonify({"error": f"{symbol} 조회 실패: {e}"}), 502
    ser = hist["Close"].dropna() if not hist.empty else hist
    if len(ser) == 0:
        return jsonify({"error": f"{symbol}: 데이터를 찾을 수 없습니다"}), 404

    meta = {}
    try:
        meta = tk.history_metadata or {}
    except Exception:
        pass
    payload = {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "dates": [int(d.strftime("%Y%m%d")) for d in ser.index],
        "closes": [round(float(c), 4) for c in ser],
    }
    _cache[key] = (time.time(), payload)
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8766, threaded=True)
