# 메모리 사이클 '현재 결론' 갱신 절차

알파노트 분석 탭 **💾 메모리**(https://pdhman.github.io/report-summary/memory.html)의
판정 표·항목별 해설·수급 지표·최근 신호를 고치는 절차다.
PC가 꺼져 있어도 클라우드 세션(claude.ai/code, Claude 앱)에서 할 수 있다.

사용자가 **"Memory Cycle Watch"** 글을 붙여넣고 "업데이트해줘"라고 하면 이 절차를 따른다.

## 무엇을 고치나

`memory-cycle/data/cycle_view_manual.json` 하나만 고친다. 페이지는 스크립트가 다시 그린다.

| 키 | 화면 | 비고 |
|---|---|---|
| `as_of` | 기준일 | `YYYY-MM-DD`. 갱신한 날짜로 바꾼다 |
| `rows[]` | 현재 결론 표 + 항목별 해설 카드 | `area`·`dots`·`label`·`direction`·`what`·`note[]` |
| `hbm_gap` | 수급 지표 탭 — HBM 수요·공급·Gap·공급/수요 비율 | |
| `peak_warning.items[]` | HBM 정점 경고 5개 조건 | `met: true/false`, `now`. 충족 개수는 자동 집계 |
| `lead_times.items[].history[]` | 리드타임 표 | `{date, weeks, source}` 한 줄 추가. 방향은 자동 판정 |
| `updates[]` | 최근 반영한 신호 | **맨 위에** 추가. 화면에는 최근 6건 |
| `source` | 출처 줄 | 새 종류의 출처를 쓰면 이름 추가 |

`dots`: `g` 녹색(강세) · `y` 노랑(중립) · `o` 주황(약세 조짐) · `r` 빨강(경고).
1~2개(`gg` = 강도 높음), 범위는 슬래시(`y/o`). `updates[].level` 도 같은 글자 하나.

## 절차

1. **원문 확인이 먼저다.** 붙여넣은 글의 핵심 수치·주장을 1차 출처(TrendForce 보도자료, 회사 발표,
   Reuters 등)에서 WebFetch/WebSearch 로 확인한다.
   - 확인이 안 되거나 출처가 약하면(유통사 블로그, 날짜 불명, 1년 지난 자료) **반영하지 말고**
     무엇이 확인되고 무엇이 안 됐는지 사용자에게 알린다. 사용자가 명시적으로 요구한 원칙이다.
   - 전망과 실현 데이터를 구분해 쓴다("TrendForce 전망", "계약 가격 확정" 등).
   - 2차 인용(예: 증권사 리포트를 인용한 기사)은 원 리포트 날짜와 함께 적는다.
2. JSON 을 고친다(아래 **쓰는 법**).
3. 검사 후 반영:
   ```bash
   python3 memory-cycle/update_view.py --check
   python3 memory-cycle/update_view.py
   ```
   검사에 실패하면 페이지는 그대로 남는다. 오류 메시지대로 JSON 을 고친다.
4. 커밋해서 **main** 에 올린다.
   ```bash
   git add memory-cycle/data/cycle_view_manual.json docs/memory.html
   git commit -m "메모리 사이클: YYYY-MM-DD 갱신 — (한 줄 요약)"
   git push origin HEAD:main
   ```
   클라우드 세션은 기본적으로 `claude/…` 브랜치에 올린다. main 에 직접 푸시가 안 되면
   PR 을 만들고 사용자에게 병합을 요청한다(휴대폰 GitHub 앱에서 병합 가능).
   JSON 이 main 에 들어가기만 하면 GitHub Actions(`memory-view.yml`)가 페이지를 자동으로 다시 그린다.
5. 1~2분 뒤 라이브 페이지에 새 기준일이 보이는지 확인한다.
   ```bash
   curl -s "https://pdhman.github.io/report-summary/memory.html?v=$RANDOM" | grep -o "판정 기준일 <!--cv:asof-->[0-9-]*"
   ```
   클라우드 기본 네트워크(Trusted)에서는 `pdhman.github.io` 가 막혀 이 명령이 실패할 수 있다.
   그 경우 WebFetch 로 확인하거나, 확인을 건너뛰고 사용자에게 페이지 링크로 확인해 달라고 한다.
   (영구 해결: 클라우드 환경 설정 → 네트워크 Custom → `pdhman.github.io` 추가)

## 쓰는 법 (사용자 요청 사항)

- **쉬운 말로.** 전문용어는 풀어서 쓴다. 예: "bit 기준 13%" → "웨이퍼를 30% 써도 만들어지는 용량은
  전체의 13% — HBM은 같은 용량에 웨이퍼가 약 3배 들기 때문". 수치는 남기되 '그래서 무슨 뜻인지'를 붙인다.
- `note` 는 문장 리스트로, 카드당 2~5줄.
- **화면에 보이는 문구(`note`·`label`·`direction`·`updates` 등)에 내부 작업 안내나 파일 경로를 쓰지 않는다.**
  공개 페이지에 그대로 나간다. 작업 메모는 `_comment` 에. (검사 스크립트가 `data/`·`.json` 등을 막는다)
- 판정(`dots`·`label`)을 바꿀 때는 이유를 `updates[].impact` 에 적는다. 붙여넣은 글이 판정 변경을 말하지 않으면 바꾸지 않는다.
- 일반 D램·중국 공급 신호는 **HBM 정점 경고 조건에 넣지 않는다**(HBM 공급과 별개).
- 표에 없는 영역(예: "QLC NAND")은 새 행을 만들기 전에 기존 행(기업용 SSD 등)으로 충분한지 먼저 본다.

## 이 방식으로 안 되는 것

- **차트·나침반·백테스트 갱신**: 입력 데이터(주가·재무·컨센서스)가 PC 에만 있다.
  PC 에서 `python memory-cycle/cycle_model.py` 로 전체 빌드해야 한다(결론 부분도 같이 새로 그려진다).
- 결론 구역의 **디자인(CSS)** 변경: 페이지 스타일은 전체 빌드가 만든다. 문구·판정만 이 방식으로 바꾼다.
- `docs/memory.html` 의 구역 표시(`<!--cv:now-->` 등)가 사라졌다면 PC 에서 전체 빌드를 한 번 돌려야 한다.
