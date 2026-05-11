"""Claude AI processor for news filtering, sentiment analysis, and summarization."""
import json
import os
import re
from pathlib import Path
from anthropic import Anthropic


ANTHROPIC_MODEL = "claude-haiku-4-5"  # 더 좋은 품질 원하면 "claude-sonnet-4-5"

# Claude 응답 토큰 한도 (16k까지 안전)
MAX_TOKENS = 16000

# Claude에게 한 번에 보낼 최대 기사 수 (응답 토큰 한도 고려)
MAX_ARTICLES_PER_CALL = 60

PROMPT_PATH = Path(__file__).parent.parent / "prompt" / "kih_daily_news_agent.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _preselect_articles(articles: list[dict], max_n: int) -> list[dict]:
    """Cap input articles for Claude. Priority:
       1. Naver-hosted (is_naver=True) first
       2. Within each group, newest first (already sorted by Naver API)
       3. Truncate description to 200 chars to save tokens
    """
    if len(articles) <= max_n:
        return [_trim_article(a) for a in articles]

    naver = [a for a in articles if a.get("is_naver")]
    external = [a for a in articles if not a.get("is_naver")]

    # Take all Naver first, fill remainder with external
    selected = naver[: max_n]
    remaining = max_n - len(selected)
    if remaining > 0:
        selected += external[: remaining]

    print(
        f"[INFO] Preselected {len(selected)}/{len(articles)} articles for AI "
        f"(naver_in={min(len(naver), max_n)}, external_in={max(0, len(selected) - len(naver))})",
        flush=True,
    )
    return [_trim_article(a) for a in selected]


def _trim_article(a: dict) -> dict:
    """Reduce per-article token footprint."""
    desc = a.get("description", "")
    if len(desc) > 200:
        desc = desc[:200] + "..."
    return {
        "company": a.get("company"),
        "title": a.get("title"),
        "description": desc,
        "link": a.get("link"),
        "is_naver": a.get("is_naver"),
        "media": a.get("media"),
        "pub_date": a.get("pub_date"),
    }


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from Claude's response.

    Handles markdown fences, leading/trailing prose, and recovers from truncated
    JSON by finding the last complete object inside `filtered`.
    """
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)

    # First try: parse as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Recovery: truncated `filtered` array. Find last `},` and close array.
    if '"filtered"' in text:
        last_brace = text.rfind("},")
        if last_brace != -1:
            candidate = text[: last_brace + 1] + "]}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        # Try closing one more object if it's mid-object
        last_complete = text.rfind('}\n')
        if last_complete != -1:
            candidate = text[: last_complete + 1] + "]}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    return None


def process_daily_news(articles: list[dict]) -> list[dict]:
    """Filter, sentiment-analyze, and summarize for daily delivery."""
    if not articles:
        return []

    selected = _preselect_articles(articles, MAX_ARTICLES_PER_CALL)

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = (
        "아래는 직전 24시간 동안 수집된 한국투자금융그룹 관련 기사 목록입니다.\n"
        "시스템 프롬프트의 §4 (수집·선별 규칙), §5 (감성 분석) 기준을 적용하세요.\n\n"
        f"기사 목록 ({len(selected)}건, JSON):\n"
        f"{json.dumps(selected, ensure_ascii=False, indent=2)}\n\n"
        "다음 JSON 형식으로만 응답하세요. 다른 설명·머리말·꼬리말 없이 JSON만 출력:\n"
        "{\n"
        '  "filtered": [\n'
        "    {\n"
        '      "company": "회사명",\n'
        '      "title": "원문 제목 그대로",\n'
        '      "link": "원문 link 그대로 (반드시 입력 데이터의 link 필드 그대로 복사)",\n'
        '      "sentiment": "positive | neutral | negative",\n'
        '      "summary": "40자 이내 한 줄 요약 (주술 구조)",\n'
        '      "importance": 1-10\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "규칙:\n"
        "- 단순 홍보 기사는 제외. 단, 글로벌 금융기업 MOU/금융제도 관련이면 포함.\n"
        "- 부정 감성 기사는 무조건 포함.\n"
        "- 동일 보도자료 기반 중복 기사는 1건만 채택.\n"
        "- summary는 반드시 40자 이내, 군더더기 없이 간결하게.\n"
        "- link 필드는 입력 데이터의 link를 그대로 복사 (수정·재구성 금지).\n"
    )

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text
    result = _extract_json(text)
    if result is None:
        print(f"[ERROR] Failed to parse Claude response. Length={len(text)}", flush=True)
        print(f"[ERROR] First 500 chars: {text[:500]}", flush=True)
        print(f"[ERROR] Last 200 chars: {text[-200:]}", flush=True)
        return []

    filtered = result.get("filtered", [])

    # Re-attach is_naver / media from input by matching link
    link_to_input = {a["link"]: a for a in selected if a.get("link")}
    for item in filtered:
        src = link_to_input.get(item.get("link"))
        if src:
            item.setdefault("is_naver", src.get("is_naver", False))
            item.setdefault("media", src.get("media", ""))

    # Sort: negative -> neutral -> positive; within group by importance desc
    filtered.sort(key=lambda x: (
        0 if x.get("sentiment") == "negative"
        else (1 if x.get("sentiment") == "neutral" else 2),
        -x.get("importance", 0),
    ))

    print(f"[INFO] AI returned {len(filtered)} items after filter.", flush=True)
    return filtered


def process_weekly_digest(articles: list[dict]) -> dict:
    """Friday weekly digest (no Kakao card constraints, prose output)."""
    if not articles:
        return {"by_company": {}, "keywords": []}

    selected = _preselect_articles(articles, MAX_ARTICLES_PER_CALL * 2)  # 주간이라 더 많이

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = (
        "아래는 지난 7일간 수집된 한국투자금융그룹 관련 기사 목록입니다.\n"
        "시스템 프롬프트의 §7 (금요일 주간 종합) 기준으로 주간 요약을 생성하세요.\n\n"
        f"기사 목록 ({len(selected)}건, JSON):\n"
        f"{json.dumps(selected, ensure_ascii=False, indent=2)}\n\n"
        "다음 JSON 형식으로만 응답 (다른 텍스트 일체 금지):\n"
        "{\n"
        '  "by_company": {\n'
        '    "회사명": [\n'
        '      {"summary": "한 줄 요약", "link": "입력 link 그대로"}\n'
        "    ]\n"
        "  },\n"
        '  "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"]\n'
        "}\n\n"
        "회사당 최대 3건. 이슈 없는 회사는 by_company에서 제외.\n"
    )

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text
    result = _extract_json(text)
    if result is None:
        print("[ERROR] Failed to parse weekly digest response.", flush=True)
        return {"by_company": {}, "keywords": []}
    return result
