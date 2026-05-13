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
        '      "main_company": "기사의 실질 주체 회사명 (아래 목록 중 하나만)",\n'
        '      "sentiment": "positive | neutral | negative",\n'
        '      "summary": "40자 이내 한 줄 요약 (주술 구조)",\n'
        '      "importance": 1-10,\n'
        '      "event_group": "이 기사가 다루는 실제 사건/사안의 고유 식별자 (아래 규칙 준수)"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "main_company는 반드시 다음 11개 중 하나의 정확한 회사명으로 응답:\n"
        "- 한국투자금융지주\n"
        "- 한국투자증권\n"
        "- 한국투자신탁운용\n"
        "- 한국투자밸류자산운용\n"
        "- 한국투자파트너스\n"
        "- 한국투자프라이빗에쿼티\n"
        "- 한국투자캐피탈\n"
        "- 한국투자저축은행\n"
        "- 한국투자리얼에셋운용\n"
        "- 한국투자부동산신탁\n"
        "- 한국투자액셀러레이터\n\n"
        "main_company 판정 원칙 (중요):\n"
        "- 기사 본문에서 '실질적 주체'로 다뤄지는 회사 1개를 선택.\n"
        "- 예: 기사 제목·본문이 '한국투자금융지주 자회사 한국투자증권은 ...' 또는 \n"
        "  '한국투자금융지주의 한국투자증권이 ...'와 같이 자회사 활동을 다룰 때는 자회사명(이 경우 '한국투자증권')을 선택.\n"
        "- 지주회사 자체의 결정·실적·인사·자본 변동 등이 주된 내용일 때만 '한국투자금융지주' 선택.\n"
        "- 검색에 사용된 회사명(입력 데이터의 company 필드)과 main_company가 다를 수 있음. 본문 기준으로 재판단.\n\n"
        "필터링 원칙 (엄격히 준수):\n"
        "1. 기사 출처(네이버 직접 등재 여부)는 필터링 기준이 아님. 외부 매체(매일경제·한국경제·조선비즈 등) 기사도 동등하게 평가할 것.\n"
        "2. 다음 기사는 무조건 포함: 부정 감성, 글로벌 금융기업 MOU, 금융제도·규제 변경, 임원 인사, 분기 실적, 자회사 변동, 신규 펀드 설정/청산, M&A.\n"
        "3. 다음 기사는 무조건 제외 (중요): 한국투자증권 리서치센터 연구원의 종목 분석·목표주가 제시/조정·투자의견 변경 기사. 예: '한국투자증권은 ◯◯기업 목표가 상향', '한투증권 ◯◯연구원 매수 의견'. 이는 그룹사 자체 활동이 아닌 단순 리서치 인용임.\n"
        "4. 제외 대상은 '신상품 단순 홍보', '시황 종목 추천 글에 회사명 단순 언급', '리서치 리포트 인용'에 한정. 보도 가치가 조금이라도 있으면 포함.\n"
        "5. summary는 반드시 40자 이내, 군더더기 없이 간결하게.\n\n"
        "event_group 규칙 (가장 중요, 중복 발송 방지):\n"
        "- 모든 입력 기사를 검토하여, **실질적으로 같은 사건/사안을 다룬 기사들을 동일 event_group으로 묶기**.\n"
        "- event_group은 사건을 식별하는 짧은 영문 슬러그 (예: 'kis-pe-network-event', 'ace-space-etf-2000bn', 'kis-q1-earnings-2026').\n"
        "- 같은 사건의 기준: 같은 회사가 같은 시점에 같은 행위를 했거나 같은 사건이 발생한 것. 표현·매체·관점이 달라도 사실의 핵심이 같으면 같은 사건.\n"
        "  - 예시 1: '한국투자증권, CEO 네트워크 행사 개최' + '한국투자증권, 사모운용사·자문사와 WM 협업 강화' → 같은 행사를 다룬 것이면 동일 event_group.\n"
        "  - 예시 2: 'ACE 美 우주테크 ETF 순자산 2000억 돌파' + 'ACE 미국우주테크액티브 ETF 순자산 2000억원 돌파' → 동일 펀드의 동일 마일스톤이므로 동일 event_group.\n"
        "  - 예시 3: 같은 임원 선임을 매체 5곳이 보도 → 모두 동일 event_group.\n"
        "- 다른 사건의 기준: 같은 회사라도 다른 시점·다른 행위·다른 주제면 다른 event_group.\n"
        "  - 예: '한투증권 1분기 실적' vs '한투증권 신규 ETF 출시' → 별개 event_group.\n"
        "- 한 event_group에 속한 기사 중 filtered 배열에는 **단 1건만 포함**시킬 것. 가장 정보량이 많고, 가장 신뢰도 높은 매체의 기사를 선택.\n"
        "  매체 신뢰도 우선순위: 매일경제·한국경제·조선비즈·서울경제·머니투데이 > 그 외 종합지 > 업종 전문지 > 군소 매체.\n"
        "  네이버 등재(is_naver=true) 기사가 후보에 있으면 동등 신뢰도 매체 중에서 네이버 기사 우선.\n"
        "- 단독 사건은 자체적으로 1개의 event_group (예: 'kis-ipo-deal-bgen-2027').\n"
        "- event_group 이름은 영문 소문자 + 하이픈만 사용, 30자 이내.\n"
        "- 동일 event_group이 결과 배열에 2건 이상 들어가지 않도록 응답 전 반드시 자체 검증.\n"
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

    # Whitelist of valid main_company values
    VALID_COMPANIES = {
        "한국투자금융지주", "한국투자증권", "한국투자신탁운용", "한국투자밸류자산운용",
        "한국투자파트너스", "한국투자프라이빗에쿼티", "한국투자캐피탈", "한국투자저축은행",
        "한국투자리얼에셋운용", "한국투자부동산신탁", "한국투자액셀러레이터",
    }

    # Re-attach original data by idx; use main_company from AI for tagging
    enriched = []
    reclass_count = 0
    for ai_item in ai_items:
        idx = ai_item.get("idx")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(selected):
            print(f"[WARN] Invalid idx in AI response: {idx}", flush=True)
            continue
        src = selected[idx]
        # main_company: AI가 본문 기준으로 재판정한 실질 주체 회사
        # 화이트리스트 검증 후 invalid면 src의 검색 기준 회사로 fallback
        main_company = ai_item.get("main_company") or src.get("company")
        if main_company not in VALID_COMPANIES:
            print(f"[WARN] Invalid main_company '{main_company}' for idx={idx}; using src company.", flush=True)
            main_company = src.get("company")
        # Log reclassifications for visibility
        if main_company != src.get("company"):
            print(f"[INFO] Reclassified idx={idx}: '{src.get('company')}' → '{main_company}'", flush=True)
            reclass_count += 1
        enriched.append({
            "company": main_company,
            "title": src.get("title"),
            "link": src.get("link"),
            "is_naver": src.get("is_naver", False),
            "media": src.get("media", ""),
            "sentiment": ai_item.get("sentiment", "neutral"),
            "summary": ai_item.get("summary", ""),
            "importance": ai_item.get("importance", 5),
            "event_group": ai_item.get("event_group", ""),
        })
    if reclass_count:
        print(f"[INFO] Total reclassifications: {reclass_count}", flush=True)

    n_naver_pre = sum(1 for it in enriched if it.get("is_naver"))
    n_ext_pre = sum(1 for it in enriched if not it.get("is_naver"))
    print(f"[INFO] AI filter result (pre-group-dedupe): total={len(enriched)} "
          f"(naver={n_naver_pre}, external={n_ext_pre})", flush=True)

    # --- Event-group dedupe (defensive: AI is instructed to do this, but verify in code) ---
    # If AI happens to return multiple items with the same non-empty event_group, keep only
    # the one with highest priority (naver-hosted > importance > earlier in list).
    grouped = {}  # event_group -> chosen item
    no_group = []  # items with empty event_group (treated as unique)
    for it in enriched:
        eg = (it.get("event_group") or "").strip().lower()
        if not eg:
            no_group.append(it)
            continue
        if eg not in grouped:
            grouped[eg] = it
        else:
            # Pick the better one: naver wins, then higher importance
            current = grouped[eg]
            cur_score = (1 if current.get("is_naver") else 0, current.get("importance", 0))
            new_score = (1 if it.get("is_naver") else 0, it.get("importance", 0))
            if new_score > cur_score:
                print(f"[INFO] Group dedupe: replacing '{current.get('title','')[:30]}' "
                      f"with '{it.get('title','')[:30]}' (group={eg})", flush=True)
                grouped[eg] = it
            else:
                print(f"[INFO] Group dedupe: dropping '{it.get('title','')[:30]}' "
                      f"(duplicate of group={eg})", flush=True)

    deduped = list(grouped.values()) + no_group
    n_dropped = len(enriched) - len(deduped)
    if n_dropped:
        print(f"[INFO] Event-group dedupe: dropped {n_dropped} duplicate(s) of {len(grouped)} group(s).",
              flush=True)

    n_naver = sum(1 for it in deduped if it.get("is_naver"))
    n_ext = sum(1 for it in deduped if not it.get("is_naver"))
    print(f"[INFO] AI filter result (final): total={len(deduped)} (naver={n_naver}, external={n_ext})", flush=True)

    deduped.sort(key=lambda x: (
        0 if x.get("sentiment") == "negative"
        else (1 if x.get("sentiment") == "neutral" else 2),
        -x.get("importance", 0),
    ))
    return deduped


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
