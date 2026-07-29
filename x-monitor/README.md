# X 팔로잉 모니터링

엑셀 팔로잉 리스트(O 이상 표시 계정)의 X(트위터) 글을 모니터링하는 시스템.

## 사용법

Claude Code에서 **"X 모니터링 돌려줘"** 또는 `/x-monitor` 라고 하면:
1. Chrome으로 X 리스트 타임라인을 읽어 마지막 수집 이후 새 글을 가져오고
2. `reports/YYYY-MM-DD.md`에 한국어 요약 리포트를 만들고
3. `dashboard/index.html` 대시보드를 갱신한다.

## 구조

```
x-monitor/
├── accounts.json        # 모니터링 대상 (엑셀에서 생성, grade 2=★★(엑셀 OO) / 1=★(엑셀 O))
├── state.json           # X 리스트 URL, 마지막 수집 시각
├── data/                # 날짜별 수집 원문 (JSON)
├── reports/             # 날짜별 요약 리포트 (Markdown)
├── dashboard/
│   ├── index.html       # 대시보드 (브라우저로 열기)
│   └── data.js          # data/ 병합 결과 (build_dashboard.py가 생성)
└── scripts/
    ├── parse_excel.py       # 엑셀 → accounts.json (표시 바꾼 뒤 재실행)
    └── build_dashboard.py   # data/*.json → dashboard/data.js
```

## 엑셀 표시를 바꿨을 때

```bash
python x-monitor/scripts/parse_excel.py
```

O/OO/OOO 표시 기준으로 accounts.json이 다시 만들어진다.
X 리스트 멤버도 바뀐 계정에 맞춰 수동(또는 Claude에게 요청)으로 반영 필요.
