"""Claude AI processor for news filtering, sentiment analysis, and summarization."""
import json
import os
from pathlib import Path
from anthropic import Anthropic


ANTHROPIC_MODEL = "claude-haiku-4-5"  # 비용 최적화. 품질 더 원하면 claude-sonnet-4-5

PROMPT_PATH = Path(__file__).parent.parent / "prompt" / "kih_daily_news_agent.md"


def _load_prompt() -> str:
    """Load the system prompt from the .md file."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def process_daily_news(articles: list[dict]) -> list[dict]:
    """Filter, sentiment-analyze, and summarize articles for daily delivery.

    Returns:
        list[dict]: Each item has:
            - company: str
            - title: str
            - link: str
            - sentiment: "positive" | "neutral" | "negative"
            - summary: str (한 줄 요약, 40자 내외)
            - importance: int (1-10, 부정 기사일수록 높게)
    """
    if not articles:
        return []

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = f"""아래는 직전 24시간 동안 수집된 한국투자금융그룹 관련 기사 목록입니다.
시스템 프롬프트의 §4 (수집·선별 규칙), §5 (감성 분석) 기준을 적용하여 처리하세요.

기사 목록 (JSON):
{json.dumps(articles, ensure_ascii=False, indent=2)}

다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력:
{{
  "filtered": [
    {{
      "company": "회사명",
      "title": "원문 제목 그대로",
      "link": "원문 link 그대로",
      "sentiment": "positive | neutral | negative",
      "summary": "40자 이내 한 줄 요약 (주술 구조)",
      "importance": 1-10 (부정·실적·규제·인사 관련일수록 높게)
    }}
  ]
}}

규칙:
- 단순 홍보 기사는 제외 (단, 글로벌 금융기업 MOU/금융제도 관련이면 포함)
- 부정 감성 기사는 무조건 포함
- 동일 보도자료 기반 중복 기사는 1건만 채택 (link is_naver=true 우선)
- importance 정렬은 신경쓰지 말 것 (호출자가 정렬)
"""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()
    # Remove markdown code fence if present
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse Claude response: {e}", flush=True)
        print(f"[ERROR] Raw response: {text[:500]}", flush=True)
        return []

    filtered = result.get("filtered", [])
    # Sort: negative first, then by importance desc
    filtered.sort(key=lambda x: (
        0 if x.get("sentiment") == "negative" else (1 if x.get("sentiment") == "neutral" else 2),
        -x.get("importance", 0),
    ))
    return filtered


def process_weekly_digest(articles: list[dict]) -> dict:
    """Generate weekly digest for Friday delivery.

    Returns:
        dict: {
            "by_company": {company_name: [items]},
            "keywords": [str, ...],
        }
    """
    if not articles:
        return {"by_company": {}, "keywords": []}

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = f"""아래는 지난 7일간 수집된 한국투자금융그룹 관련 기사 목록입니다.
시스템 프롬프트의 §7 (금요일 주간 종합) 기준으로 주간 요약을 생성하세요.

기사 목록 (JSON):
{json.dumps(articles, ensure_ascii=False, indent=2)}

다음 JSON 형식으로만 응답:
{{
  "by_company": {{
    "회사명": [
      {{"summary": "한 줄 요약", "link": "URL"}}
    ]
  }},
  "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"]
}}

판단 기준 (시스템 프롬프트 §7.2):
- 그룹 손익에 직접 영향
- 규제·감독 이슈
- 대표·임원·핵심 본부장급 인사
- 시장 점유율·랭킹 변동
- 신규 사업·M&A
- 경쟁사 대비 차별적 움직임

회사당 최대 3건. 이슈가 없는 회사는 키에서 제외.
"""

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse weekly digest response: {e}", flush=True)
        return {"by_company": {}, "keywords": []}
