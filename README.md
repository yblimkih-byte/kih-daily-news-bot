# KIH Daily News Bot

한국투자금융그룹 11개 계열사 관련 뉴스를 하루 4회 카카오톡 '나와의 채팅방'으로 자동 전송하는 봇.

## Architecture

```
GitHub Actions (cron 4회/일, KST 07:40/09:30/14:00/17:30)
  │
  ▼
Python (src/main.py)
  ├── Naver News API (수집 윈도우: 슬롯별 동적, 1.5h~14.5h)
  ├── Claude API (필터·감성분석·요약·주체 회사 재분류)
  ├── Kakao Token Refresh (access_token 갱신)
  ├── Kakao Memo API ('나와의 채팅방' 발송)
  │     - 네이버 등재 기사: list 카드 (최대 5개씩)
  │     - 기타 매체 기사: text 메시지 (URL 본문 포함)
  └── 락 파일 (.last_send.json) 자동 commit으로 중복 발송 방지
```

## Required GitHub Secrets

| Name | Description |
|---|---|
| `NAVER_CLIENT_ID` | Naver Developers Client ID |
| `NAVER_CLIENT_SECRET` | Naver Developers Client Secret |
| `KAKAO_REST_API_KEY` | Kakao Developers REST API Key |
| `KAKAO_REFRESH_TOKEN` | Kakao OAuth refresh token (60일 유효) |
| `ANTHROPIC_API_KEY` | Anthropic Console API Key |

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

| 슬롯 | 시각 | 수집 윈도우 | 비고 |
|---|---|---|---|
| morning | 07:40 | 직전 17:30 ~ 현재 (14.5h) | 야간·새벽 누적 |
| pre_open | 09:30 | 직전 07:40 ~ 현재 (2h) | 장 시작 직전 |
| midday | 14:00 | 직전 09:30 ~ 현재 (4.7h) | 점심 후 |
| close | 17:30 | 직전 14:00 ~ 현재 (3.7h) | 장 마감 후 |

각 슬롯마다 cron 2개를 10분 간격으로 등록하여 GitHub Actions 누락에 대비. 락 파일이 같은 슬롯 중복 발송을 차단함.

### Friday Weekly Digest
금요일 07:40 슬롯에서 일일 발송 후 지난 7일 주간 종합을 추가 발송.

### Empty Window Behavior
- 수집 윈도우에 기사 0건 → 발송 생략 (메시지 없음)
- AI 필터 후 0건 → 발송 생략

### Manual Trigger
GitHub > Actions > "KIH Daily News Bot" > "Run workflow" → 수동 실행 시 4시간 윈도우로 동작.

## Maintenance

### 60일 카카오 토큰 갱신
`KAKAO_REFRESH_TOKEN`은 60일마다 갱신 필요. Kakao가 만료 1개월 이내일 때 새 토큰을 응답에 포함시키며, 로그에 `[WARNING] Kakao issued a new refresh_token` 출력됨.

### 모니터링 대상 변경
`src/naver_news.py`의 `TARGET_COMPANIES` 수정. 새 회사 추가 시 `src/kakao_sender.py`의 `_shorten_company` 매핑도 함께 갱신 + AI 프롬프트의 화이트리스트(`src/ai_processor.py`의 `VALID_COMPANIES`)에도 추가.

### AI 처리 기준 변경
`prompt/kih_daily_news_agent.md` 또는 `src/ai_processor.py`의 user_message 수정.

### AI 모델 변경
`src/ai_processor.py`의 `ANTHROPIC_MODEL` 상수 변경 (예: `claude-sonnet-4-5`로 격상하면 품질 ↑, 비용 ~5배).

## Cost Estimate

| 항목 | 무료 한도 | 본 봇 사용량 | 비용 |
|---|---|---|---|
| Naver API | 일 25,000회 | 일 약 40회 (4슬롯 × 11회사) | 무료 |
| Anthropic API (Haiku) | 사용량 과금 | 일 4회 AI 호출 | 월 약 USD 3-7 |
| GitHub Actions | 월 2,000분 | 월 약 30분 | 무료 |
| Kakao API | 충분히 큰 한도 | 일 평균 ~30회 발송 | 무료 |
