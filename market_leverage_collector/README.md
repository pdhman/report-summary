# 금융시장 레버리지 데이터 수집기

금융투자협회 FreeSIS 화면에서 신용잔고와 증시자금 데이터를 수집해 CSV와
SQLite로 저장합니다.

## 실행

파일 탐색기에서 `run.cmd`를 더블클릭하거나 명령 프롬프트에서 실행합니다.

```bat
cd C:\Users\SAMSUNG\Desktop\클로드코드\market_leverage_collector
run.cmd
```

결과는 `data` 폴더에 저장됩니다.

- `credit_balance.csv`
- `market_funds.csv`
- `market_leverage.db`

## 테스트

```bat
cd C:\Users\SAMSUNG\Desktop\클로드코드\market_leverage_collector
test.cmd
```

이 프로젝트는 프로젝트 표준 Python 3.11 을 사용합니다.

필요한 패키지(pandas, lxml, beautifulsoup4, html5lib, playwright)는 해당 Python 에 설치되어 있습니다.
