# KIH Daily News Bot

한국투자금융그룹 11개 계열사 관련 뉴스를 매일 자동으로 **카카오톡 / 이메일 / 텔레그램**으로 전송하는 봇.

## Architecture

```
cron-job.org (외부 cron, 무료)
  │
  │ HTTP POST + PAT 인증 (정해진 시각에 트리거)
  ▼
GitHub Actions workflow_dispatch
  │
  ▼
Python (src/main.py)
  ├── Naver News API (수집 + URL/제목 정규화로 중복 제거)
  ├── Sent History (.sent_history.json) — 최근 3일 발송 이력으로 회차 간 중복 차단
  ├── Google Gemini API (필터·감성분석·요약·주체 회사 재분류·이벤트 그룹화)
  └── Channel dispatch (각 채널 try/except 격리, 한 채널 실패해도 나머지 동작)
       ├── Kakao Memo API → 나와의 채팅방 (list 카드 + text 묶음 발송)
       ├── SMTP (Gmail) → 다중 수신자 이메일 (Outlook 호환 HTML)
       └── Telegram Bot API → 1:1 또는 그룹/채널 (HTML 포맷)
```

## Required GitHub Secrets

채널별로 필요한 Secrets만 등록하면 됩니다. 안 쓰는 채널은 등록 안 해도 됨.

### 공통 (필수)

| Name | Description |
|---|---|
| `NAVER_CLIENT_ID` | Naver Developers Client ID |
| `NAVER_CLIENT_SECRET` | Naver Developers Client Secret |
| `GEMINI_API_KEY` | Google AI Studio API Key (https://aistudio.google.com/apikey) |

### 카카오톡 사용 시

| Name | Description |
|---|---|
| `KAKAO_REST_API_KEY` | Kakao Developers REST API Key |
| `KAKAO_REFRESH_TOKEN` | Kakao OAuth refresh token (60일 유효) |

### 이메일 사용 시

| Name | Description |
|---|---|
| `SMTP_USER` | 발신용 Gmail 주소 |
| `SMTP_PASSWORD` | Gmail 앱 비밀번호 (16자리) |
| `EMAIL_RECIPIENTS` | 쉼표 구분 수신자 목록 |

### 텔레그램 사용 시

| Name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_IDS` | 쉼표 구분 chat_id 목록 (본인=양수, 그룹=음수) |

## GitHub Variables (선택, 기본값 작동)

| Name | Default | Description |
|---|---|---|
| `ENABLE_KAKAO` | `true` | 카카오 발송 ON/OFF |
| `ENABLE_EMAIL` | `false` | 이메일 발송 ON/OFF |
| `ENABLE_TELEGRAM` | `false` | 텔레그램 발송 ON/OFF |
| `EMAIL_FROM_NAME` | `KIH News Bot` | 이메일 발신자 표시 이름 |
| `EMAIL_DELIVERY_MODE` | `to` | `to` / `bcc` / `individual` |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP 서버 |
| `SMTP_PORT` | `587` | SMTP 포트 |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | AI 모델 (`gemini-2.5-flash`로 다운그레이드 가능) |
| `SENT_HISTORY_DAYS` | `3` | 발송 이력 보존 기간 (일) |

## Monitoring Targets (11개사)

| 회사명 | 메시지 태그 |
|---|---|
| 한국투자금융지주 | [지주] |
| 한국투자증권 | [증권] |
| 한국투자신탁운용 | [한투운용] |
| 한국투자밸류자산운용 | [밸류운용] |
| 한국투자파트너스 | [파트너스] |
| 한국투자프라이빗에쿼티 | [PE] |
| 한국투자캐피탈 | [캐피탈] |
| 한국투자저축은행 | [저축은행] |
| 한국투자리얼에셋운용 | [리얼에셋] |
| 한국투자부동산신탁 | [부동산신탁] |
| 한국투자액셀러레이터 | [AC] |

## Operation

### 자동 실행 스케줄 (KST)

cron-job.org의 4개 cron job이 정해진 시각에 GitHub Actions를 트리거.

| 슬롯 | 시각 | 수집 윈도우 | 비고 |
|---|---|---|---|
| morning | 07:40 | 직전 17:00 ~ 현재 (14.8h) | 야간·새벽 누적 |
| pre_open | 09:10 | 직전 07:40 ~ 현재 (1.6h) | 장 시작 직전 |
| midday | 13:30 | 직전 09:10 ~ 현재 (4.5h) | 점심 후 |
| close | 17:00 | 직전 13:30 ~ 현재 (3.7h) | 장 마감 후 |

슬롯 매칭은 ±15분 윈도우. 그 외 시각의 수동 트리거는 manual 슬롯(4시간 윈도우)으로 처리.

### 주말·공휴일

주말 (토/일) 및 한국 공휴일에는 **close 슬롯(17:00)만 발송**. 나머지 슬롯은 cron 트리거되어도 즉시 종료.

공휴일 인식은 Python `holidays` 패키지의 한국 공휴일 데이터 사용 (대체공휴일 포함).

### Friday Weekly Digest
금요일 07:40 슬롯에서 일일 발송 후 지난 7일 주간 종합을 추가 발송. 단, 금요일이 공휴일이면 17:00에 발송.

### Empty Window Behavior
- 수집 윈도우에 기사 0건 → 발송 생략
- 회차 간/내 중복 제거 후 0건 → 발송 생략
- AI 필터 후 0건 → 발송 생략

### Manual Trigger
GitHub > Actions > "KIH Daily News Bot" > "Run workflow" → 수동 실행 시 manual 슬롯(4시간 윈도우)으로 동작. 락 파일에 기록 안 함 (다음 정규 슬롯에 영향 없음).

## 중복 발송 방지 메커니즘

3중 차단:

1. **회차 내 중복 제거 (URL 정규화)**: 추적 파라미터(`?utm_*`, `?inflow=`, `?OutUrl=`) 제거, `m./www.` 통일하여 같은 URL 인식
2. **회차 내 중복 제거 (제목 정규화)**: `[속보]` 등 괄호 제거, 따옴표·특수문자 제거 후 비교
3. **회차 간 중복 제거 (3일 이력)**: 최근 3일간 발송한 모든 기사 URL/제목 키를 `.sent_history.json`에 저장하고 새 슬롯에서 매번 비교
4. **AI 기반 이벤트 그룹화**: 같은 사건을 다른 매체가 보도한 경우 AI가 event_group 식별자로 묶고 1건만 채택

## Maintenance

### 60일 카카오 토큰 갱신
`KAKAO_REFRESH_TOKEN`은 60일마다 갱신 필요. 만료 시 GitHub Actions 로그에 `KOE322` 에러 출력. INSTALL.md의 카카오 OAuth 단계를 다시 진행하여 갱신.

### 1년 GitHub PAT 갱신
cron-job.org가 사용하는 PAT는 1년 후 만료. 새 PAT 발급 후 cron-job.org 4개 cron job의 Authorization 헤더 업데이트.

### 모니터링 대상 변경
`src/naver_news.py`의 `TARGET_COMPANIES` 수정. 새 회사 추가 시 다음 4곳을 함께 갱신:
- `src/naver_news.py`의 `TARGET_COMPANIES`
- `src/kakao_sender.py`의 `_shorten_company` 매핑
- `src/email_sender.py`의 `_shorten_company` 매핑
- `src/telegram_sender.py`의 `_shorten_company` 매핑
- `src/ai_processor.py`의 `VALID_COMPANIES`
- `prompt/kih_daily_news_agent.md`의 §2

### 발송 시각 변경
두 곳을 동시에 수정:
1. cron-job.org의 4개 cron job 시각
2. `src/main.py`의 `SLOT_CONFIG`

일치하지 않으면 슬롯 매칭 실패 → manual 슬롯으로 처리.

### AI 모델 변경
환경변수 `GEMINI_MODEL`을 GitHub Variables에 등록하여 변경 가능:
- `gemini-3-flash-preview` (기본, 최신, 무료 한도 있음)
- `gemini-2.5-flash` (안정 버전)
- `gemini-2.5-flash-lite` (가장 빠르고 저렴)

Anthropic Claude로 되돌리려면 백업된 `ai_processor_anthropic_backup.py` 활용 (TROUBLESHOOTING.md 참조).

## Cost Estimate

| 항목 | 무료 한도 | 본 봇 사용량 | 비용 |
|---|---|---|---|
| Naver API | 일 25,000회 | 일 약 40회 | 무료 |
| **Gemini API** | **일 1,500 요청** | **일 약 5회** | **무료** |
| GitHub Actions | 월 2,000분 | 월 약 30분 | 무료 |
| Kakao API | 충분히 큰 한도 | 일 평균 ~30회 | 무료 |
| Gmail SMTP | 일 500건 | 일 약 4건 | 무료 |
| Telegram Bot | 분당 30건 | 일 약 4건 | 무료 |
| cron-job.org | 분당 1회 | 일 4회 | 무료 |
| **월 총비용** | | | **$0** |

## See Also

- [docs/INSTALL.md](../main/docs/install.md) - 설치 가이드 (처음 사용 시)
- [docs/ARCHITECTURE.md](../main/docs/architecture.md) - 시스템 구조 상세
- [docs/TROUBLESHOOTING.md](../main/docs/troubleshooting.md) - 문제 해결
