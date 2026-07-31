# price-alert — 보유종목 급등락 카카오톡 알림

prop-dashboard `data.js`의 보유종목을 평일 09:00~15:30, 10분 간격으로 감시해
전일 종가 대비 등락률이 단계 임계값(기본 ±3% → ±5% → ±7%)을 돌파하면
카카오톡 **나와의 채팅**으로 알림을 보낸다. 같은 단계·방향은 하루 1회만 알린다.

- 시세: 네이버 증권 폴링 API(배치) + 종목별 basic API 폴백. 표준 라이브러리만 사용(pip 불필요).
- 실행: Windows 작업 스케줄러(로컬 전용). **보유종목 데이터·토큰은 절대 커밋 금지** —
  `kakao_tokens.json`/`alert_state.json`은 .gitignore로 차단돼 있다.

## 1회 설정 — 카카오 앱 만들기 (직접, 브라우저에서)

1. <https://developers.kakao.com> 로그인 → **내 애플리케이션 → 애플리케이션 추가하기** (이름 예: `price-alert`)
2. **[앱] > [플랫폼 키]**에서 **REST API 키** 복사 (JavaScript 키 아님!)
3. **제품 설정 > 카카오 로그인 > 일반** → 사용 설정 ON
4. **[앱] > [플랫폼 키] > [REST API 키] > [리다이렉트 URI]**에 아래를 정확히 등록:
   ```
   http://localhost:8899/callback
   ```
   (구버전 콘솔은 제품 설정 > 카카오 로그인 페이지에 Redirect URI 항목이 있음)
5. **제품 설정 > 카카오 로그인 > 동의항목** → **카카오톡 메시지 전송(talk_message)** → 선택 동의 설정
6. 인증 실행:
   ```bash
   python price-alert/kakao_auth.py
   ```
   REST API 키 붙여넣기 → 브라우저에서 로그인·동의 → "나와의 채팅"에 테스트 메시지가 오면 성공.

> "나에게 보내기"는 앱 소유자 본인 계정에는 검수 없이 바로 동작한다.

## 스케줄 등록

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File price-alert/register_task.ps1
```

태스크명 `PriceAlert_10min`. PC가 켜져 있고 로그인된 상태여야 실행된다.
로그는 `logs\price_alert_YYYYMMDD.log` (일별, 30일 보관).

## 수동 실행 / 테스트

```bash
python price-alert/alert_monitor.py --test      # 테스트 메시지 발송
python price-alert/alert_monitor.py --dry-run   # 발송 없이 판정 결과만 출력
python price-alert/alert_monitor.py --force     # 장시간 가드 무시하고 실제 실행
```

## 설정 변경 (`config.json`)

- `thresholds`: 알림 단계(%). 예: `[2, 4, 6, 10]`
- `market_start`/`market_end`: 감시 시간대 (KST)

## 문제 해결

- **알림이 안 옴 + 로그에 `NEEDS_REAUTH`**: refresh token 만료(약 2개월 미사용 시).
  `python price-alert/kakao_auth.py` 재실행으로 복구.
- **`invalid_client`**: REST API 키 확인. **redirect 오류**: Redirect URI 등록값 확인.
- **네이버 API 오류 반복**: 비공식 엔드포인트라 형식이 바뀔 수 있음. `quote.py` 자가 테스트로 확인:
  `python price-alert/quote.py 069500`
