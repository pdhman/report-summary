# AI Cycle Risk Monitor

"호황은 어떻게 끝나는가" — AI 슈퍼사이클의 3가지 종료 경로를 무료 데이터로 추적하는 모니터링 대시보드.

| 축 | 역사적 유형 | 추적 지표 | 소스 |
|---|---|---|---|
| ① 공급과잉 | 1999 닷컴 | H100 GPU 임대가, DC REIT(DLR·EQIX), 공실률 | vast.ai(→500.farm 폴백) · Yahoo · CBRE(수동) |
| ② 수요둔화 | 2022 긴축 | Hyperscaler 매출 YoY, 클라우드 부문 YoY | Yahoo · 실적발표(수동) |
| ③ 레버리지 | 2008 금융위기 | BBB·HY OAS, 네오클라우드 주가, BDC 프록시 | FRED(→allorigins 폴백) · Yahoo |

> 해외 IP에서 FRED가 차단되거나 vast.ai API가 403을 반환하는 환경을 위해
> 미국 경유 프록시(allorigins.win)와 커뮤니티 미러(500.farm) 폴백이 내장되어 있습니다.

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
