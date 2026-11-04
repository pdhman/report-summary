# 종목 리서치 → 알파노트 논리 탭 (자동 숫자 + 수동 논리)

리서치팀 발표자료(PPT)를 웹으로 옮겨 두고, 공시·실적이 바뀌면 **숫자는 자동으로 따라가고 글은 해당 STEP 만 손보는** 구조다.
결과 페이지: 알파노트 **분석 › 🧭 논리** (`docs/thesis.html`) → 아래 "심층 투자 논리 리포트" 표에서 종목 클릭.

비유: PPT 는 인쇄된 신문(숫자 하나 바뀌어도 다시 찍어야 함), 이 구조는 전광판 + 게시판(전광판 숫자는 알아서 돌고, 게시판 글만 필요할 때 바꿔 붙임).

## 파일 한 종목당 2개

| 파일 | 내용 | 공개 |
|---|---|---|
| `docs/thesis/<코드>.md` | 논리 본문(12 STEP 또는 7단계 탑다운) + frontmatter(기준일·기준 주가·컨센서스 등 `base_*`) | 공개 (GitHub Pages) |
| `docs/thesis/private/<코드>.md` | 목표가·진입가·손절·시나리오 가격·손익비 표 | **PC 전용** (`.gitignore` 로 git 제외 → 공개 사이트에서는 404 라 안 보임) |

`docs/thesis/index.json` 이 목록(종목·날짜·점수·분류·판단)이고, `thesis-lab/publish_thesis.py` 가 frontmatter 를 읽어 갱신한다.

뷰어는 종목을 열면 **📌 리서치 기준 대비 지금** 카드를 자동으로 만든다: 기준 주가 → 지금, 올해 EPS(E)·영업이익(E) 컨센서스 변화, PER·52주 고점 대비, 최근 분기, 기준일 이후 리포트·수주 공시 건수, 다음 실적 D-day, 자동 점수. PC 에서 열면 🔒 목표1·손절까지 % 와 손절선 근접 경고도 붙는다.
자동 숫자의 원천은 `docs/thesis_data.js`(금요일 17:00 자동, 평일은 `python -X utf8 thesis-lab/build_thesis.py` 로 수동 갱신).

## 1. 새 PPT 를 올릴 때

```bash
python -X utf8 thesis-lab/pptx_to_text.py <덤프폴더> "종목 리서치/<폴더>/<파일>.pptx"
```

1. 덤프 텍스트를 보고 `docs/thesis/<코드>.md`(공개) 와 `docs/thesis/private/<코드>.md`(가격) 를 쓴다. 형식은 `docs/thesis/031980.md`(피에스케이홀딩스) 를 견본으로.
   - 공개 본문에는 리서치팀 목표가·진입가·손절·시나리오 가격·손익비 표를 넣지 않는다(증권사 목표주가·이동평균선·과거 주가는 괜찮다).
   - frontmatter 의 `base_price`·`base_eps_e`·`base_op_e`·`base_rev_e` 가 "기준 대비 지금" 카드의 출발점이다.
2. 게시: `python -X utf8 thesis-lab/publish_thesis.py <코드> docs/thesis/<코드>.md`
3. 커밋·푸시 (private 폴더는 자동 제외됨). Claude Code 에게 "종목 리서치 PPT 올려줘" 라고 하면 1~3 을 대신한다.

## 2. 공시 알림이 왔을 때 (매일 08:20 텔레그램 DM)

`telegram/send_dart_alert.py` 가 `index.json` 에 게시된 종목 전부의 새 DART 공시를 모아 종류별로 손볼 STEP 을 붙여 보낸다(작업 스케줄러 `리서치공시_0820_알림`, 같은 공시는 `telegram/dart_alert_state.json` 으로 한 번만).

| 공시 종류 | 손볼 곳 (12 STEP 기준) |
|---|---|
| 분기·반기·사업보고서, 잠정실적 | STEP 7 실적 전달 · STEP 8 기대 수준 · STEP 11 철회 조건 |
| 단일판매·공급계약, 수주 | STEP 4 왜 지금 · STEP 6 증거 · STEP 9 미반영 · STEP 10 촉매 |
| 유상증자·CB·합병·자기주식 | STEP 9 · STEP 11 |
| 지분 변동 | STEP 1 · STEP 11 |
| IR·조회공시·공정공시 | STEP 8 · STEP 10 |

고치는 법: 해당 STEP 문단만 수정 → frontmatter 의 `date` 와 `base_*` 를 수정일의 숫자로 바꾼다(카드가 "수정일 기준"으로 다시 셈) → `publish_thesis.py` → 커밋. 이전 버전은 `docs/thesis/history/` 에 날짜별로 남는다. 가격 레벨이 바뀌면 `private/<코드>.md` 도 같이.

수동 확인: `python -X utf8 telegram/send_dart_alert.py --days 7 --dry-run`

## 3. PC 에서 가격 레벨까지 보기

공개 사이트(https://pdhman.github.io/report-summary/thesis.html)에는 본문만 뜬다. 가격 레벨까지 보려면 docs 를 로컬로 띄운다.

```bash
python -m http.server 8791 --directory docs
```

→ http://localhost:8791/thesis.html#c=<코드> (seasonality 서버 8766 도 docs 를 그대로 서빙하므로 http://127.0.0.1:8766/thesis.html 로도 된다). `file://` 로 직접 열면 리포트 fetch 가 막혀 본문이 안 뜬다.

**백업 주의**: `docs/thesis/private/` 는 git 에 없으므로 PC 백업에 꼭 포함한다.
