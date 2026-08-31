# 크립토 모니터

비트코인 핵심 지표를 무료 데이터로 수집해 단일 HTML 대시보드(`crypto.html`)를 생성한다.

## 구성 지표

| 섹션 | 지표 | 소스 |
|---|---|---|
| 가격·모멘텀 | BTC 가격 + MA50/MA200, RSI(14), ETH/BTC 상대강도 | Binance 현물 API |
| 밸류에이션 | MVRV 비율 (CryptoQuant와 동일 지표) | CoinMetrics 커뮤니티 API (무료·무키) |
| ETF 자금흐름 | 미국 현물 ETF 일별/누적 순유입 (2024-01~) | Farside Investors (HTML 파싱) |
| 파생상품 | 펀딩비(일평균), 미결제약정 OI | Binance 선물 API |

## 사용법

```
pip install requests
python crypto_monitor.py     # 수집 → crypto.html 생성
```

- 모든 날짜는 UTC 일봉 기준.
- **OI**는 바이낸스가 최근 30일만 제공 → 실행할 때마다 `oi_history.csv`에 누적 저장.
- **ETF 흐름**은 `etf_flow_history.csv`에 누적 캐시 — Farside 장애 시 캐시로 렌더.
- 신호 임계값은 `crypto_monitor.py`의 `build_signals()`에서 조정.

## 자동 실행

작업 스케줄러에 **`크립토모니터_1100_수집`** 으로 등록됨 (매일 11:00, `run_crypto.ps1`).

- **11시인 이유**: UTC 일봉이 09:00 KST에 마감되므로 그 뒤라야 전일 UTC 데이터(MVRV·OI·가격)가
  완성된다. 미국 ETF 세션은 05:00 KST 마감이고 Farside는 그날 UK 심야에 게시하므로
  11시면 전 세션 유입까지 들어온다.
- 배터리 실행 허용 + 놓친 실행 따라잡기 적용 (OI는 하루 걸러면 그 구간이 영구 유실되므로 필수).
- 로그는 `logs/crypto_*.log` (30일 보관). 수동 실행도 같은 스크립트를 쓰면 된다:
  `powershell -ExecutionPolicy Bypass -File run_crypto.ps1`
- `run_crypto.ps1`은 **UTF-8 BOM으로 저장**해야 한다 — 스케줄러의 PS 5.1이 BOM 없는 파일을
  CP949로 읽어 한글 경로·문자열을 깨뜨린다.

## 알파노트 연동

홈(index.html) 카드로 노출된다 — **시장 레버리지 바로 뒤, 8번째 카드**. 하단 내비의 6번째 탭이
아니라 카드 한 장이다(내비는 5칸 고정).

- `crypto_monitor.py` 가 대시보드와 함께 `crypto_summary.json`(카드용 요약)을 만든다.
- `make_summary.py` 의 `card_crypto()` 가 그 JSON을 읽어 카드를 만들고,
  `crypto.html` 을 `docs/` 로 복사하면서 하단 내비를 주입한다(수급 동향 `flow.html` 과 같은 방식).
  내비 CSS가 쓰는 사이트 변수(`--panel` 등)는 이 대시보드에 없어 `nav_shim` 으로 채워 넣는다.
- `run_crypto.ps1` 이 수집 직후 홈을 재생성하고 **직접 커밋·푸시**하므로 11:00 수집분이
  바로 사이트에 반영된다. 스테이징은 `git add docs` 만 한다(`crypto-monitor/` 는 git 비추적).
  `add -A` 는 금지 — 추적 외 개인 파일이 공개 저장소로 나간다(2026-08-03 실사고).
- **`make_summary.py` 를 고쳤으면 반드시 커밋할 것.** run_leverage(17:30)는 `--autostash`
  없이 `pull --rebase` 를 해서, 추적 파일에 미커밋 변경이 남아 있으면 그 작업이 실패한다.
- 페이지에 `MutationObserver` 로 `data-theme` 을 감시해 테마 전환 시 차트를 다시 그린다.
  사이트 공용 내비의 테마 스크립트가 페이지 자체의 `_tgTheme`(renderAll 호출)을 덮어쓰기 때문에
  이게 없으면 다크 전환 시 차트 색만 그대로 남는다.

## 알려진 데이터 함정

Farside는 아직 집계되지 않은 당일과 미국 증시 휴장일도 표에 행으로 게시하는데, 개별 종목 칸은
전부 `-`인 반면 **Total 칸만 `0.0`** 으로 찍힌다. Total만 읽으면 '순유입 0인 거래일'로 오인된다
(2026-08-31 발견 당시 17일 오염). `_parse_farside_page()`는 개별 종목 칸 중 최소 하나가 숫자인
행만 실거래일로 인정한다. 캐시 CSV가 이미 오염된 경우 파서 결과와 대조해 따로 정리해야 한다
(캐시를 먼저 읽고 병합하는 구조라 저절로 고쳐지지 않음).

## 대시보드 기능

- 기간 필터(30일/90일/180일/1년/전체) — 모든 차트 공통 적용
- 신호 요약 표(MVRV·RSI·MA200·펀딩비·ETF 5일·OI 7일 신호등)
- 크로스헤어 툴팁, 표 보기(각 차트), 다크/라이트 테마(알파노트와 theme 키 공유)

## 미포함 (무료 소스 없음)

- 청산량(liquidation) — Coinglass/CryptoQuant 유료 영역
- 거래소 보유량, 고래 플로우 등 온체인 상세 — CryptoQuant 유료 영역
