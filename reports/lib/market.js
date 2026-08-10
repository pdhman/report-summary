// 분석 탭(차트·계절성) 공용 종목 해석·조회 헬퍼.
//
// 조회 경로
//   1) 유니버스 정적 파일  data/<티커>.json          (124종목, 전 이력)
//   2) 한국 전종목 브랜치  chart-data/<티커>.json     (2,700여 종목, raw 는 CORS 허용)
//   3) 분석 서버 API       localhost:8766             (서버를 켠 PC에서만)
//
// 검색창에 '코미코' 같은 한글 종목명을 넣어도 되도록 KRX_INDEX(data/krx.js)로
// 종목명·6자리 코드를 야후 형식 티커(183300.KQ)로 바꿔준다.
"use strict";

const MARKET = (() => {
  const KR_DATA_URL = "https://raw.githubusercontent.com/pdhman/report-summary/chart-data/";
  const isKr = s => /^\d{6}\.(KS|KQ)$/.test(s);

  // '코미코' → 183300.KQ · '183300' → 183300.KQ · 그 외는 대문자 티커
  function resolve(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    const up = s.toUpperCase();
    if (typeof TICKER_INDEX !== "undefined" && TICKER_INDEX.some(e => e.s === up)) return up;
    if (typeof KRX_INDEX === "undefined") return up;

    const code = up.match(/^(\d{6})(\.(KS|KQ))?$/);
    if (code) {
      const hit = KRX_INDEX.find(r => r[0] === code[1]);
      return hit ? `${hit[0]}.${hit[2]}` : up;
    }
    // 한글/영문 종목명: 완전일치 → 접두일치 순
    const hit = KRX_INDEX.find(r => r[1] === s) || KRX_INDEX.find(r => r[1].startsWith(s));
    return hit ? `${hit[0]}.${hit[2]}` : up;
  }

  // 소스에 따라 시가/고가/저가가 0으로 채워진 봉이 섞인다(거래 없는 날 종가만 이월).
  // 그대로 그리면 0 에서 시작하는 캔들이 생겨 가격축이 망가지므로 종가로 평평하게 만든다.
  function sanitize(bars) {
    const out = [];
    for (const b of bars) {
      const c = b[4];
      if (!(c > 0)) continue;                       // 종가조차 없으면 버린다
      out.push(b[1] > 0 && b[2] > 0 && b[3] > 0
        ? b
        : [b[0], c, c, c, c, b[5] || 0, ...(b.length > 6 ? [b[6]] : [])]);
    }
    return out;
  }

  // 한국 전종목 브랜치. bars 는 [ymd,o,h,l,c,v] 6필드(수정종가 없음).
  async function fromKr(symbol) {
    if (!isKr(symbol)) return null;
    try {
      const res = await fetch(KR_DATA_URL + symbol + ".json");
      if (!res.ok) return null;
      const d = await res.json();
      if (!d.bars || !d.bars.length) return null;
      return { symbol, name: d.name, currency: d.currency || "KRW",
               note: d.note || "", bars: sanitize(d.bars), source: "kr-branch" };
    } catch (e) { return null; }
  }

  // 유니버스 정적 파일. bars 는 [ymd,o,h,l,c,v,adj] 7필드.
  async function fromStatic(symbol) {
    try {
      const res = await fetch(`data/${encodeURIComponent(symbol.replace("^", "_"))}.json`);
      if (!res.ok) return null;
      const d = await res.json();
      return { symbol, name: d.name, currency: d.currency || "",
               note: d.note || "", bars: d.bars, source: "static" };
    } catch (e) { return null; }
  }

  async function fromApi(symbol, path) {
    const API = location.port === "8766" ? "" : "http://localhost:8766";
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 2500);
    try {
      const res = await fetch(`${API}${path}?symbol=${encodeURIComponent(symbol)}`,
                              { signal: ctl.signal });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    } finally { clearTimeout(timer); }
  }

  // 한국 종목으로 해석은 됐는데 데이터가 없는 경우와, 아예 못 찾은 경우를 구분해
  // 안내한다(전자는 수집 대상에서 빠진 종목이라 사용자가 할 수 있는 일이 없다).
  function notFound(symbol) {
    if (isKr(symbol)) {
      const name = krxName(symbol);
      return new Error(`${name ? name + "(" + symbol + ")" : symbol}: 아직 수집되지 않은 종목입니다. `
        + `주간 수집에 반영되면 자동으로 조회됩니다.`);
    }
    return new Error(`${symbol}: 찾을 수 없는 종목입니다. `
      + `한국 주식은 종목명(예: 코미코)이나 6자리 코드로, 해외 종목은 티커로 검색하세요.`);
  }

  function krxName(symbol) {
    if (typeof KRX_INDEX === "undefined") return null;
    const m = symbol.match(/^(\d{6})\./);
    const hit = m && KRX_INDEX.find(r => r[0] === m[1]);
    return hit ? hit[1] : null;
  }

  return { resolve, fromKr, fromStatic, fromApi, isKr, notFound, krxName, KR_DATA_URL };
})();
