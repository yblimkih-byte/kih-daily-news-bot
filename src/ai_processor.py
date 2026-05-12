"""Claude AI processor for news filtering, sentiment analysis, and summarization."""
import json
import os
import re
from pathlib import Path
from anthropic import Anthropic

try:
    from json_repair import repair_json
    HAS_REPAIR = True
except ImportError:
    HAS_REPAIR = False


ANTHROPIC_MODEL = "claude-haiku-4-5"

MAX_TOKENS = 16000

# 입력 기사 한도. 네이버 + 외부 모두 합쳐서. 외부 매체 정상 통과 위해 확대.
MAX_ARTICLES_PER_CALL = 90

# 외부 매체 기사를 입력에 보장할 최소 비중 (기사가 충분히 많을 때)
MIN_EXTERNAL_RATIO = 0.5

PROMPT_PATH = Path(__file__).parent.parent / "prompt" / "kih_daily_news_agent.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _preselect_articles(articles: list[dict], max_n: int) -> list[dict]:
    """Cap input to AI. Keep all Naver, then ensure external gets a fair share."""
    if len(articles) <= max_n:
        return [_trim_article(a) for a in articles]

    naver = [a for a in articles if a.get("is_naver")]
    external = [a for a in articles if not a.get("is_naver")]

    # Reserve at least half of slots for external (if there's that many external)
    min_external_slots = int(max_n * MIN_EXTERNAL_RATIO)
    external_take = min(len(external), max(min_external_slots, max_n - len(naver)))
    naver_take = min(len(naver), max_n - external_take)
    # If naver is small, give the rest to external
    if naver_take < max_n - external_take and len(external) > external_take:
        external_take = min(len(external), max_n - naver_take)

    selected = naver[:naver_take] + external[:external_take]
    print(
        f"[INFO] Preselected {len(selected)}/{len(articles)} articles for AI "
        f"(naver_in={naver_take}/{len(naver)}, external_in={external_take}/{len(external)})",
        flush=True,
    )
    return [_trim_article(a) for a in selected]


def _trim_article(a: dict) -> dict:
    desc = a.get("description", "")
    if len(desc) > 180:
        desc = desc[:180] + "..."
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

    Strategy:
    1. Strip markdown fences
    2. Strict json.loads
    3. json-repair (auto-fixes unescaped quotes, trailing commas, etc.)
    4. Manual truncation recovery
    """
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)

    # 1. Strict parse
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[WARN] Strict JSON parse failed: {e}", flush=True)

    # 2. json-repair (handles unescaped quotes, missing commas, etc.)
    if HAS_REPAIR:
        try:
            repaired = repair_json(text)
            result = json.loads(repaired)
            print("[INFO] JSON recovered by json-repair.", flush=True)
            return result
        except Exception as e:
            print(f"[WARN] json-repair failed: {e}", flush=True)

    # 3. Manual truncation recovery (for cut-off responses)
    if '"filtered"' in text:
        for closing in ("},", "}\n"):
            last_pos = text.rfind(closing)
            if last_pos != -1:
                candidate = text[: last_pos + 1] + "]}"
                try:
                    result = json.loads(candidate)
                    print(f"[INFO] JSON recovered by truncation at '{closing}'.", flush=True)
                    return result
                except json.JSONDecodeError:
                    continue
    return None


def process_daily_news(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    selected = _preselect_articles(articles, MAX_ARTICLES_PER_CALL)

    # Each article gets a stable index. AI returns only the index; we re-attach
    # the original link ourselves to eliminate URL corruption by the model.
    indexed_articles = []
    for i, a in enumerate(selected):
        indexed_articles.append({"idx": i, **a})

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = (
        "아래는 직전 24시간 동안 수집된 한국투자금융그룹 관련 기사 목록입니다.\n"
        "각 기사는 'idx' 번호가 부여되어 있습니다.\n"
        "시스템 프롬프트의 §4 (수집·선별 규칙), §5 (감성 분석) 기준을 적용하세요.\n\n"
        f"기사 목록 ({len(indexed_articles)}건, JSON):\n"
        f"{json.dumps(indexed_articles, ensure_ascii=False, indent=2)}\n\n"
        "다음 JSON 형식으로만 응답. 다른 텍스트 절대 금지:\n"
        "{\n"
        '  "filtered": [\n'
        "    {\n"
        '      "idx": 기사의 idx 번호 (정수),\n'
        '      "sentiment": "positive | neutral | negative",\n'
        '      "summary": "40자 이내 한 줄 요약 (주술 구조)",\n'
        '      "importance": 1-10\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "중요: title과 link 필드는 응답에 포함하지 말 것 (idx로 식별).\n\n"
        "필터링 원칙 (엄격히 준수):\n"
        "1. 기사 출처(네이버 직접 등재 여부)는 필터링 기준이 아님. 외부 매체(매일경제·한국경제·조선비즈 등) 기사도 동등하게 평가할 것.\n"
        "2. 다음 기사는 무조건 포함: 부정 감성, 글로벌 금융기업 MOU, 금융제도·규제 변경, 임원 인사, 분기 실적, 자회사 변동, 신규 펀드 설정/청산, M&A.\n"
        "3. 다음 기사는 무조건 제외 (중요): 한국투자증권 리서치센터 연구원의 종목 분석·목표주가 제시/조정·투자의견 변경 기사. 예: '한국투자증권은 ◯◯기업 목표가 상향', '한투증권 ◯◯연구원 매수 의견'. 이는 그룹사 자체 활동이 아닌 단순 리서치 인용임.\n"
        "4. 제외 대상은 '신상품 단순 홍보', '시황 종목 추천 글에 회사명 단순 언급', '리서치 리포트 인용'에 한정. 보도 가치가 조금이라도 있으면 포함.\n"
        "5. 동일 보도자료 기반 중복 기사는 1건만 채택. 단, 매체별 시각 차이가 명확하면 별도 채택.\n"
        "6. summary는 반드시 40자 이내, 군더더기 없이 간결하게.\n"
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
        chunk_size = 2000
        for i in range(0, len(text), chunk_size):
            print(f"[ERROR_DUMP {i}-{i+chunk_size}] {text[i:i+chunk_size]}", flush=True)
        return []

    ai_items = result.get("filtered", [])

    # Re-attach original data by idx (no URL corruption possible)
    enriched = []
    for ai_item in ai_items:
        idx = ai_item.get("idx")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(selected):
            print(f"[WARN] Invalid idx in AI response: {idx}", flush=True)
            continue
        src = selected[idx]
        enriched.append({
            "company": src.get("company"),
            "title": src.get("title"),
            "link": src.get("link"),
            "is_naver": src.get("is_naver", False),
            "media": src.get("media", ""),
            "sentiment": ai_item.get("sentiment", "neutral"),
            "summary": ai_item.get("summary", ""),
            "importance": ai_item.get("importance", 5),
        })

    n_naver = sum(1 for it in enriched if it.get("is_naver"))
    n_ext = sum(1 for it in enriched if not it.get("is_naver"))
    print(f"[INFO] AI filter result: total={len(enriched)} (naver={n_naver}, external={n_ext})", flush=True)

    enriched.sort(key=lambda x: (
        0 if x.get("sentiment") == "negative"
        else (1 if x.get("sentiment") == "neutral" else 2),
        -x.get("importance", 0),
    ))
    return enriched


def process_weekly_digest(articles: list[dict]) -> dict:
    if not articles:
        return {"by_company": {}, "keywords": []}

    selected = _preselect_articles(articles, MAX_ARTICLES_PER_CALL * 2)
    indexed_articles = [{"idx": i, **a} for i, a in enumerate(selected)]

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = _load_prompt()

    user_message = (
        "아래는 지난 7일간 수집된 한국투자금융그룹 관련 기사 목록입니다.\n"
        "각 기사는 'idx' 번호가 부여되어 있습니다.\n"
        "시스템 프롬프트의 §7 (금요일 주간 종합) 기준으로 주간 요약을 생성하세요.\n\n"
        f"기사 목록 ({len(indexed_articles)}건, JSON):\n"
        f"{json.dumps(indexed_articles, ensure_ascii=False, indent=2)}\n\n"
        "다음 JSON 형식으로만 응답 (다른 텍스트 절대 금지):\n"
        "{\n"
        '  "by_company": {\n'
        '    "회사명": [\n'
        '      {"idx": 기사의 idx (정수), "summary": "한 줄 요약"}\n'
        "    ]\n"
        "  },\n"
        '  "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"]\n'
        "}\n\n"
        "중요: link 필드는 응답에 포함하지 말 것 (idx로 식별).\n"
        "회사당 최대 3건. 이슈 없는 회사는 by_company에서 제외.\n"
        "외부 매체 기사도 네이버 기사와 동등하게 평가할 것.\n"
        "한국투자증권 리서치센터 연구원의 종목 분석·목표주가 기사는 제외.\n"
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

    # Re-attach link by idx
    by_company = result.get("by_company", {})
    for company, items in by_company.items():
        for item in items:
            idx = item.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(selected):
                item["link"] = selected[idx].get("link", "")
            else:
                item["link"] = ""
    return {"by_company": by_company, "keywords": result.get("keywords", [])}
