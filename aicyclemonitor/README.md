# AI Cycle Risk Monitor

"호황은 어떻게 끝나는가" — AI 슈퍼사이클의 3가지 종료 경로를 무료 데이터로 추적하는 모니터링 대시보드.

| 축 | 역사적 유형 | 추적 지표 | 소스 |
|---|---|---|---|
| ① 공급과잉 | 1999 닷컴 | GPU 임대가(H100·B200 임대중 중앙값), DC REIT(DLR·EQIX), 공실률 | 500.farm(vast.ai 미러) · Yahoo · CBRE(수동) |
| ② 수요둔화 | 2022 긴축 | Hyperscaler 매출 YoY, 클라우드 부문 YoY, NVDA DC 매출 YoY, 5사 연간 capex(컨센서스 vs 실적·런레이트), MU 매출 YoY(HBM 프록시) | Yahoo·SEC(자동) · 실적발표·Bloomberg(수동) |
| ③ 레버리지 | 2008 금융위기 | AA·BBB·HY·CCC OAS, 네오클라우드 주가, BDC 프록시, CRWV 분기 capex, ORCL RPO, 개별 CDS(수동) | FRED(→allorigins 폴백) · Yahoo · SEC |

> **금융화 위험신호의 지표화** (2026-08 추가): '저신용 차입 용이성'→CCC OAS(자동),
> '계약 취소·사용률 하락'→ORCL RPO 잔고 QoQ(자동), 'HBM 물량 둔화'→MU 매출 YoY(자동,
> 회계분기가 빨라 조기 신호). 'GPU 담보가치'는 GPU 임대가(공급 축)가, '사모 크레딧
> 스프레드'는 BDC 프록시가 이미 커버. 'GPU 대출 LTV'는 사적 계약 조건이라 지표화 불가.

> 개별 기업 CDS(오라클·NVIDIA·브로드컴 등)는 Markit/Bloomberg 유료 독점이라 무료 추적 불가.
> 대신 등급 버킷 OAS로 프록시: AA=하이퍼스케일러(MSFT AAA·GOOGL AA+·AMZN AA·META AA-)·NVIDIA급,
> BBB=오라클·브로드컴급. 해당 등급 스프레드가 벌어지면 그 그룹의 조달비용 상승 신호.
> 개별 CDS 수치가 언론·리서치에 인용되면 manual_data.json 의 bigtech_cds_5y_bp 에 스냅샷으로
> 기록(차트 + ORCL 200bp 이상 경계). 개별 CDS는 등급 버킷보다 개별 리스크를 먼저 반영한다 —
> 2026-07 기준 ORCL CDS 215bp vs BBB OAS 97bp 처럼 괴리가 커지면 그 자체가 신호.
>
> **오라클 신용등급: 2026-08 S&P가 BBB-로 강등** (FY26 FCF -$237억, 총부채 ~$1,300억) —
> 정크 한 단계 위. 추가 강등 시 '추락천사(fallen angel)'로 IG 지수에서 퇴출되며 기계적
> 매도가 발생하고 HY 지수가 이를 흡수 — BBB OAS·HY OAS 급변의 잠재 트리거로 주시.

> FRED는 로컬·GitHub 러너 모두 직접 접근이 차단이라 allorigins 프록시 + 로컬 캐시 폴백을 내장.
> GPU 임대가는 vast.ai 직접 API가 어디서든 403이라 통계 미러(500.farm)를 기본 소스로 사용.
>
> **왜 '임대중(rented)' 중앙값인가**: 가용(available) 호가는 안 팔리고 남은 매물이라 위로
> 편향되고(실측 +40%) 표본도 적다. 임대중 중앙값이 체결가에 가깝고 표본이 4배.
> **왜 B200 병행 추적인가**: H100 가격 하락은 세대교체 감가와 섞여 해석이 애매하지만,
> 최신 세대(B200) 임대가 하락은 순수한 공급과잉 신호다.
>
> **클라우드 부문 YoY의 정의**: AWS는 순수 클라우드 부문(달러 공시), Google Cloud는
> GCP+Workspace를 묶은 부문(달러 공시), Azure는 MS가 달러를 공시하지 않아
> 'Azure 및 기타 클라우드 서비스' 성장률(%)만 존재 — 모두 각사 공식 공시 기준.
> OCI는 오라클 Cloud Infrastructure(IaaS) 부문(달러 공시) — 회계분기가 8·11·2·5월
> 마감이라 캘린더 분기에 1개월 시차로 매핑(8월분기→Q3 등). 오라클 전사 매출은
> 레거시 SW가 희석해 수요 축 4사 평균에 넣지 않고, OCI 부문만 클라우드 카드에서 추적.
> Meta는 클라우드 사업이 없어 전사 매출로만 추적.
>
> **NVDA DC 매출은 공급자 측 프록시로 별도 추적**: 5대 하이퍼스케일러 capex 합산이 놓치는
> 비미국·비상장(코어위브, 중국 클라우드, Stargate 등) 투자까지 결국 NVIDIA 매출로 흐르므로,
> 수요자 합산의 과소집계를 총량에서 교차검증하는 지표. 세그먼트 수치라 XBRL에 없어
> 분기 실적 발표 후 manual_data.json 의 nvda_dc_revenue_bil 에 수동 입력(라벨=회계분기 말월).
>
> 전사 매출 분기 히스토리는 SEC EDGAR XBRL API(data.sec.gov, 무료·무키)로 2023Q1부터
> 백필함. yfinance가 최근 5~6개 분기만 주므로 과거 분기는 EDGAR가 원본.
>
> **5사 연간 capex 카드의 자동/수동 구분**: 분기 capex 실적은 SEC XBRL에서 자동 수집
> (YTD 차분, 역년 합산 + 최근 2개 분기 ×2 런레이트). Bloomberg 컨센서스(E)만 무료
> API가 없어 수동. 경계 판정은 컨센서스가 아니라 **런레이트 vs 직전 4분기 합**(같은
> SEC 기준끼리)으로 자동 계산 — 0% 이하(가속 멈춤)면 경계라, 컨센서스를 안 고쳐도
> 실행 둔화는 체크리스트에서 자동 감지된다. SEC 현금 capex와 Bloomberg 합계는
> 집계 정의가 소폭(~8%) 달라 직접 비교 경계선은 두지 않음.
>
> 2026-01~07 월말 히스토리는 500.farm Grafana(Prometheus) API에서 소급 수집함
> (`vastai_ondemand_price_median_dollars{verified="yes",rented="yes|no"}` —
> `/vastai/grafana.v2/api/datasources/uid/EdgV2xcnz/resources/api/v1/query`, 익명 접근.
> 과거 구간을 다시 채울 일이 있으면 이 API로 임의 시점 조회 가능).

## 사용법

```bash
pip install yfinance pandas requests

python ai_cycle_monitor.py            # 실데이터 수집 → dashboard.html 생성
python ai_cycle_monitor.py --sample   # 네트워크 없이 예시 데이터로 레이아웃 확인
```

- 최초 실행 시 `manual_data.json` 생성 → 공실률(반기)·클라우드 부문 YoY(분기)를 직접 입력
- GPU 임대가·분기 매출은 실행할 때마다 로컬 CSV에 누적 (히스토리 축적)
- 경계 임계값은 `ai_cycle_monitor.py` 상단 `TH` 딕셔너리에서 조정

## 주 1회 자동 실행 (GitHub Actions — 기본)

`.github/workflows/weekly-aicycle.yml` 이 **매주 월요일 08:41 KST** 에 미국 러너에서 실행:
수집 → `reports/aicycle.html` 로 복사 → 히스토리 CSV 커밋 → GitHub Pages 배포.

- 대시보드 주소: **https://pdhman.github.io/report-summary/aicycle.html**
- FRED·vast.ai는 러너(데이터센터 IP)에서도 차단됨 — 내장 폴백
  (allorigins 프록시 + FRED 로컬 캐시, 500.farm 미러)이 실제 수집 경로
- `aicyclemonitor/` 변경을 push 하면 즉시 재실행·재배포 (수동 실행 버튼도 있음)
- 히스토리 CSV·`manual_data.json` 은 누적·공유를 위해 repo에 포함 (git 제외 아님)
- 실적 시즌마다 `manual_data.json` 의 클라우드 YoY·공실률을 갱신하고 push 하면 반영

### (대안) Windows 로컬 실행

`register_weekly_task.bat` 실행 → 매주 월요일 08:30 작업 스케줄러 등록.
(`run_monitor.bat`가 실제 실행 + `monitor_log.txt` 로그 기록)
Actions 자동화가 기본이므로 로컬 스케줄러는 등록하지 않아도 된다.

## 파일 구성

- `ai_cycle_monitor.py` — 수집·계산·렌더링 (단일 파일, 템플릿 내장)
- `dashboard_template.html` — 대시보드 템플릿 (같은 폴더에 있으면 내장본보다 우선 적용)
- `run_monitor.bat` / `register_weekly_task.bat` — Windows 자동화 (대안 경로)
- 데이터: `manual_data.json`, `gpu_rental_history.csv`, `revenue_history.csv`, `fred_*_history.csv` (repo 포함)
- 생성물: `dashboard.html` (git 제외, 배포본은 `reports/aicycle.html`)

---

# 한국 시장 사이클 모델 (korea_cycle_monitor.py)

켄 피셔식 접근 — "지수 레벨·밸류에이션을 예측하지 말고 강세장의 단계를 확인하라."
**Global → 반도체 → Breadth → Euphoria** 프레임으로 5개 팩터를 0~100점 채점, 가중 합산해
Market Regime Score 를 만든다. 기존에 쌓아온 데이터 자산을 전부 재사용한다.

| 팩터 (가중치) | 하위지표 | 소스 |
|---|---|---|
| ① Global Trend (25) | S&P500 200일선 이격도, VIX, 달러 60일 모멘텀 | Yahoo (3년, kc_cache 병합) |
| ② 반도체·이익 리비전 (25) | SOX 이격도, 삼전·하이닉스 목표주가 60일 기울기, 메모리 사이클 국면, 전종목 영업이익(E) 상향비율, 삼전·하이닉스 주가 추세 | 리포트서머리.xlsx · memory-cycle · 퀀트데이터 주간 스냅샷 · Yahoo |
| ③ Market Breadth (20) | MA200/MA50 위 비율, ADR20, 52주 신고가 비율, 맥클렐런 | reports/data/market_history.csv (breadth_build) |
| ④ 신용·유동성 (15) | 예탁금 20일 증가율, 반대매매 비중, 신용/예탁금, 美 HY OAS | market_leverage_collector · FRED(allorigins 폴백) |
| ⑤ Euphoria (15, 역방향) | 저가주 거래대금 비중, 상한가 수, 회전율, 신용융자 20일 증가율 | market_history · 레버리지 |

- 채점: 하위지표를 자기 히스토리 백분위(0~100)로 변환(방향 통일) 후 평균, 팩터는 5일 평활.
  목표주가 기울기·사이클 국면은 규칙 기반 매핑(히스토리 짧아 백분위 부적합).
- 국면: ≥80 Strong Bull(주식 90~100%) / 65~ Bull(75~90%) / 50~ Neutral(50~75%)
  / 35~ Risk-off(30~50%) / <35 Bear(현금·헤지).
- 실행: `python korea_cycle_monitor.py` (또는 `run_korea_cycle.bat`), `--offline` 은 캐시만 사용.
- 국면 알림: `run_korea_cycle.bat` 이 모델 실행 후 `telegram/send_cycle_alert.py` 를 호출 —
  직전 기록 대비 종합 스코어 2점 이상 하락, 국면 밴드 하향(⚠️) 또는 상향(🟢) 전환 시
  텔레그램 DM(개인 대화)으로 발송(채널 미발송, 날짜당 1회).
- 출력: `korea_cycle.html` + 배포 사본 `reports/korea_cycle.html`(그쪽 상호링크는 aicycle.html).
  AI 사이클 대시보드 헤더와 상호 링크로 연결.
- 누적: `kc_cache/global.csv`(글로벌 시세), `kc_cache/revision_breadth.csv`(리비전 폭),
  `kc_cache/score_log.csv`(일별 점수 기록 — 재계산으로 과거 백분위가 흔들려도 당시 기록 보존).
- 주의: 종목선정(TAIGAN·팩터랭킹)과 분리된 **시장 국면 판단 전용 레이어**. market_history 가
  평일 16:35 갱신된 뒤(17시 이후) 실행해야 당일 데이터가 반영된다.
