# KIH Daily News Bot

금융그룹 10개 계열사 관련 뉴스를 매일 07:40 KST에 카카오톡 '나와의 채팅방'으로 자동 전송하는 봇.

## Architecture

```
GitHub Actions (cron 07:40 KST)
  │
  ▼
Python (src/main.py)
  ├── Naver News API (24h 기사 수집)
  ├── Claude API (필터·감성분석·요약)
  ├── Kakao Token Refresh (access_token 갱신)
  └── Kakao Memo API ('나와의 채팅방' 발송)
```

## Required GitHub Secrets

| Name | Description |
|---|---|
| `NAVER_CLIENT_ID` | Naver Developers Client ID |
| `NAVER_CLIENT_SECRET` | Naver Developers Client Secret |
| `KAKAO_REST_API_KEY` | Kakao Developers REST API Key |
| `KAKAO_REFRESH_TOKEN` | Kakao OAuth refresh token (60일 유효) |
| `ANTHROPIC_API_KEY` | Anthropic Console API Key |

## Operation

- **자동 실행**: 매일 07:40 KST
- **금요일**: 일일 + 지난 7일 주간 종합
- **수동 실행**: GitHub > Actions 탭 > "KIH Daily News Bot" > "Run workflow" 버튼

## Maintenance

- `KAKAO_REFRESH_TOKEN`은 60일마다 갱신 필요. Kakao가 만료 1개월 이내일 때 새 토큰을 응답에 포함시키며, 로그에 `[WARNING] Kakao issued a new refresh_token` 출력됨.
- 모니터링 대상 회사 변경: `src/naver_news.py`의 `TARGET_COMPANIES` 수정.
- AI 처리 기준 변경: `prompt/kih_daily_news_agent.md` 수정.
- 모델 변경: `src/ai_processor.py`의 `ANTHROPIC_MODEL` 상수 변경 (예: `claude-sonnet-4-5`로 격상하면 품질 ↑ 비용 ↑).

## Cost Estimate

- Naver API: 무료 (일 25,000회 한도, 본 봇 사용량 일 ~20회)
- Anthropic API (Haiku): 월 약 USD 1-3
- GitHub Actions: 무료 (월 2,000분, 본 봇 사용량 월 ~5분)
- Kakao API: 무료
