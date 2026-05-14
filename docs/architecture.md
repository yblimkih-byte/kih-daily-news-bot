# 시스템 구조 (Architecture)

본 봇의 내부 동작 원리와 데이터 흐름을 설명합니다.

## 전체 구성도

```
┌─────────────────┐
│  cron-job.org   │  (외부 cron 서비스, 무료)
│  4개 cron jobs  │  평일 07:40 / 09:10 / 13:30 / 17:00 KST
└────────┬────────┘
         │ HTTP POST + PAT 인증
         ▼
┌─────────────────────────────────────┐
│  GitHub API                         │
│  workflow_dispatch 엔드포인트       │
└────────┬────────────────────────────┘
         │ 워크플로우 트리거
         ▼
┌────────────────────────────────────────────────────────────┐
│  GitHub Actions Runner (Ubuntu)                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Python 3.11                                          │  │
│  │ src/main.py 실행                                     │  │
│  └─┬────────────────────────────────────────────────────┘  │
└────┼───────────────────────────────────────────────────────┘
     │
     │ ───────────── 수집 단계 ─────────────
     ├──→ Naver Search API           (뉴스 수집, 11개사 × 슬롯 윈도우)
     │    └─→ URL/제목 정규화 dedupe (회차 내 중복 제거)
     │
     │ ───────────── 회차 간 중복 차단 ─────────────
     ├──→ .sent_history.json 로드     (최근 3일 발송 이력)
     │    └─→ 이미 발송한 기사 제외
     │
     │ ───────────── AI 처리 (한 번만) ─────────────
     ├──→ Google Gemini API          (필터·감성·요약·재분류·이벤트 그룹화)
     │    └─→ event_group 기반 코드 추가 dedupe
     │
     │ ───────────── 발송 단계 (채널 디스패치) ─────────────
     ├──→ [if ENABLE_KAKAO]
     │    Kakao OAuth → Kakao Memo API
     │      ├─→ 네이버 기사: list 카드 (5건/메시지)
     │      └─→ 외부 매체: text 카드 (200자 묶음)
     │
     ├──→ [if ENABLE_EMAIL]
     │    SMTP (Gmail) → 다중 수신자
     │      └─→ Outlook 호환 HTML (table 기반)
     │
     └──→ [if ENABLE_TELEGRAM]
          Telegram Bot API → 1:1 또는 그룹/채널
            └─→ HTML 포맷 메시지 (4000자 자동 분할)
     │
     │ ───────────── 상태 저장 ─────────────
     ▼
┌─────────────────────────────────────┐
│  GitHub Repository (auto commit)    │
│  ├── .last_send.json   (락 파일)   │
│  └── .sent_history.json (3일 이력) │
└─────────────────────────────────────┘
```

## 실행 흐름 (단일 슬롯 기준)

### Step 1. cron-job.org 트리거

정해진 시각 (예: 07:40)에 cron-job.org가 GitHub API에 POST 요청.

```http
POST /repos/{user}/{repo}/actions/workflows/daily.yml/dispatches
Authorization: Bearer {PAT}

{"ref":"main"}
```

### Step 2. GitHub Actions 실행

`.github/workflows/daily.yml`이 실행되며:

1. Ubuntu Runner 시작
2. Python 3.11 환경 setup
3. `pip install -r requirements.txt` (google-genai, requests, json-repair, holidays)
4. `python src/main.py` 실행
5. 종료 후 `.last_send.json` + `.sent_history.json`을 저장소에 commit

### Step 3. 슬롯 결정 및 휴일 게이팅 (main.py)

`main.py`는 다음 순서로 동작:

1. **현재 KST 시각 측정**
2. **슬롯 매칭**: SLOT_CONFIG의 4개 슬롯 시각 (07:40 / 09:10 / 13:30 / 17:00)과 비교, ±15분 윈도우 안이면 해당 슬롯. 아니면 `manual` 슬롯 (4시간 윈도우).
3. **휴일 게이팅** (close 슬롯이 아닐 때만):
   - 주말 (토/일) 또는 한국 공휴일이면 **즉시 종료** (메시지 발송 없이)
   - close 슬롯은 휴일에도 발송 (하루 1번)
4. **금요일 + 주간종합 슬롯 결정**: 금요일 07:40 슬롯이면 주간 종합도 추가 발송. 단, 금요일이 공휴일이면 17:00 슬롯에서 주간 종합 발송.
5. **수집 윈도우 계산**: 슬롯 간격 (07:40 직전은 14.8h, 다른 슬롯은 1.6~4.5h)

### Step 4. 뉴스 수집 (naver_news.py)

11개 회사명으로 Naver News API 검색:

```python
TARGET_COMPANIES = [
    "한국투자금융지주", "한국투자증권", "한국투자신탁운용",
    "한국투자밸류자산운용", "한국투자파트너스", "한국투자프라이빗에쿼티",
    "한국투자캐피탈", "한국투자저축은행", "한국투자리얼에셋운용",
    "한국투자부동산신탁", "한국투자액셀러레이터",
]
```

회사당 약 10건 → 총 110건 → 시간 필터 후 약 30~90건.

**회차 내 중복 제거**:
- URL 정규화 (`sent_history.normalize_url`)로 같은 기사 인식
- 제목 정규화 (`sent_history.normalize_title`)로 같은 사건 인식

### Step 5. 회차 간 중복 차단 (sent_history.py)

`.sent_history.json` 로드 후 최근 3일 발송 기사들의 URL key + 제목 key를 set으로 변환.

수집된 기사 중 이 set에 있는 것 제외 → AI에 보낼 최종 후보 확정.

### Step 6. AI 처리 (ai_processor.py)

Google Gemini API에 다음 요청:

1. **system_instruction**: `prompt/kih_daily_news_agent.md` 전체 (약 3000자)
2. **user_message**: 기사 목록 + 작업 지시 (약 30000~50000자)
3. **config**: `response_mime_type="application/json"` (JSON 강제)

Gemini 응답:
```json
{
  "filtered": [
    {
      "idx": 12,
      "main_company": "한국투자증권",
      "sentiment": "negative",
      "summary": "한투증권 IMA 자금 해외 사모대출 투입 논란",
      "importance": 9,
      "event_group": "kis-ima-overseas-loan"
    }
  ]
}
```

**event_group 기반 추가 dedupe**: 같은 event_group 기사가 2개 이상이면 (is_naver, importance) 기준으로 최우선 1건만 채택.

### Step 7. 채널 디스패치

main.py가 enabled된 채널을 순회하며 발송:

```python
sent = {}
if ENABLE_KAKAO and items:
    try:
        sent["kakao"] = kakao_sender.send_daily_news(...)
    except Exception as e:
        print(f"[ERROR] Kakao failed: {e}")
        sent["kakao"] = 0  # 한 채널 실패해도 나머지 진행
if ENABLE_EMAIL and items:
    try:
        sent["email"] = email_sender.send_daily_news(...)
    except Exception as e:
        ...
```

각 채널은 try/except로 격리되어 한 채널이 실패해도 다른 채널은 정상 동작.

#### Kakao 발송 (kakao_sender.py)

- **네이버 기사** (is_naver=true): list 카드 (제목+요약+버튼 5건씩 묶음)
- **외부 매체** (is_naver=false): text 카드 (이모지+태그+제목 25자+URL)
  - 200자 한도 안에서 여러 entry를 greedy 묶음

#### Email 발송 (email_sender.py)

- 모든 기사를 한 HTML로 빌드 (table 기반, Outlook 호환)
- SMTP (Gmail STARTTLS 587)로 발송
- `EMAIL_DELIVERY_MODE` 환경변수로 `to` / `bcc` / `individual` 선택

#### Telegram 발송 (telegram_sender.py)

- HTML 포맷 메시지 (4000자 자동 분할)
- 각 chat_id로 순차 발송 (rate limit 30/분 안)

### Step 8. 발송 후 상태 저장

성공 발송이 있으면:

1. **락 파일 갱신** (`.last_send.json`): `{슬롯}.last_sent` = 현재 KST timestamp
2. **이력 추가** (`.sent_history.json`): 발송한 모든 기사의 URL key + 제목 key + 발송 시각 추가
3. **3일 초과 이력 자동 정리**
4. GitHub Actions가 두 파일을 자동 commit + push

## 데이터 흐름 상세

### 락 파일 (`.last_send.json`)

```json
{
  "morning":   {"last_sent": "2026-05-12T07:42:31+09:00"},
  "pre_open":  {"last_sent": "2026-05-12T09:11:05+09:00"},
  "midday":    {"last_sent": "2026-05-12T13:31:22+09:00"},
  "close":     {"last_sent": "2026-05-12T17:00:48+09:00"}
}
```

같은 슬롯이 30분 안에 두 번 트리거되면 두 번째는 즉시 종료 (중복 발송 방지).

### 발송 이력 (`.sent_history.json`)

```json
{
  "entries": [
    {
      "url_key": "n.news.naver.com/mnews/article/123/0001",
      "title_key": "한국투자증권 ima 자금 해외 사모대출 투입 논란",
      "sent_at": "2026-05-12T07:42:31+09:00"
    }
  ],
  "retention_days": 3
}
```

새 슬롯에서 수집한 기사의 정규화 key가 이 set에 있으면 발송 안 함.

### URL 정규화 규칙 (sent_history.py)

같은 기사가 다른 URL로 보일 때 중복으로 인식:

| 원본 | 정규화 후 |
|---|---|
| `https://n.news.naver.com/mnews/article/123/0001?inflow=mt` | `n.news.naver.com/mnews/article/123/0001` |
| `https://m.news.naver.com/mnews/article/123/0001` | `n.news.naver.com/mnews/article/123/0001` |
| `https://www.example.com/article/9999?utm_source=naver` | `example.com/article/9999` |
| `http://example.com/article/9999` | `example.com/article/9999` |

### 제목 정규화 규칙

같은 사건이 다른 제목으로 보일 때 중복 인식:

| 원본 | 정규화 후 |
|---|---|
| `[속보] 한국투자증권, IMA 자금 논란` | `한국투자증권 ima 자금 논란` |
| `"한국투자증권 IMA 자금 논란"` | `한국투자증권 ima 자금 논란` |
| `한국투자증권 IMA 자금 논란…한투 도마위` | `한국투자증권 ima 자금 논란 한투 도마위` |

## 채널별 메시지 빌드 흐름

### 카카오 외부 매체 묶기

200자 한도 안에서 greedy 묶기:

1. 각 기사를 `🔴[증권] 제목25자\nURL` 형태 entry로 변환
2. 헤더 reserve (22자) 빼고 남은 178자 안에서 entry들을 greedy 추가
3. 200자 초과 직전에 새 메시지로 분리
4. 평균 1.5~1.7건/메시지로 묶임 (URL 짧으면 2건, 길면 1건)

### 이메일 Outlook 호환

```html
<!-- table 기반 (Outlook이 div를 잘 못 그림) -->
<table cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="#fafafa">
  <tr>
    <td width="4" bgcolor="#d93025"></td>  <!-- 4px 색깔 막대 -->
    <td style="padding:10px;font-family:'맑은 고딕';">
      <span style="color:#d93025;">●</span>  <!-- 이모지 대신 색깔 점 -->
      <span style="color:#d93025;font-weight:bold;">[증권]</span>
      <a href="...">제목</a>
    </td>
  </tr>
</table>
```

원칙:
- div → table (Outlook 호환)
- 🔴🟡🟢 → ● + color
- border-radius 제거
- '맑은 고딕' 폰트 우선

### 텔레그램 HTML

```
📅 05-12(화) 07:40 🔴2 🟡5 🟢1

🔴 [증권] <a href="...">한투증권, IMA 자금 논란</a>
한투증권 IMA 자금 해외 사모대출 투입 논란
```

4000자 초과 시 자동 분할.

## 비용 최적화 포인트

### 1. AI 호출 1회로 모든 채널 발송 (가장 큼)

다음 흐름이 핵심:

```
수집 → AI 처리 (1번, $$$) → 결과 활용 → 카톡 + 이메일 + 텔레그램 동시 발송
```

각 채널마다 AI를 다시 호출하지 않음. AI 비용을 채널 수만큼 곱하지 않음.

### 2. Gemini Flash 무료 한도

Gemini 3 Flash 무료 한도: **일 1,500 요청**.

본 봇 사용량:
- 평일 4회 + 금요일 주간 종합 1회 = 일 4~5회
- 무료 한도의 0.3%만 사용

→ **사실상 $0 운영**

### 3. JSON 응답 강제로 토큰 절감

`response_mime_type="application/json"`로 AI가 markdown 펜스나 설명문 없이 순수 JSON만 반환. 출력 토큰 약 10~15% 절감.

### 4. preselect로 입력 토큰 제한

회사별 수집 결과를 90건으로 캡 (`MAX_ARTICLES_PER_CALL = 90`). 폭증 시에도 토큰 비용 고정.

### 5. 회차 간 중복 차단으로 불필요한 AI 호출 제거

3일 이력에 있는 기사는 AI에 보내기 전에 제외. AI 처리 대상이 평균 20~30% 감소.

## 보안 모델

| 자산 | 노출 위험 | 보호 방법 |
|---|---|---|
| Gemini API Key | 본인 Google 계정 quota 소진 | GitHub Secrets (암호화 저장) |
| Naver API Key | 본인 Naver 계정 quota 소진 | GitHub Secrets |
| Kakao Refresh Token | 본인 카톡 '나와의 채팅방' 발송 가능 | GitHub Secrets |
| GitHub PAT | 본인 저장소 workflow 트리거 | cron-job.org HTTPS 전송 |
| Gmail App Password | 본인 Gmail로 발신 | GitHub Secrets |
| Telegram Bot Token | 본인 봇으로 메시지 발송 | GitHub Secrets |

GitHub Secrets는 워크플로우 실행 중에만 환경변수로 노출되며 로그에 기록되지 않음.

## 자주 묻는 구조적 질문

**Q. 왜 AI 처리는 한 번만 하는가?**
A. 비용 최적화. 채널마다 AI를 호출하면 비용이 N배가 됨. 한 번 처리해서 결과를 공유.

**Q. 왜 try/except로 채널을 격리하는가?**
A. 한 채널 (예: 카카오 토큰 만료)이 실패해도 다른 채널 발송은 정상 동작. 운영 견고성.

**Q. 왜 락 파일을 GitHub에 commit하는가?**
A. GitHub Actions 환경은 매번 새 Ubuntu 컨테이너. 로컬 파일은 실행 후 사라짐. 저장소 commit이 유일한 영구 저장소.

**Q. 왜 cron-job.org를 외부 cron으로 쓰는가?**
A. GitHub Actions의 자체 schedule trigger는 5~15분의 지연이 발생. cron-job.org는 분 단위 정확도.

**Q. .sent_history.json은 왜 3일만 보존하는가?**
A. 너무 길면 같은 사건이 1주일 뒤에 후속 보도되어도 무시됨 (오탐). 너무 짧으면 회차 간 중복 제거가 약함. 3일이 균형점.

## 향후 확장 가능성

다음은 코드 수정으로 추가 가능한 기능:

- Slack 발송 채널 추가 (`src/slack_sender.py` 신규)
- Discord 발송 채널 추가
- Microsoft Teams 발송 채널
- 회사별 별도 필터링 (예: 특정 회사만 받기)
- 키워드 필터 (예: 부정 기사만 받기)
- 다른 회사 그룹으로 응용 (TARGET_COMPANIES 교체)
- 외국어 뉴스 (Naver 대신 Bing News, NewsAPI)

각 확장은 main.py의 채널 디스패치 구조를 그대로 활용 가능.

## See Also

- [INSTALL.md](INSTALL.md) - 설치 가이드
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - 문제 해결
- [project/README.md](../project/README.md) - 운영 가이드
