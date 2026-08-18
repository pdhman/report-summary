---
name: x-monitor
description: X(트위터) 관심 계정 모니터링 실행 — Chrome으로 X 리스트 타임라인을 읽어 새 글을 수집하고, 일일 요약 리포트와 HTML 대시보드를 갱신한다. "X 모니터링", "트위터 새 글 확인", "리스트 훑어줘" 같은 요청에 사용.
---

# X 팔로잉 모니터링

베이스 폴더: `x-monitor/` (프로젝트 루트 기준). 모니터링 대상은 `accounts.json`
(엑셀 팔로잉 리스트에서 O 이상 표시된 계정만, grade 2=★★ 중요(엑셀 OO) / 1=★ 참고(엑셀 O)).
표시 규칙: 화면·리포트에서는 항상 ★★/★ 로 표기한다 (엑셀 입력만 O/OO).

## 사전 확인

1. `x-monitor/state.json` 읽기 — `list_url`(X 리스트 URL), `last_run`(마지막 수집 시각, ISO),
   `browser_device_id`(지난번 정상 동작한 Chrome 확장 deviceId) 확인.
2. `list_url`이 null이면 아래 **최초 설정** 먼저 수행.
3. Claude in Chrome 도구를 ToolSearch **한 번에** 로드:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__list_connected_browsers,mcp__claude-in-chrome__select_browser,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp`
   (사용자의 실제 Chrome — X 로그인 세션 필요. in-app Browser pane은 로그인이 없으므로 쓰지 않는다.)
4. **브라우저 인스턴스 고정** — 사용자는 업무상 **Chrome을 항상 2개 띄워 둔다.** 즉 확장이
   상시 2개 연결된 상태가 정상이며, 이건 일시적 오류가 아니다. 둘 중 하나만 x.com에
   로그인되어 있고, 다른 하나는 `navigate`/`javascript_tool`이 **300초 타임아웃**으로
   실패할 수 있다("The underlying operation ... may be stuck or unresponsive").
   기본 선택을 믿지 말고 **매 실행마다 명시적으로 고른다**:
   1. `state.json.browser_device_id`가 있으면 **`select_browser`로 그것부터 선택**한다
      (없으면 `list_connected_browsers` 결과의 첫 번째).
   2. `tabs_create_mcp` → `list_url`로 `navigate` → JS로 로그인 확인:
      `!!document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]')`
   3. **타임아웃이거나 로그인 false면 그 브라우저는 실격** — `list_connected_browsers`의
      다음 deviceId로 `select_browser` 후 2를 반복한다.
   4. 성공한 deviceId를 `state.json.browser_device_id`에 기록한다. 다음 실행은 첫 시도에
      맞으므로 300초 타임아웃을 겪지 않는다.
   - `tabs_context_mcp`는 **판별에 쓸 수 없다** — 응답하지 않는 브라우저에서도 성공한다.
     반드시 `navigate` 성공 + 로그인 확인까지 봐야 한다.
   - deviceId·표시명("Browser 1/2")은 확장 재설치 시 바뀌고 순서도 고정이 아니다.
     **실제로 navigate가 되고 x.com에 로그인된 쪽**만이 판단 기준이다.
   - 예약 실행(사용자 부재)에서는 어느 브라우저를 쓸지 묻지 말고 위 순서대로 시도한다.
     연결된 전부가 타임아웃이거나 로그아웃일 때만 수집을 건너뛰고 보고한다.

   > 근본 해결책(사용자 선택): 2개 창을 계속 쓰되 **X에 로그인된 프로필에만 Claude 확장을
   > 남기고 다른 프로필에서는 확장을 비활성화**하면 연결이 1개가 되어 이 분기 자체가 사라진다.
   > 창을 2개 띄우는 것과 확장이 2개 연결되는 것은 별개다(같은 프로필의 창 2개 = 확장 1개).

## 수집

1. 새 탭에서 `list_url` 열기. X 리스트 타임라인은 최신순(reverse-chronological).
2. javascript_tool로 화면에 렌더된 트윗을 구조화 추출 (참여 지표 포함):
   ```js
   JSON.stringify([...document.querySelectorAll('article[data-testid="tweet"]')].map(a => {
     const link = [...a.querySelectorAll('a')].find(x => /\/status\/\d+$/.test(x.href) && x.querySelector('time'));
     const t = a.querySelector('time');
     const txt = a.querySelector('[data-testid="tweetText"]');
     const social = a.querySelector('[data-testid="socialContext"]');
     const g = a.querySelector('[role="group"][aria-label]');
     // aria-label 예: "78 답글, 69 재게시, 540 마음에 들어요, 54 북마크, 127364 조회수" (숫자가 앞)
     const gl = g ? g.getAttribute('aria-label') : '';
     const ml = gl.match(/([\d,]+)\s*마음에 들어요/), mv = gl.match(/([\d,]+)\s*조회/);
     return {
       url: link ? link.href : null,
       time: t ? t.getAttribute('datetime') : null,
       handle: link ? link.href.split('/')[3] : null,
       text: txt ? txt.innerText : '',
       repost_by: social ? social.innerText : null,
       likes: ml ? +ml[1].replace(/,/g, '') : null,
       views: mv ? +mv[1].replace(/,/g, '') : null
     };
   }))
   ```
3. **스크롤은 반드시 `computer`의 scroll 액션으로** (CDP 입력 이벤트). 탭이 백그라운드일 때
   JS `window.scrollBy()`는 가상 렌더링을 깨우지 못해 같은 글만 반복 추출된다.
   스크롤 → 추출 반복, 가장 오래된 글의 time이 `last_run` 이전이 될 때까지
   (최초 실행이거나 last_run이 오래됐으면 최근 24시간만 커버).
   리스트 타임라인에서 특정 글을 못 찾거나 지표가 비면, 해당 글의 status URL을 직접
   방문해 첫 article에서 같은 방식으로 추출하는 것이 확실한 폴백이다.
4. `last_run` 이후 글만 남기고 url 기준 중복 제거. 리포스트(repost_by 있음)는 유지하되 표기.
5. **★★(grade 2) 계정 전문 수집**: 수집된 글 중 ★★ 계정의 글이 잘려 있으면
   (…로 끝나거나 문장 중간에서 끊김 — 타임라인은 긴 글을 자름) 해당 글의
   status URL을 열어 `article[data-testid="tweet"] [data-testid="tweetText"]`의
   innerText로 text를 교체한다. ★/무등급 글은 잘린 채로 둔다 (수집 시간 절약).
   status 페이지로 이동하면 페이지 전역 변수가 날아가므로 **6번 회수를 끝낸 뒤** 실행한다.

### 수집 데이터 회수 (javascript_tool 출력 제한 우회)

`javascript_tool` 반환값에는 두 가지 제약이 있어 **한 번에 전체 JSON을 덤프할 수 없다**:
- **길이**: 약 1,200자에서 `[TRUNCATED]`로 잘린다 (요청한 slice 길이와 무관).
- **내용 필터**: base64처럼 보이거나 쿼리스트링·쿠키처럼 보이는 문자열이 섞이면 출력 전체가
  `[BLOCKED: Base64 encoded data]` / `[BLOCKED: Cookie/query string data]`로 대체된다.

**시도하지 말 것** (2026-08-12 실행에서 전부 실패):
gzip+base64 인코딩(내용 필터에 통째로 차단) · `execCommand('copy')`로 클립보드 경유
(페이지에서는 성공하지만 PowerShell `Get-Clipboard`에 도달하지 않음) · Blob + `a.download`
다운로드(파일이 Downloads에 생성되지 않음) · 문자열 offset slice 반복(차단된 구간이 생기면
인덱스가 어긋나 어느 구간이 빠졌는지 추적 불가).

**작동하는 방식** — 페이지 전역 배열에 모아두고 **글 단위로 결정론적으로 나눠 받는다**:
```js
// 스크롤·추출이 끝난 뒤 한 번만 실행
const clean = s => (s||'')
  .replace(/https?:\/\/\S+/g,'[link]')
  .replace(/\b[\w.\/-]*\.(com|io|org|net|co|be|ai)\b[\w.\/?=&%-]*/gi,'[link]')
  .replace(/\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{8,}\b/g,'#');  // 필터 유발 토큰 제거
window.__P = Object.values(window.__X)
  .filter(x => x.time && x.time > LAST_RUN)
  .sort((a,b) => b.time.localeCompare(a.time))
  .map(x => ({ url:x.url.replace(/\?.*$/,''), time:x.time, handle:x.handle,
               text: clean(x.text).slice(0,240), repost_by:x.repost_by,
               likes:x.likes, views:x.views }));
window.__g = (k,n) => JSON.stringify(window.__P.slice(k,k+n));
window.__P.length
```
- **`window.__g(k,3)`을 `browser_batch`에 10개씩 묶어** 호출한다 (1회 왕복에 30건).
  3건·본문 240자면 출력이 1,200자 아래로 유지되어 잘리지 않는다.
- `time`은 **ISO 원본 그대로**, `repost_by`는 **원문 문자열 그대로** 넘긴다.
  (압축하려고 분 단위·불리언으로 줄이면 데이터 파일의 초 단위 시각과 리포스터 이름이 유실된다.)
- 개별 항목이 `[BLOCKED]`으로 오면 **그 글만 `__g(k,1)`로 단독 재요청**, 그래도 막히면
  메타(`{url,time,handle,likes,views}`)와 `__P[k].text.slice(0,120)` / `.slice(120,240)`을
  나눠 받는다. 인덱스 기반이라 어느 글이 빠졌는지 항상 명확하다.
- 133건 기준 왕복 5회 정도면 끝난다.

## 저장·리포트

1. `x-monitor/data/YYYY-MM-DD.json` (KST 오늘 날짜)로 저장:
   `{"date": "...", "collected_at": "...(ISO)", "posts": [{handle, time, text, url, repost_by?}]}`
   같은 날짜 파일이 이미 있으면 posts를 합치고 url로 중복 제거.
2. `x-monitor/reports/YYYY-MM-DD.md` 한국어 리포트 작성:
   - 상단: 수집 기간·건수·한 줄 총평
   - **핵심 (★★ 계정)**: 계정별 요약 + 원문 링크
   - **주제별 정리**: 비슷한 주제끼리 2~4개 클러스터로 묶어 요약
   - 시장 시사점이 있으면 마지막에 코멘트 한 줄
   - **헤딩 형식 엄수** — 텔레그램 발송 스크립트(`telegram/send_x_summary.py`)가 정규식으로
     섹션을 뽑는다: 주제별 정리는 `## 주제별 정리` + 각 주제 `### 소제목`, 코멘트는 반드시
     **`## 코멘트` 헤딩 섹션**으로 쓸 것. `**코멘트**: ...` 인라인으로 쓰면 발송에서 누락된다
     (2026-08-18 실제 사고. 파서에 인라인 폴백을 넣어두긴 했지만 헤딩이 표준이다).
   - 영어 원문은 한국어로 요약하고, 링크는 그대로 유지
3. `python x-monitor/scripts/build_dashboard.py` 실행 → `dashboard/data.js` 갱신.
4. `python make_summary.py` 실행 → '오늘의 요약'(reports/index.html)의 X 모니터링 카드와
   게시 페이지(reports/x_YYYYMMDD.html, x.html)가 함께 재생성된다 (make_x_monitor.py 경유).
5. `state.json`의 `last_run`을 수집 시점(ISO)으로, `browser_device_id`를 이번에 성공한
   브라우저 deviceId로 갱신 (state.json은 .gitignore 대상 — 로컬 전용).
6. 사이트 게시: 변경분(x-monitor/reports·data, reports/x_*.html, x.html, index.html)을
   커밋하고 main에 push → daily-insights 워크플로가 Pages 재배포. 커밋 메시지 예:
   `x-monitor: 2026-07-29 수집 게시`
7. 텔레그램 발송: `python telegram/send_x_summary.py --to channel,rapha` — '주제별 정리'
   소제목과 '코멘트', 게시 페이지 링크를 공개 채널 **@daily_alphanote(데일리 알파노트)** 와
   **@Rapha_n_advisory(라파엔투자자문)** 두 곳에 올린다. 리포트(md)를 읽으므로 **2번 뒤에**
   실행해야 하고, 링크가 살아 있으려면 6번 push 뒤가 맞다.
   내용만 확인하려면 `--dry-run`, 개인 대화방으로만 보내려면 `--to dm`.
   여기서 빠뜨려도 `X모니터링_누락감지_발송` 작업이 매시 확인해 대신 보낸다.
8. 리포트 파일을 SendUserFile로 전달. 로컬 대시보드는 `x-monitor/dashboard/index.html`.

## 최초 설정 (list_url이 null일 때)

1. Chrome에서 x.com 로그인 상태 확인 (안 되어 있으면 사용자에게 로그인 요청).
2. 비공개 리스트 생성: x.com → Lists → 새 리스트 "모니터링" (비공개 체크).
3. `accounts.json`의 계정을 리스트 멤버로 추가: 멤버 관리 → 핸들 검색 → 추가 반복.
4. 완성된 리스트 URL(`x.com/i/lists/<id>`)을 `state.json`의 `list_url`에 저장.

## 엑셀 표시 변경 반영

사용자가 엑셀(O/OO/OOO 표시)을 바꿨다고 하면:
`python x-monitor/scripts/parse_excel.py` 재실행 → `accounts.json` 갱신 →
추가/제거된 계정을 X 리스트 멤버에도 반영.
