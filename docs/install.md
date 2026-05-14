# 멀티채널 자동 뉴스 봇 설치 가이드

이 가이드를 따라하면 매일 정해진 시각에 본인이 선택한 채널(카카오톡, 이메일, 텔레그램)로 자동 뉴스 알림이 오는 봇을 설치할 수 있습니다.

## 이 봇이 하는 일

- 매일 4번 (평일 07:40 / 09:10 / 13:30 / 17:00) 정해진 시각에 뉴스 메시지 전송
- 주말·공휴일에는 17:00 한 번만 발송 (한국 공휴일 자동 인식)
- 11개 회사 관련 뉴스를 네이버 뉴스에서 수집
- Google Gemini AI가 단순 홍보 기사 제외, 부정 기사 우선 표시, 감성 분류, 같은 사건 묶기
- 최근 3일간 이미 발송한 기사는 자동 제외 (회차 간 중복 차단)
- 카카오톡 / 이메일 / 텔레그램 중 원하는 채널만 골라서 사용

## 채널별 특징

| 채널 | 장점 | 단점 |
|---|---|---|
| **카카오톡** | 한국인 친숙, 즉시 푸시 | 본인 '나와의 채팅방'만 가능, 200자 제한 |
| **이메일** | 다중 수신자, 회사 메일 포함, 풍부한 HTML | 알림 약함 |
| **텔레그램** | 그룹/채널 가능, 4000자, 빠른 알림 | 한국에서 사용자 적음 |

세 채널 중 하나만 써도 되고, 모두 함께 써도 됩니다.

## 필요한 것

- 사용할 채널의 계정 (카톡/Gmail/텔레그램)
- 이메일 (4~5개 서비스 가입용)
- PC 또는 노트북
- 약 2~3시간 (처음 설치 기준)
- **비용 0원** (모든 외부 API의 무료 한도 안에서 운영)

## 코딩 지식이 필요한가?

**필요 없습니다.** 모든 코드는 이 패키지에 포함되어 있습니다. 가이드대로 복사·붙여넣기만 하면 됩니다.

---

# 전체 설치 흐름

총 7단계입니다. 한 단계씩 차근차근 진행하면 됩니다.

| 단계 | 내용 | 소요 시간 |
|---|---|---|
| 1 | 공통 외부 서비스 가입 + API 키 발급 | 30분 |
| 2 | GitHub 저장소 만들기 + 코드 업로드 | 30분 |
| 3 | 공통 GitHub Secrets 등록 | 10분 |
| 4-A | 카카오톡 셋업 (선택) | 30분 |
| 4-B | 이메일 셋업 (선택) | 15분 |
| 4-C | 텔레그램 셋업 (선택) | 15분 |
| 5 | cron-job.org에서 자동 실행 등록 | 20분 |
| 6 | 테스트 및 검증 | 20분 |

채널은 원하는 것만 선택하면 됩니다 (4-A, 4-B, 4-C 중 하나 이상).

---

# 1단계: 공통 외부 서비스 가입 및 API 키 발급

봇이 사용하는 공통 서비스에 가입하고 API 키를 받습니다.

## 1-1. 네이버 개발자 센터 (뉴스 검색용)

**가입 절차:**

1. https://developers.naver.com 접속
2. 우상단 **로그인** → 본인 네이버 계정으로 로그인
3. 상단 메뉴 **Application > 애플리케이션 등록** 클릭
4. 다음과 같이 입력:
   - 애플리케이션 이름: `KIH News Bot` (임의)
   - 사용 API: **검색** 선택
   - 비로그인 오픈 API 서비스 환경: **WEB 설정** 선택
   - 웹 서비스 URL: `https://example.com` 입력
5. 등록 클릭

**API 키 확인:**

등록 완료 후 다음 두 값을 메모장에 저장:
- **Client ID**
- **Client Secret**

**무료 한도**: 하루 25,000회. 본 봇은 일 평균 40회 사용 (0.2%).

## 1-2. Google AI Studio (Gemini API용)

**가입 절차:**

1. https://aistudio.google.com/apikey 접속
2. Google 계정으로 로그인
3. **Create API key** 버튼 클릭
4. 기존 Google Cloud 프로젝트가 있으면 선택, 없으면 새로 생성
5. 발급된 API 키 (보통 `AIzaSy...`로 시작) 메모장에 저장

**무료 한도**: 일 1,500 요청. 본 봇은 일 4~5회 사용 (0.3%). **결제 정보 등록 불필요.**

> 만약 Anthropic Claude API를 쓰고 싶다면 TROUBLESHOOTING.md의 "AI 백엔드 변경" 섹션 참조. 본 가이드는 무료 운영을 위해 Gemini 기준입니다.

## 1-3. GitHub (코드 저장 + 실행 환경)

1. https://github.com 접속
2. **Sign up** → 이메일로 가입
3. 무료 plan 선택 (Free)
4. 이메일 인증 완료

**메모장에 저장:**
- GitHub 사용자명

## 1-4. cron-job.org (정해진 시각에 자동 실행)

1. https://cron-job.org 접속
2. **Sign up** → 이메일로 가입
3. 이메일 인증 완료

신용카드 등록 불필요.

## 1단계 완료 체크리스트

- [ ] 네이버 Client ID
- [ ] 네이버 Client Secret
- [ ] Gemini API Key (`AIzaSy...`)
- [ ] GitHub 사용자명
- [ ] cron-job.org 계정 (가입 완료)

---

# 2단계: GitHub 저장소 만들기 및 코드 업로드

## 2-1. 저장소 생성

1. GitHub 로그인 후 우상단 **+** → **New repository**
2. 입력:
   - Repository name: `kih-news-bot` (임의)
   - **Private** 선택 (중요! API 키 노출 방지)
   - "Add a README file" 체크
3. **Create repository** 클릭

## 2-2. 코드 업로드

이 패키지의 `project/` 폴더 안의 모든 파일을 GitHub에 업로드합니다.

**방법: 웹 브라우저로 업로드**

1. 저장소 페이지에서 **Add file > Upload files** 클릭
2. `project/` 폴더 안의 다음 파일들 업로드:
   - `README.md`
   - `requirements.txt`
   - `.gitignore`
3. **Commit changes** 클릭

4. **src 폴더 만들기**:
   - **Add file > Create new file** 클릭
   - 파일명에 `src/main.py` 입력 (자동으로 src 폴더 생성)
   - 본인 패키지의 `project/src/main.py` 내용 복사·붙여넣기 → Commit
5. 같은 방식으로 src 폴더에 다음 8개 파일 추가:
   - `src/naver_news.py`
   - `src/ai_processor.py`
   - `src/sent_history.py`
   - `src/kakao_sender.py`
   - `src/email_sender.py`
   - `src/telegram_sender.py`
   - `src/token_manager.py`

6. **prompt 폴더 만들기**:
   - Add file > Create new file → `prompt/kih_daily_news_agent.md` 입력
   - 본인 패키지의 `project/prompt/kih_daily_news_agent.md` 내용 복사·붙여넣기 → Commit

7. **워크플로우 파일 만들기**:
   - Add file > Create new file → `.github/workflows/daily.yml` 입력
   - 본인 패키지의 `project/.github/workflows/daily.yml` 내용 복사·붙여넣기 → Commit

## 2-3. 파일 구조 확인

저장소가 다음과 같은 구조여야 합니다:

```
kih-news-bot/
├── README.md
├── requirements.txt
├── .gitignore
├── .github/
│   └── workflows/
│       └── daily.yml
├── src/
│   ├── main.py
│   ├── naver_news.py
│   ├── ai_processor.py
│   ├── sent_history.py
│   ├── kakao_sender.py
│   ├── email_sender.py
│   ├── telegram_sender.py
│   └── token_manager.py
└── prompt/
    └── kih_daily_news_agent.md
```

---

# 3단계: 공통 GitHub Secrets 등록

API 키들을 GitHub에 안전하게 저장합니다.

## 3-1. Secrets 페이지 진입

1. 저장소에서 상단 **Settings** 클릭
2. 좌측 메뉴 **Secrets and variables > Actions** 클릭

## 3-2. 공통 3개 Secret 등록

**New repository secret** 버튼을 3번 클릭하여 다음을 각각 등록:

| Name (정확히 입력) | Value |
|---|---|
| `NAVER_CLIENT_ID` | 1-1의 Client ID |
| `NAVER_CLIENT_SECRET` | 1-1의 Client Secret |
| `GEMINI_API_KEY` | 1-2의 Gemini API Key |

> ⚠️ Name은 대소문자 정확히 일치해야 합니다.

## 3-3. 채널 토글 Variables 등록 (선택)

기본값(카카오만 ON)을 그대로 두려면 건너뛰셔도 됩니다. 다른 채널을 추가로 켜고 싶으면:

1. GitHub > 저장소 > Settings > Secrets and variables > Actions
2. 상단 **Variables** 탭 클릭 (Secrets 탭 아님!)
3. **New repository variable**로 다음 등록:

| Name | 값 | 비고 |
|---|---|---|
| `ENABLE_KAKAO` | `true` 또는 `false` | 기본 true |
| `ENABLE_EMAIL` | `true` 또는 `false` | 기본 false |
| `ENABLE_TELEGRAM` | `true` 또는 `false` | 기본 false |

---

# 4-A단계 (선택): 카카오톡 셋업

카카오톡으로 받고 싶으면 진행. 안 쓸 거면 다음 단계로 건너뜀.

## 4-A-1. 카카오 디벨로퍼스 앱 등록

1. https://developers.kakao.com 접속
2. **로그인** → 본인 카카오 계정으로 로그인
3. 약관 동의
4. 상단 **내 애플리케이션** 클릭
5. **애플리케이션 추가하기** 클릭
6. 입력:
   - 앱 이름: `KIH News BOT`
   - 사업자명: 본인 이름 (개인 명의)
7. 저장

## 4-A-2. 플랫폼 등록

1. 생성된 앱 클릭
2. 좌측 **앱 > 플랫폼** 클릭
3. **Web 플랫폼 등록** 클릭
4. 사이트 도메인: 다음 5개를 **각각 한 줄씩** 추가 (Enter로 구분):
   ```
   https://example.com
   https://n.news.naver.com
   https://m.news.naver.com
   https://news.naver.com
   https://search.naver.com
   ```
5. 저장

## 4-A-3. 카카오 로그인 활성화

1. 좌측 **제품 설정 > 카카오 로그인** 클릭
2. **활성화 설정** 상태: **ON**
3. **Redirect URI 등록** 클릭
4. 다음 입력 후 저장:
   ```
   https://example.com/oauth
   ```

## 4-A-4. 동의 항목 설정

1. 좌측 **제품 설정 > 카카오 로그인 > 동의 항목** 클릭
2. **카카오톡 메시지 전송 (talk_message)** 찾기
3. **설정** 클릭
4. **선택 동의** 선택 후 저장

## 4-A-5. 보안 설정 확인

1. 좌측 **앱 > 보안** 클릭
2. **Client Secret** 설정이 **사용 안함** 상태인지 확인

> ⚠️ Client Secret이 활성화되어 있으면 다음 단계에서 KOE010 오류 발생

## 4-A-6. API 키 확인

1. 좌측 **앱 > 앱 키** 클릭
2. **REST API 키** 메모장에 저장

## 4-A-7. OAuth 토큰 발급

**Authorization Code 받기**:

다음 URL의 `본인_REST_API_KEY` 부분을 본인 키로 바꿔서 브라우저 주소창에 입력:

```
https://kauth.kakao.com/oauth/authorize?client_id=본인_REST_API_KEY&redirect_uri=https://example.com/oauth&response_type=code&scope=talk_message
```

1. 카카오 로그인 → 동의 → `https://example.com/oauth?code=XXXXX...` 로 리디렉션
2. 주소창의 `code=` 뒤 값을 복사 (10분 안에 다음 단계 진행)

**Access Token + Refresh Token 발급** (reqbin.com 사용):

1. https://reqbin.com 접속
2. Method: **POST**, URL: `https://kauth.kakao.com/oauth/token`
3. **Content Tab** → **Form** → 다음 필드 입력:
   - `grant_type`: `authorization_code`
   - `client_id`: 본인 REST API Key
   - `redirect_uri`: `https://example.com/oauth`
   - `code`: 방금 받은 Authorization Code
4. **Send** 클릭
5. 응답에서 `refresh_token` 값 메모장에 저장 (60일 유효)

## 4-A-8. 카카오 Secrets 등록

GitHub > Settings > Secrets and variables > Actions > **New repository secret**:

| Name | Value |
|---|---|
| `KAKAO_REST_API_KEY` | 4-A-6의 REST API 키 |
| `KAKAO_REFRESH_TOKEN` | 4-A-7의 refresh_token |

## 4-A-9. 자주 발생하는 오류

| 오류 코드 | 원인 | 해결 |
|---|---|---|
| KOE006 | Redirect URI 미등록 | 4-A-3에 `https://example.com/oauth` 등록 확인 |
| KOE010 | Client Secret 활성화 | 4-A-5에서 비활성화 |
| KOE322 | 토큰 만료 또는 무효 | 4-A-7부터 다시 진행 |

---

# 4-B단계 (선택): 이메일 셋업

Gmail로 발신, 본인이 지정한 수신자들에게 발송. 회사 메일(Microsoft Exchange) 포함 가능.

## 4-B-1. Gmail 2단계 인증 활성화

1. 발신용 Gmail 계정으로 로그인된 상태에서 https://myaccount.google.com 접속
2. 왼쪽 메뉴 **보안** 클릭
3. **2단계 인증**이 **사용** 상태인지 확인
4. 사용 안 함이면 클릭하여 켜기 (휴대폰 번호 인증)

## 4-B-2. 앱 비밀번호 발급

일반 Gmail 비밀번호로는 SMTP 로그인이 안 됩니다.

1. https://myaccount.google.com/apppasswords 직접 접속
2. "찾고 있는 설정을 계정에서 사용할 수 없습니다"가 나오면 2단계 인증을 다시 확인
3. **앱 이름**: `KIH News Bot` 입력
4. **만들기** 클릭
5. 16자리 비밀번호 표시됨 (예: `abcd efgh ijkl mnop`)
6. **공백 제거** 후 메모장에 저장 (예: `abcdefghijklmnop`)

## 4-B-3. 수신자 이메일 정리

쉼표로 구분하여 한 줄로 정리:
```
me@gmail.com, boss@example.com, team@kih.co.kr
```

## 4-B-4. 이메일 Secrets 등록

GitHub > Settings > Secrets and variables > Actions > **New repository secret**:

| Name | Value | 비고 |
|---|---|---|
| `SMTP_USER` | 발신용 Gmail 주소 | 예: `myname@gmail.com` |
| `SMTP_PASSWORD` | 4-B-2의 16자리 앱 비밀번호 | 공백 제거 |
| `EMAIL_RECIPIENTS` | 4-B-3의 수신자 목록 | 쉼표 구분 |

그리고 **Variables** 탭에서 (선택):

| Name | Value | 비고 |
|---|---|---|
| `ENABLE_EMAIL` | `true` | 이메일 활성화 |
| `EMAIL_FROM_NAME` | `KIH 뉴스봇` (임의) | 발신자 표시 이름 |

## 4-B-5. 회사 메일 수신 시 주의

Microsoft Exchange 같은 회사 메일 서버는 외부 발신을 기본 차단하는 경우가 많습니다.

- **첫 발송 후 스팸함/격리함 확인**
- 스팸 분류 시 **스팸 아님** 또는 **신뢰할 수 있는 발신자** 추가
- 격리 시 본인 또는 회사 보안팀에 풀어달라고 요청

---

# 4-C단계 (선택): 텔레그램 셋업

텔레그램 봇으로 본인 또는 그룹/채널에 발송.

## 4-C-1. BotFather로 봇 만들기

1. 텔레그램 앱에서 **@BotFather** 검색 (파란 체크 마크 있는 공식 봇)
2. `/start` 입력
3. `/newbot` 입력
4. 봇 이름 입력 (예: `KIH News Bot`)
5. 봇 username 입력 (반드시 `_bot` 또는 `Bot`으로 끝나야 함, 예: `kih_news_alert_bot`)
6. BotFather가 토큰 (예: `123456789:ABC...`) 알려줌 → 메모장에 저장

## 4-C-2. Chat ID 알아내기

**케이스 A: 본인 텔레그램으로 받기**

1. 텔레그램에서 본인 봇 검색 → 채팅창 열기
2. **START** 버튼 또는 `/start` 입력 후 전송
3. 아무 메시지 한 번 더 전송 (예: `hello`)
4. 브라우저에서 다음 URL 접속 (`{토큰}` 치환):
   ```
   https://api.telegram.org/bot{토큰}/getUpdates
   ```
5. 응답 JSON에서 `"chat":{"id": 12345678 ...}`의 숫자가 본인 chat_id

> 응답이 `{"ok":true,"result":[]}`로 비어있으면 봇에게 메시지를 안 보낸 상태. 2~3단계 다시 진행.

**케이스 B: 그룹 채팅방으로 받기**

1. 텔레그램에서 그룹 생성 → 봇 추가
2. 그룹에 아무 메시지 전송
3. 같은 URL에서 `"chat":{"id": -987654321 ..."type":"group"}` 확인 (음수, `-100`으로 시작 가능)

## 4-C-3. 텔레그램 Secrets 등록

| Name | Value | 비고 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 4-C-1의 토큰 | 콜론 포함 |
| `TELEGRAM_CHAT_IDS` | 4-C-2의 chat_id | 여러 명이면 쉼표 구분 |

**Variables**:

| Name | Value |
|---|---|
| `ENABLE_TELEGRAM` | `true` |

---

# 5단계: cron-job.org에서 자동 실행 등록

## 5-1. GitHub Personal Access Token 발급

1. GitHub 우상단 본인 아바타 → **Settings**
2. 좌측 최하단 **Developer settings**
3. **Personal access tokens > Fine-grained tokens**
4. **Generate new token** 클릭
5. 입력:
   - Token name: `cron-job-trigger`
   - Expiration: **Custom > 1년 후**
   - Repository access: **Only select repositories** → 본인 저장소 선택
   - Repository permissions:
     - **Actions**: **Read and write** ✅
     - **Contents**: **Read and write** ✅ (락 파일·발송 이력 commit용)
6. **Generate token** 클릭
7. `github_pat_...` 토큰 메모장에 저장 (한 번만 표시)

## 5-2. cron-job.org에서 4개 Cron Job 생성

cron-job.org Dashboard 접속 후 4개 cron job 생성:

### 첫 번째 (07:40 KST)

1. **Cronjobs > CREATE CRONJOB** 클릭
2. **Common 설정**:
   - Title: `KIH-bot-0740`
   - URL: `https://api.github.com/repos/본인username/저장소이름/actions/workflows/daily.yml/dispatches`
3. **Schedule 설정**:
   - Timezone: **Asia/Seoul**
   - Hours: **7**, Minutes: **40**
4. **Advanced**:
   - Request method: **POST**
   - Headers:
     | Name | Value |
     |---|---|
     | `Accept` | `application/vnd.github+json` |
     | `Authorization` | `Bearer 본인_PAT_토큰값` |
     | `X-GitHub-Api-Version` | `2022-11-28` |
     | `Content-Type` | `application/json` |
     | `User-Agent` | `cron-job-org-kih-bot` |
   - Request body: `{"ref":"main"}`
5. **Create** 클릭

### 나머지 3개 (Title과 시각만 다르게)

| Title | Hours | Minutes |
|---|---|---|
| `KIH-bot-0910` | 9 | 10 |
| `KIH-bot-1330` | 13 | 30 |
| `KIH-bot-1700` | 17 | 0 |

---

# 6단계: 테스트 및 검증

## 6-1. 수동 발송 테스트

1. GitHub > 저장소 > **Actions** 탭
2. **KIH Daily News Bot** 클릭
3. **Run workflow** → **Run workflow** 클릭
4. 1~2분 후 실행 결과 확인 (초록 체크 = 성공)

## 6-2. 발송 확인

- **카카오톡**: '나와의 채팅방' 확인
- **이메일**: 등록한 모든 수신자 메일함 확인 (스팸함도)
- **텔레그램**: 봇과의 채팅창 확인

> 그 시각 직전 4시간에 새 기사가 없으면 메시지가 안 옵니다. 정상 동작.

## 6-3. 실패 시 진단

GitHub Actions 로그의 마지막 부분 확인. 다음과 같은 줄을 찾으세요:

```
[INFO] Run completed. Daily sent: {'kakao': N, 'email': N, 'telegram': N}
```

- 모든 채널이 0이면: 기사 0건이거나 회차 간 중복 차단
- 일부만 0이면: 해당 채널 설정 문제
- 자세한 에러: TROUBLESHOOTING.md 참조

---

# 설치 후 정기 유지보수

## 60일마다: 카카오 refresh_token 갱신 (카카오 사용 시만)

**증상**: GitHub Actions 로그에 `KOE322 expired_or_invalid_refresh_token` 에러.

**해결**: 4-A-7단계 다시 진행하여 새 refresh_token으로 GitHub Secrets 갱신.

## 1년마다: GitHub PAT 갱신

**증상**: cron-job.org Last execution에 `HTTP 401` 표시.

**해결**: 5-1단계 다시 진행하여 새 PAT로 cron-job.org 4개 cron job의 Authorization 헤더 갱신.

## 월 1회 사용량 확인

- **Google AI Studio**: 일 사용량이 1500 한도의 0.3% 수준인지 (https://aistudio.google.com/apikey)
- **GitHub**: Actions 사용 시간 (무료 한도 2,000분/월 안에 있는지)

## 모니터링 대상 회사 변경

그룹 신규 자회사 편입/매각 시 다음 파일들을 함께 갱신:

1. `src/naver_news.py`의 `TARGET_COMPANIES`
2. `src/kakao_sender.py`의 `_shorten_company` 매핑
3. `src/email_sender.py`의 `_shorten_company` 매핑
4. `src/telegram_sender.py`의 `_shorten_company` 매핑
5. `src/ai_processor.py`의 `VALID_COMPANIES`
6. `prompt/kih_daily_news_agent.md`의 §2

---

# 비용 정리

| 항목 | 무료 한도 | 본 봇 사용 | 비용 |
|---|---|---|---|
| 네이버 검색 API | 일 25,000회 | 일 40회 | **무료** |
| **Google Gemini API** | **일 1,500 요청** | **일 5회** | **무료** |
| GitHub Actions | 월 2,000분 | 월 30분 | **무료** |
| 카카오 API | 충분히 큼 | 일 평균 ~30회 | **무료** |
| Gmail SMTP | 일 500건 | 일 약 4건 | **무료** |
| Telegram Bot | 분당 30건 | 일 약 4건 | **무료** |
| cron-job.org | 분당 1회 | 일 4회 | **무료** |
| **월 총비용** | | | **$0** |

---

# FAQ

**Q. 다른 사람에게도 카톡으로 보낼 수 있나요?**
A. 카카오 정책상 본인 '나와의 채팅방'으로만 발송 가능. 다른 사람과 공유하려면 이메일 또는 텔레그램을 사용하시거나, 각자 본인 카카오 셋업을 하시면 됩니다 (AI 비용 분담 고려 필요).

**Q. 발송 시각을 바꾸려면?**
A. 두 곳 동시에 수정:
1. cron-job.org에서 각 cron job의 시각 변경
2. `src/main.py`의 `SLOT_CONFIG`도 같은 시각으로 변경

두 곳을 일치시키지 않으면 슬롯 매칭이 안 됨.

**Q. AI가 잘못된 분류를 하면?**
A. `prompt/kih_daily_news_agent.md`의 필터링 규칙(§4) 또는 `src/ai_processor.py`의 user_message 부분을 수정.

**Q. 더 좋은 AI 모델을 쓰려면?**
A. GitHub Variables에 `GEMINI_MODEL`을 추가:
- `gemini-3-pro-preview`: 더 정확하지만 유료 (무료 한도 없음)
- `gemini-2.5-flash`: 안정 버전
- `gemini-2.5-flash-lite`: 가장 저렴

**Q. 봇이 갑자기 멈췄어요.**
A. 가장 흔한 원인:
1. 카카오 refresh_token 만료 (60일 주기) → 4-A-7 재진행
2. GitHub PAT 만료 (1년 주기) → 5-1 재진행
3. Gemini API 키 만료 또는 한도 초과 → Google AI Studio 확인

**Q. Anthropic Claude로 되돌리려면?**
A. TROUBLESHOOTING.md의 "AI 백엔드 변경" 섹션 참조.

---

# 도움이 필요할 때

1. **에러 메시지 검색**: 6-3 또는 TROUBLESHOOTING.md 확인
2. **GitHub Actions 로그**: 실패한 실행의 로그에서 정확한 에러 원인 찾기
3. **공식 문서**:
   - 카카오: https://developers.kakao.com/docs
   - GitHub Actions: https://docs.github.com/actions
   - Google AI Studio: https://ai.google.dev/gemini-api/docs
   - Telegram Bot: https://core.telegram.org/bots/api

---

# 마무리

이 가이드는 한국투자금융그룹 11개 계열사 모니터링용으로 설계되었지만, **다른 회사나 토픽으로도 쉽게 응용 가능**합니다.

다른 토픽으로 사용하려면 6개 파일을 수정 (project/README.md의 "모니터링 대상 변경" 섹션 참조).

성공적으로 봇을 운영하시길 바랍니다.
