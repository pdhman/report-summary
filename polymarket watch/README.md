# Polymarket Macro Watch (PMSS)

Polymarket 공개 데이터 API 를 **실시간 매크로 데이터 공급원**으로 써서
`이벤트 확률 충격 → 유동성 필터 → 금리·FX·선물 확인 → Macro Regime → PMSS 0~100 → 대시보드/알림`
까지 자동화한다. 거래 기능은 없다.

```
Polymarket (선행 신호)  →  2Y/10Y/DXY/VIX/NQ/SMH/USDKRW (확인 신호)  →  Net Exposure 조절 (실행)
```

대시보드: `docs/polymarket.html` → https://pdhman.github.io/report-summary/polymarket.html (알파노트 홈 카드 "폴리마켓 매크로")

## 운영 (GitHub Actions 가 기본)

`.github/workflows/polymarket-watch.yml` — **4시간마다**(KST 08:23 / 12:23 / 16:23 / 20:23 / 00:23 / 04:23)
러너에서 수집 → `docs/polymarket.html` + `docs/data/polymarket_*.json` 커밋·푸시(= 배포).
GitHub Actions 탭에서 "Run workflow" 로 수동 실행 가능. `polymarket watch/` 소스를 push 해도 한 번 돈다.

**국내 IP 에서는 gamma/clob/data API 가 모두 HTTP 451(지역 차단)** 이다. 클라이언트가 451 을 만나면
`r.jina.ai` 리더 프록시로 자동 전환한다(`PMW_PROXY=auto`, 분당 20회 → 전체 실행 5~6분).
러너(미국 IP)에서는 직접 호출이 되면 1분 안에 끝난다.

## 로컬 실행

```bash
pip install -r requirements.txt
cd "polymarket watch"
python main.py run --dry            # 히스토리·알림 상태 저장 없이 HTML 만 (프록시 경유 ~6분)
python main.py run                  # 1회 실행 → docs/polymarket.html + JSON 갱신
python main.py run --max-history 12 # CLOB 히스토리 호출을 12개로 제한 (빠른 점검)
python main.py loop --every 60      # 60분마다 반복
```

환경변수
- `PMW_PROXY` = auto(기본) / always / never
- `PMW_HTML` = 출력 경로 (기본 `docs/polymarket.html`)
- `PMW_TG_TOKEN`, `PMW_TG_CHAT` = 텔레그램 알림. 미설정이면 알림은 로그로만 남는다.
  `PMW_TG_FALLBACK=1` 이면 알파노트 봇(`telegram/config.json`)을 재사용한다(기본 꺼짐).
  Actions 에서 알림을 받으려면 저장소 Secrets 에 `PMW_TG_TOKEN`, `PMW_TG_CHAT` 등록.

## 구조

| 파일 | 역할 |
|---|---|
| `config.py` | API/프록시, 태그, **GROUPS(선별 정규식·극성)**, KEY_SERIES(추이 차트), 유동성·알림 기준, 자산 기대방향 |
| `polymarket_client.py` | Gamma(태그별 이벤트) / CLOB(히스토리) 호출, 451 → 프록시 폴백, 구간 라벨 파서 |
| `selector.py` | 이벤트 분류 → kind(binary/dist/ladder) 판정 → 시장 극성·구간값, 마감 지난 시장 제외 |
| `signals.py` | ΔP(1H/6H/24H/7D), logit Z-score, 유동성 등급, persistence (히스토리 없으면 Gamma 변화값으로 lite) |
| `cross_asset.py` | yfinance 24h 변화 vs 기대방향 채점, Divergence Type |
| `pmss.py` | 100점 모델, Regime 4분면, Net Exposure 조절 |
| `store.py` | `docs/data/polymarket_history.json`(실행별 레짐·확률 누적), `polymarket_state.json`(알림 cooldown) |
| `report.py` | 콘솔 표 / 텔레그램 메시지 / `template.html` 렌더(알파노트 내비 주입) / 요약 JSON |
| `template.html` | 대시보드 템플릿 (`__DATA_JSON__` 치환, 인라인 SVG, 다크모드) |
| `main.py` | 오케스트레이션, CLI |

## 시장 선별 방식

1. Gamma `/events?tag_slug=` 로 태그(economy, fed-rates, macro-indicators, Global-Rates, inflation, treasuries,
   geopolitics, oil, tariffs, trade-war, government-shutdown, jobs-report, gdp, china)별 활성 이벤트를 거래량순 100개씩 수집.
2. `GROUPS` 의 include/exclude 정규식(slug+title)으로 FED · INFLATION · GROWTH · RATES · GLOBAL_CB · TRADE · FISCAL · ENERGY · GEOPOLITICS 분류. 어디에도 안 걸리면 버림.
3. 이벤트 종류: 시장 1개 = **binary**, negRisk(상호배타 구간) = **dist**, 그 외 = **ladder**(날짜·임계값 사다리).
4. dist 는 구간 라벨을 숫자로 파싱해 **내재 기대값 μ** 를 계산하고, μ 위/아래 구간에 ±극성(× ev_sign)을 준다.
   binary/ladder 는 질문 키워드 규칙(polarity)으로 극성 부여. 극성 0 이면 레짐·Cross-asset 집계에서 제외(표시만).
5. CLOB 1주 60분봉은 거래량 상위 `MAX_SIGNAL_HISTORY`(70)개만 받고(프록시 제한), 나머지는 Gamma 의 1h/24h 변화로 lite 신호.

## PMSS 계산

| 항목 | 배점 | 산식 |
|---|---:|---|
| Probability Shock | 30 | max(\|Z\|/3, \|ΔP24h\|/25%p) × 30 |
| Cross-Asset Confirmation | 30 | (일치−불일치)/전체 × 30, 음수는 0 |
| Liquidity | 15 | A=15 / B=10 / X=0 |
| Persistence | 15 | 최근 6h 중 이동의 50% 이상 유지된 비율 × 15 |
| Event Importance | 10 | 그룹별 고정값 |

Z-score = ΔL / (σ_1h · √h), σ_1h = 1주일치 60분봉 logit 차분의 표준편차 (하한 0.02).

## 알림 조건

```
(|ΔP1H| ≥ 8%p  OR  |ΔP24H| ≥ 15%p  OR  |Z| ≥ 2)
AND Liquidity ≠ X   AND  PMSS ≥ 55   AND  같은 시장·방향 6시간 내 미발송
```

## 자주 손볼 곳

1. **분류가 놓치거나 잘못 잡은 이벤트** → `config.GROUPS[...]["include"/"exclude"]` 정규식, 또는 `MANUAL_EVENTS` 에 slug 등록.
2. **극성이 어색한 시장** → 해당 그룹의 `polarity` 규칙(첫 매치 우선). 분포형은 `ev_sign`.
3. **추이 차트에 넣을 시장** → `KEY_SERIES` (이벤트 slug 정규식 + 라벨 정규식). FOMC 회의는 달마다 slug 가 바뀌므로 갱신 필요.
4. **Gamma 필드명이 바뀐 경우** → `polymarket_client.parse_gamma_market()` 한 곳만 수정.
5. **2Y 금리 프록시** → yfinance 에 2Y 현물 티커가 없어 `ZT=F`(2Y 선물, 가격↑=금리↓)를 쓴다.
6. **프록시가 막히면** → `PROXY_URL` 교체. allorigins·corsproxy·codetabs 는 2026-09 기준 불가, r.jina.ai 만 동작.
