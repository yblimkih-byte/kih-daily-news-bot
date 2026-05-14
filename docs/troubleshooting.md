# 문제 해결 가이드 (Troubleshooting)

봇 운영 중 발생할 수 있는 문제와 해결 방법을 다룹니다.

## 빠른 진단 체크리스트

문제 발생 시 다음 순서로 확인:

1. **GitHub Actions 로그 확인** (가장 중요)
   - 저장소 > Actions 탭 > 최근 실패한 실행 클릭 > 로그 펼치기
   - 빨간 X 표시된 step의 로그 확인
   
2. **수신 채널 확인**
   - 카카오톡: '나와의 채팅방'
   - 이메일: **스팸함** 포함
   - 텔레그램: 봇과의 채팅창

3. **cron-job.org Last execution 확인**
   - https://cron-job.org Dashboard > 각 cron job 클릭 > History
   - HTTP 204 = 성공 (GitHub Actions가 받음)
   - HTTP 401 = 인증 실패 (PAT 문제)

---

## A. 자주 발생하는 증상별 해결

### A-1. 메시지가 안 옴 (모든 채널)

**원인 가능성**:

1. **수집 윈도우에 신규 기사 0건** (정상)
2. **회차 간 중복 차단**: 모든 기사가 최근 3일 이력에 있음 (정상)
3. **AI 필터 후 0건**: 모든 기사가 단순 홍보로 분류 (정상)
4. **모든 채널이 비활성화**: `ENABLE_KAKAO/EMAIL/TELEGRAM` 모두 false
5. **API 키 만료/한도 초과**

**진단**:

GitHub Actions 로그 끝부분에서 다음 줄 찾기:

```
[INFO] Filtered N → M (after sent_history dedupe)
[INFO] AI filter result (final): total=N
[INFO] Run completed. Daily sent: {'kakao': X, 'email': X, 'telegram': X}
```

- AI filter result가 0이면 → 수집 윈도우에 보고 가치 있는 기사 없음 (정상)
- daily sent가 모두 0이면 → 채널 활성화 또는 토큰 문제

**해결**:

| 진단 결과 | 해결 |
|---|---|
| 정규 시각이 아닌 manual 슬롯 | 정상 (다음 정규 시각 기다림) |
| 모든 채널 sent=0 | Variables 확인 (`ENABLE_KAKAO/EMAIL/TELEGRAM`) |
| 특정 채널만 0 | 그 채널 섹션 참조 (A-2, A-3, A-4) |

### A-2. 카카오톡만 안 옴

GitHub Actions 로그에서 `[ERROR] Kakao failed` 줄 찾기.

| 에러 키워드 | 원인 | 해결 |
|---|---|---|
| `KOE322` | refresh_token 만료 (60일) | INSTALL.md 4-A-7 다시 진행, 새 token으로 Secret 갱신 |
| `KOE010` | Client Secret 활성화 | INSTALL.md 4-A-5에서 비활성화 |
| `KOE006` | Redirect URI 불일치 | INSTALL.md 4-A-3 확인 |
| `403` | 권한 없음 | 4-A-4 동의 항목 다시 확인 |
| `over 200 chars` | 본문 200자 초과 (방어 코드로 잘림) | 자체 처리, 무시 가능 |
| `KOE_TIMEOUT` | 카카오 서버 지연 | 다음 슬롯에 자연 회복 |

### A-3. 이메일만 안 옴

GitHub Actions 로그에서 `[ERROR] Email failed` 또는 `SMTP` 키워드 검색.

| 에러 키워드 | 원인 | 해결 |
|---|---|---|
| `SMTPAuthenticationError` | 앱 비밀번호 오류 | Gmail 2단계 인증 확인 후 INSTALL.md 4-B-2 재발급 |
| `SMTP_USER not set` | Secret 미등록 | GitHub Secrets에 `SMTP_USER` 등록 |
| `EMAIL_RECIPIENTS empty` | 수신자 미등록 | Secret 등록 또는 형식 확인 (쉼표 구분) |
| `SMTPRecipientsRefused` | 수신 거부 | 수신자 주소 오타 확인 |
| `Connection refused` | SMTP 포트 차단 | `SMTP_PORT=465` Variable 시도 (SSL) |
| `Mail delivery failed` | 회사 메일 서버 차단 | 회사 보안팀에 발신 도메인 화이트리스트 요청 |

**스팸함 확인 필수**: 첫 발송 후 반드시 스팸함 확인.

**Outlook 데스크톱에서 디자인 깨짐**: 정상. table 기반 + ● 점 디자인이 적용되어 있으나 일부 Outlook 버전에서 색깔이 약하게 표시될 수 있음.

### A-4. 텔레그램만 안 옴

GitHub Actions 로그에서 `[ERROR] Telegram failed` 검색.

| 에러 키워드 | 원인 | 해결 |
|---|---|---|
| `chat_not_found` | chat_id 잘못됨 또는 봇이 추가 안 됨 | INSTALL.md 4-C-2 재확인. 봇과 `/start` 먼저 |
| `bot was blocked by the user` | 사용자가 봇 차단 | 차단 해제 후 재시도 |
| `Unauthorized` | 봇 토큰 오류 | BotFather에서 토큰 재발급 |
| `Bad Request: message is too long` | 4000자 초과 | 자동 분할되어야 함. 발생 시 코드 점검 필요 |
| `Forbidden: bot can't initiate conversation` | 1:1 채팅 시작 안 함 | 봇 검색 후 `/start` 누르기 |

**그룹 발송 시 주의**:
- 봇이 그룹 메시지 보려면 BotFather에서 **Group Privacy: disabled** 설정 필요
- 또는 봇을 그룹의 **관리자**로 추가

### A-5. 매번 같은 기사가 다시 옴

**원인**: 회차 간 중복 차단이 동작 안 함.

**진단**: GitHub Actions 로그에서 다음 확인:
```
[INFO] Filtered N → M (after sent_history dedupe)
```

이 줄이 안 보이면 sent_history.py가 동작 안 함.

**원인 가능성**:

1. **`.sent_history.json` commit 실패**: 저장소가 private이고 PAT 권한 부족
2. **`.gitignore`에 `.sent_history.json` 포함**: 저장 안 됨
3. **AI가 같은 사건에 다른 event_group 부여**: 코드는 동작하나 AI 출력 문제

**해결**:

1. PAT 권한 재발급: INSTALL.md 5-1에서 **Contents: Read and write** 확인
2. `.gitignore`에서 `.sent_history.json` 줄 제거
3. AI 프롬프트 (`prompt/kih_daily_news_agent.md`) §4의 이벤트 그룹화 규칙 강화

### A-6. 다른 회사 기사가 옴

**증상**: 회사명 검색에 다른 회사 기사가 잡혀서 발송됨. 예: "한국투자증권 ◯◯기업 분석" 같은 리서치 보고서가 발송됨.

**원인**: AI 필터링이 약함.

**해결**:

1. `prompt/kih_daily_news_agent.md`의 §4.3 (리서치 보고서 제외 규칙) 확인
2. 약하면 명시적 키워드 추가:
   ```
   다음 기사는 무조건 제외:
   - 제목에 "목표주가", "투자의견", "BUY", "HOLD" 포함
   - 본문이 ◯◯연구원의 종목 분석
   - "한국투자증권은 ◯◯기업 ..." 형태로 시작하는 분석문
   ```

### A-7. 부정 기사가 중립으로 분류됨

**원인**: AI 감성 분류 기준이 보수적.

**해결**: `prompt/kih_daily_news_agent.md`의 §5 (감성 분석)에 명시적 예시 추가:
```
다음은 negative로 분류:
- 검사·제재·과징금 관련 기사
- 자회사 부실, 손실 보도
- 임원진 갑질·구속·해임
- 영업이익 전년 대비 감소 (10%+)
```

### A-8. 한 채널만 실패 후 다른 채널은 정상 (이건 정상)

**증상**: 카카오는 안 왔는데 이메일·텔레그램은 왔음 (또는 그 반대).

**원인**: 채널별 try/except 격리 동작 (정상).

**해결**: 실패한 채널 섹션 (A-2, A-3, A-4) 참조하여 해당 채널만 수정.

---

## B. 일정·시각 관련 문제

### B-1. 17:00에 메시지가 안 옴 (평일)

**진단**: cron-job.org Dashboard > **KIH-bot-1700** > History 확인.

| Status | 의미 |
|---|---|
| HTTP 204 | GitHub Actions로 트리거 성공. GitHub Actions 로그 확인 |
| HTTP 401 | PAT 만료 |
| HTTP 404 | 저장소 URL 오타 또는 PAT 권한 부족 |
| HTTP 422 | workflow 파일 문법 오류 |
| Not executed | cron job이 비활성화됨 |

### B-2. 슬롯이 manual로 잡힘

**증상**: 로그에 `[INFO] Matched slot: manual` 표시.

**원인**: 현재 시각이 SLOT_CONFIG의 4개 슬롯 ±15분 범위 밖.

**해결**:

1. cron-job.org 시각과 `src/main.py`의 `SLOT_CONFIG` 일치 여부 확인
2. cron-job.org Timezone이 **Asia/Seoul**인지 확인 (UTC면 9시간 어긋남)
3. 두 곳을 정확히 일치시킨 후 다음 정규 시각까지 대기

**중요**: 두 곳의 시각이 일치하지 않으면 슬롯 매칭 실패 → manual 슬롯 → 4시간 윈도우로 발송됨 (스팸 가능성).

### B-3. 17:00에 한 번도 발송 안 됨 (이전 17:30이었으면)

**원인**: 이전 가이드에서 17:30으로 잘못 안내된 경우가 있었음. 정정 시각은 **17:00**.

**해결**:

1. cron-job.org의 **KIH-bot-1700** cron job: Hours=17, Minutes=0 확인
2. `src/main.py`의 SLOT_CONFIG에서 close 슬롯 시각이 (17, 0)인지 확인
3. 두 곳 모두 17:00이어야 함

### B-4. 주말에 발송 안 옴

**정상 동작**. 주말 (토/일) 및 한국 공휴일에는 close 슬롯(17:00)만 발송. 다른 슬롯은 cron이 트리거되어도 즉시 종료.

GitHub Actions 로그에서 다음 확인:
```
[INFO] Off-day detected (Saturday). Only close slot will send.
[INFO] Current slot 'morning' is not close slot. Exiting.
```

이게 보이면 정상 동작.

### B-5. 공휴일에 발송이 됨 (안 됐어야 하는 슬롯)

**원인 가능성**:

1. **공휴일 데이터 미반영**: Python `holidays` 패키지 버전이 오래됨
2. **임시 공휴일 미포함**: 정부가 임시로 지정한 공휴일은 반영 안 될 수 있음

**해결**:

1. `requirements.txt`의 `holidays>=0.40` 버전 확인
2. 새 GitHub Actions 실행 시 최신 버전 자동 설치
3. 임시 공휴일은 코드에서 수동 추가:
   ```python
   # main.py의 _is_holiday_kr 함수에 추가
   CUSTOM_HOLIDAYS = ["2026-10-09", "2026-12-30"]  # 임의 추가
   ```

### B-6. 같은 시각에 두 번 트리거됨

**증상**: 07:40에 cron-job.org가 두 번 트리거되어 GitHub Actions가 두 번 실행됨.

**원인 가능성**:

1. cron-job.org에 같은 시각의 cron job이 중복 등록됨
2. cron-job.org가 retry 시도

**해결**:

1. cron-job.org Dashboard에서 중복된 cron job 삭제
2. 두 번째 실행은 락 파일 검사로 자동 종료 (정상):
   ```
   [INFO] Slot 'morning' was sent X minutes ago. Skip.
   ```

---

## C. API 인증 및 키 관련

### C-1. Gemini API 키 만료/한도 초과

**증상**: GitHub Actions 로그에 `Gemini API call failed`.

| 에러 키워드 | 원인 | 해결 |
|---|---|---|
| `API key not valid` | 키 무효 | Google AI Studio에서 키 재발급 |
| `429 RESOURCE_EXHAUSTED` | 일 1500 요청 한도 초과 | 다음 날까지 대기 또는 다른 모델로 전환 |
| `403 PERMISSION_DENIED` | API 비활성화 | Google Cloud Console에서 Generative Language API 활성화 |
| `Model not found` | 모델명 오류 | `GEMINI_MODEL` Variable 확인 |

**키 한도 확인**: https://aistudio.google.com/apikey > 본인 키 옆 Usage

### C-2. AI 백엔드 변경 (Gemini ↔ Claude)

**Gemini에서 Claude로 되돌리기**:

1. `requirements.txt` 수정:
   ```
   anthropic>=0.40.0
   requests>=2.31.0
   json-repair>=0.30.0
   holidays>=0.40
   ```

2. `src/ai_processor.py` 교체: 저장소 commit history에서 이전 Anthropic 버전 복원
   - 또는 백업된 `ai_processor_anthropic_backup.py` 사용 (이름을 `ai_processor.py`로 변경)

3. `.github/workflows/daily.yml` 환경변수 교체:
   ```yaml
   # GEMINI_API_KEY: ...  → 주석 처리 또는 삭제
   ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
   ```

4. GitHub Secrets에 `ANTHROPIC_API_KEY` 등록 (이전에 등록했으면 그대로 사용)

**다른 Gemini 모델로 변경**:

GitHub Variables에 `GEMINI_MODEL` 추가:

| 값 | 특징 |
|---|---|
| `gemini-3-flash-preview` | 기본. 최신, 무료 한도 1500/일 |
| `gemini-2.5-flash` | 안정 버전, 무료 한도 있음 |
| `gemini-2.5-flash-lite` | 가장 빠르고 저렴 |
| `gemini-3-pro-preview` | 더 정확, 무료 한도 **없음** |

### C-3. 카카오 토큰 만료 (KOE322)

**증상**: GitHub Actions 로그에 `KOE322 expired_or_invalid_refresh_token`.

**원인**: refresh_token 60일 만료.

**해결**: INSTALL.md 4-A-7 절차 다시 진행 → 새 refresh_token으로 `KAKAO_REFRESH_TOKEN` Secret 갱신.

**예방**: 60일마다 알림 설정 (캘린더 반복 일정 등).

### C-4. GitHub PAT 만료

**증상**: cron-job.org Last execution에 **HTTP 401**.

**해결**: INSTALL.md 5-1 절차 다시 진행 → 새 PAT로 cron-job.org 4개 cron job의 Authorization 헤더 갱신.

**예방**: 11개월쯤 지난 시점에 갱신 알림 설정.

### C-5. Naver API 한도 초과

**증상**: GitHub Actions 로그에 `Naver search failed: 429`.

**원인**: 일 25,000회 한도 초과 (정상 사용 시 도달 어려움).

**해결**:
1. 같은 키를 다른 봇과 공유 중인지 확인
2. 새 Naver 앱 등록하여 새 키 발급
3. 검색 빈도 최적화: 회사당 검색 결과 수 줄이기 (`naver_news.py`의 `display` 파라미터)

---

## D. 발송 내용 관련

### D-1. 메시지가 잘려서 옴

**카카오톡 200자 제한 도달**:

증상: 외부 매체 메시지에 URL이 잘려 옴.

원인: URL이 매우 긴 매체 + 다른 entry와 함께 묶인 경우.

해결: `src/kakao_sender.py`의 `EXT_TITLE_MAX`를 줄여서 (25 → 20) 더 짧게 압축. 또는 그 매체 1건씩만 묶이도록.

**텔레그램 4000자 제한**:

자동 분할되어야 정상. 안 되면 `src/telegram_sender.py`의 분할 로직 점검.

### D-2. 같은 사건이 여러 매체에서 와서 도배됨

**증상**: 같은 사건을 매일경제, 한경, 머니투데이 등이 보도해서 3~5건 발송됨.

**원인 가능성**:

1. AI가 event_group을 부여 안 했거나 다르게 부여
2. 회차 간 dedupe는 동작하나 같은 회차 내 그룹 dedupe는 약함

**해결**:

1. `prompt/kih_daily_news_agent.md` §4의 event_group 규칙 강화
2. `src/ai_processor.py`의 user_message에서 event_group 지시문 더 명확히
3. 시간이 갈수록 AI가 학습 (Gemini 3 Flash는 같은 프롬프트로 점차 잘 묶음)

### D-3. 외부 매체 너무 많음 (도배)

**원인**: AI가 외부 매체 기사를 잘 필터링 못함.

**해결**: 시스템 프롬프트 (`prompt/kih_daily_news_agent.md`) §4에서 외부 매체 필터링 강화. 단, 다음 균형 주의:

- 너무 강하게 필터: 중요한 외부 매체 기사 누락
- 너무 약하게 필터: 도배

권장: importance 6 이상만 통과 (현재 기본값).

### D-4. 회사명 분류 오류

**증상**: `[증권]` 태그로 와야 할 기사가 `[지주]`로 옴.

**원인**: AI의 main_company 판정 오류.

**해결**:

1. 시스템 프롬프트 §2 (모니터링 대상) + ai_processor.py user_message의 main_company 판정 원칙 확인
2. 자회사 활동인데 지주로 분류된 경우 다음 추가:
   ```
   다음 패턴은 자회사로 분류:
   - "한국투자금융지주의 자회사 ◯◯이 ..."
   - "한국투자금융지주 산하 ◯◯의 ..."
   - 본문이 한국투자증권의 영업 활동을 다루면 main_company="한국투자증권"
   ```

---

## E. 다른 시각으로 운영 시 정정 사항

**예시 (07:40 / 09:10 / 13:30 / 17:00 운영 시 외 다른 스케줄)**:

본 봇은 07:40 / 09:10 / 13:30 / 17:00 운영이 기본. 다른 시각으로 바꾸려면 **반드시 두 곳을 동시에**:

1. **cron-job.org 4개 cron job**: 각 cron의 시각 변경
2. **`src/main.py`의 `SLOT_CONFIG`**: 동일한 시각으로 변경

일치 안 시키면 매번 manual 슬롯으로 빠짐 (스팸 가능성).

확인 방법: GitHub Actions 실행 후 로그에서 `Matched slot: manual`이 아닌 정규 슬롯명 (`morning`, `pre_open`, `midday`, `close`) 표시되어야 정상.

---

## F. 운영 일상 점검

### 월 1회 점검

- [ ] Google AI Studio Usage 페이지에서 일 사용량 1500 한도 안에 있는지 확인
- [ ] GitHub Actions 사용 시간 (Settings > Actions > Usage)
- [ ] 카카오 토큰 만료까지 남은 일수 (60일 주기 알림)

### 분기 1회 점검

- [ ] Naver Developers에서 검색 사용량 확인
- [ ] GitHub PAT 만료까지 남은 일수 (1년 주기 알림)
- [ ] 모니터링 대상 11개 회사가 최신 상태인지 (편입·매각·합병 확인)

### 1년 후 점검

- [ ] GitHub PAT 갱신
- [ ] requirements.txt의 패키지 최신 버전 확인
- [ ] Python 버전 (.github/workflows/daily.yml) 확인 (3.11 → 3.12 등)

---

## G. 도움 요청 시 정리할 정보

문제 해결이 안 되어 도움을 요청할 때 다음을 정리:

1. **증상**: 무엇이 안 되는지 (예: 17:00에 카톡 안 옴, 이메일은 정상)
2. **언제부터**: 처음부터인지, 갑자기인지 (날짜)
3. **GitHub Actions 로그**: 실패한 실행의 마지막 50줄
4. **cron-job.org Last execution**: HTTP status
5. **변경 이력**: 최근에 무엇을 바꿨는지 (코드, Secret, cron 시각 등)

이 정보가 있어야 빠른 진단 가능.

---

## See Also

- [INSTALL.md](INSTALL.md) - 설치 가이드
- [ARCHITECTURE.md](ARCHITECTURE.md) - 시스템 구조 상세
- [project/README.md](../project/README.md) - 운영 가이드
