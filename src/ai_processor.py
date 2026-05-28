"""AI processor for news filtering, sentiment analysis, summarization, and grouping.

Backend: Google Gemini (default: gemini-3-flash-preview).

Env vars:
    GEMINI_API_KEY            (required) — Google AI Studio API key
    GEMINI_MODEL              (optional) — model name override (default: gemini-3-flash-preview)

Free tier (as of May 2026): 1,500 requests/day on Flash models.
This bot uses ~5 requests/day, so cost is effectively $0 within the free tier.
"""
import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types

try:
    from json_repair import repair_json
    HAS_REPAIR = True
except ImportError:
    HAS_REPAIR = False


# Default model. Override via GEMINI_MODEL env var.
DEFAULT_MODEL = "gemini-3-flash-preview"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

# Fallback model when primary fails with transient errors (503 etc).
# 안정 버전이라 preview보다 가용성 높음.
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash").strip()

# Retry policy for transient Gemini errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED, etc).
# 3 attempts on primary model + 1 attempt on fallback model = 최대 4 attempts total.
RETRY_ATTEMPTS_PRIMARY = 3
RETRY_WAIT_SECONDS = [2, 4]  # 1차 실패 후 2초, 2차 실패 후 4초 대기

MAX_TOKENS = 16000

# 입력 기사 한도. 네이버 + 외부 모두 합쳐서.
MAX_ARTICLES_PER_CALL = 90

# 외부 매체 기사를 입력에 보장할 최소 비중 (기사가 충분히 많을 때)
MIN_EXTERNAL_RATIO = 0.5

PROMPT_PATH = Path(__file__).parent.parent / "prompt" / "kih_daily_news_agent.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _get_client() -> genai.Client:
    """Build Gemini client. API key from GEMINI_API_KEY env var."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a free API key from https://aistudio.google.com/apikey "
            "and register it as a GitHub Secret."
        )
    return genai.Client(api_key=api_key)


def _preselect_articles(articles: list[dict], max_n: int) -> list[dict]:
    """Cap input to AI. Keep all Naver, then ensure external gets a fair share."""
    if len(articles) <= max_n:
        return [_trim_article(a) for a in articles]

    naver = [a for a in articles if a.get("is_naver")]
    external = [a for a in articles if not a.get("is_naver")]

    min_external_slots = int(max_n * MIN_EXTERNAL_RATIO)
    external_take = min(len(external), max(min_external_slots, max_n - len(naver)))
    naver_take = min(len(naver), max_n - external_take)
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
    """Try to extract a JSON object from Gemini's response.

    Even with response_mime_type='application/json', occasionally need to handle
    surrounding whitespace / markdown fences. Strategies:
      1. Strict json.loads
      2. Strip markdown fences then strict
      3. json-repair (handles unescaped quotes, trailing commas, etc.)
      4. Manual truncation recovery
    """
    text = text.strip()

    # 1. Strict parse first (works when response_mime_type=application/json)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences (legacy / fallback)
    stripped = re.sub(r"^```(?:json)?\s*", "", text)
    stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        print(f"[WARN] Strict JSON parse failed: {e}", flush=True)

    # 3. json-repair
    if HAS_REPAIR:
        try:
            repaired = repair_json(stripped)
            result = json.loads(repaired)
            print("[INFO] JSON recovered by json-repair.", flush=True)
            return result
        except Exception as e:
            print(f"[WARN] json-repair failed: {e}", flush=True)

    # 4. Manual truncation recovery
    if '"filtered"' in stripped:
        for closing in ("},", "}\n"):
            last_pos = stripped.rfind(closing)
            if last_pos != -1:
                candidate = stripped[: last_pos + 1] + "]}"
                try:
                    result = json.loads(candidate)
                    print(f"[INFO] JSON recovered by truncation at '{closing}'.", flush=True)
                    return result
                except json.JSONDecodeError:
                    continue
    return None


def _is_transient_error(exc: Exception) -> bool:
    """Identify transient Gemini errors that warrant retry.

    Transient (재시도 가치 있음):
      - 503 UNAVAILABLE (server overload, "high demand")
      - 429 RESOURCE_EXHAUSTED (rate limit)
      - 5xx server errors
      - Connection errors (timeout, reset)

    Permanent (재시도 안 함):
      - 401/403 (auth)
      - 400 (bad request, invalid input)
    """
    msg = str(exc)
    # google-genai SDK는 HTTP 코드를 문자열에 포함시킴: "503 UNAVAILABLE", "429 RESOURCE_EXHAUSTED" 등
    transient_signals = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
                          "500", "INTERNAL", "504", "DEADLINE_EXCEEDED",
                          "Connection", "Timeout", "timed out"]
    permanent_signals = ["401", "403", "PERMISSION_DENIED", "UNAUTHENTICATED",
                          "400", "INVALID_ARGUMENT", "NOT_FOUND"]
    for sig in permanent_signals:
        if sig in msg:
            return False
    for sig in transient_signals:
        if sig in msg:
            return True
    # 보수적: 알 수 없는 에러는 transient로 간주하여 재시도
    return True


def _call_gemini_once(model: str, system_prompt: str, user_message: str) -> str:
    """Single Gemini API call. May raise."""
    client = _get_client()
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=MAX_TOKENS,
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


def _call_gemini(system_prompt: str, user_message: str) -> str:
    """Send request to Gemini with retry on transient errors.

    Strategy:
      1. Try primary model (GEMINI_MODEL) up to RETRY_ATTEMPTS_PRIMARY times
         with exponential-ish backoff (2s, 4s).
      2. If all primary attempts fail with transient errors, fall back to
         FALLBACK_MODEL (안정 버전) once.
      3. Permanent errors (401, 400 etc) abort immediately without retry.

    Returns raw response text. Raises only after all retries exhausted.
    """
    last_exc = None

    # Phase 1: primary model retries
    for attempt in range(1, RETRY_ATTEMPTS_PRIMARY + 1):
        try:
            text = _call_gemini_once(GEMINI_MODEL, system_prompt, user_message)
            if attempt > 1:
                print(f"[INFO][gemini] Primary model succeeded on attempt {attempt}/{RETRY_ATTEMPTS_PRIMARY}.",
                      flush=True)
            return text
        except Exception as e:
            last_exc = e
            is_transient = _is_transient_error(e)
            if not is_transient:
                print(f"[ERROR][gemini] Permanent error on primary "
                      f"(attempt {attempt}/{RETRY_ATTEMPTS_PRIMARY}): {type(e).__name__}: {e}",
                      flush=True)
                raise
            if attempt < RETRY_ATTEMPTS_PRIMARY:
                wait = RETRY_WAIT_SECONDS[attempt - 1] if (attempt - 1) < len(RETRY_WAIT_SECONDS) else RETRY_WAIT_SECONDS[-1]
                print(f"[WARN][gemini] Transient error on primary "
                      f"(attempt {attempt}/{RETRY_ATTEMPTS_PRIMARY}): {type(e).__name__}: {e}. "
                      f"Retrying in {wait}s...", flush=True)
                time.sleep(wait)
            else:
                print(f"[WARN][gemini] Primary model exhausted "
                      f"({RETRY_ATTEMPTS_PRIMARY} attempts). Falling back to {FALLBACK_MODEL}.",
                      flush=True)

    # Phase 2: fallback model (single attempt)
    if FALLBACK_MODEL and FALLBACK_MODEL != GEMINI_MODEL:
        try:
            print(f"[INFO][gemini] Attempting fallback model: {FALLBACK_MODEL}", flush=True)
            text = _call_gemini_once(FALLBACK_MODEL, system_prompt, user_message)
            print(f"[INFO][gemini] Fallback model {FALLBACK_MODEL} succeeded.", flush=True)
            return text
        except Exception as e:
            print(f"[ERROR][gemini] Fallback model also failed: {type(e).__name__}: {e}",
                  flush=True)
            last_exc = e

    # All attempts failed
    raise last_exc if last_exc else RuntimeError("All Gemini call attempts failed without raising")


def process_daily_news(articles: list[dict]) -> list[dict]:
    """회사 카테고리(category='company') 기사만 필터링·요약하여 반환.

    업권 카테고리 기사가 섞여 있어도 자동으로 제외하고 회사 기사만 처리.
    업권 카테고리는 별도로 process_sector_news()를 호출.
    """
    if not articles:
        print("[STEP 3][diag] Input articles list is empty.", flush=True)
        return []

    # 회사 카테고리 기사만 추림. category 필드가 없는 구버전 데이터는 안전상 회사로 간주.
    company_articles = [a for a in articles
                        if a.get("category", "company") == "company"]

    # 진단 로그: 입력 카테고리 분포
    sector_articles_count = sum(1 for a in articles if a.get("category") == "sector")
    no_cat_count = sum(1 for a in articles if "category" not in a)
    print(
        f"[STEP 3][diag] Input breakdown: total={len(articles)}, "
        f"company={len(company_articles)}, sector={sector_articles_count}, no_category={no_cat_count}",
        flush=True,
    )

    if not company_articles:
        print("[STEP 3][diag] No company-category articles to process. "
              "원본 articles에 category='company' 항목이 없습니다.", flush=True)
        # 더 자세한 진단: 첫 3건의 category 값 표시
        for i, a in enumerate(articles[:3]):
            print(f"[STEP 3][diag] articles[{i}]: category={a.get('category')!r}, "
                  f"company={a.get('company')!r}, sector={a.get('sector')!r}, "
                  f"title={(a.get('title') or '')[:40]!r}", flush=True)
        return []

    selected = _preselect_articles(company_articles, MAX_ARTICLES_PER_CALL)

    # 각 기사에 stable idx 부여. AI는 idx만 응답하고, 코드가 link를 다시 매핑.
    indexed_articles = []
    for i, a in enumerate(selected):
        indexed_articles.append({"idx": i, **a})

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
        "- 검색에 사용된 회사명(입력 데이터의 company 필드)과 main_company가 다를 수 있음. 본문 기준으로 재판단.\n"
        "★ 오매칭 방지 (매우 중요): 기사의 실제 주체가 한국투자그룹 계열사가 '아닌' 경우 "
        "(예: KB금융지주, 신한증권, 미래에셋, OK저축은행 등 타사 기사) 그 기사는 filtered에서 제외하라. "
        "'금융지주', '증권사' 같은 일반명사나 'KB금융그룹'을 '한국투자금융지주'로 임의 해석 금지. "
        "summary에도 본문에 없는 한국투자 계열사명을 날조하지 말 것. 본문에 실제 나온 회사명만 사용.\n\n"
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

    print(f"[INFO] Calling Gemini ({GEMINI_MODEL}) for daily news...", flush=True)
    print(f"[STEP 3][diag] Sending {len(indexed_articles)} company articles to AI "
          f"(user_message length: {len(user_message)} chars)", flush=True)
    try:
        text = _call_gemini(system_prompt, user_message)
    except Exception as e:
        print(f"[ERROR][STEP 3] Gemini API call failed: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return []

    print(f"[STEP 3][diag] Gemini response received (length: {len(text)} chars).", flush=True)
    if not text or len(text) < 20:
        print(f"[ERROR][STEP 3] Gemini response is empty or too short: {text!r}", flush=True)
        return []

    result = _extract_json(text)
    if result is None:
        print(f"[ERROR][STEP 3] Failed to parse Gemini response. Length={len(text)}", flush=True)
        chunk_size = 2000
        for i in range(0, len(text), chunk_size):
            print(f"[ERROR_DUMP {i}-{i+chunk_size}] {text[i:i+chunk_size]}", flush=True)
        return []

    ai_items = result.get("filtered", [])
    print(f"[STEP 3][diag] AI returned 'filtered' array with {len(ai_items)} items.", flush=True)
    if not ai_items:
        # 진단: 어떤 키들이 응답에 있는지
        print(f"[WARN][STEP 3] AI returned empty 'filtered' array. "
              f"Response keys: {list(result.keys())}", flush=True)
        # 응답 처음 500자 표시
        print(f"[WARN][STEP 3] Response preview: {text[:500]!r}", flush=True)
        return []

    VALID_COMPANIES = {
        "한국투자금융지주", "한국투자증권", "한국투자신탁운용", "한국투자밸류자산운용",
        "한국투자파트너스", "한국투자프라이빗에쿼티", "한국투자캐피탈", "한국투자저축은행",
        "한국투자리얼에셋운용", "한국투자부동산신탁", "한국투자액셀러레이터",
    }

    # Re-attach original data by idx
    enriched = []
    reclass_count = 0
    for ai_item in ai_items:
        idx = ai_item.get("idx")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(selected):
            print(f"[WARN] Invalid idx in AI response: {idx}", flush=True)
            continue
        src = selected[idx]
        main_company = ai_item.get("main_company") or src.get("company")
        if main_company not in VALID_COMPANIES:
            print(f"[WARN] Invalid main_company '{main_company}' for idx={idx}; using src company.", flush=True)
            main_company = src.get("company")
        if main_company != src.get("company"):
            print(f"[INFO] Reclassified idx={idx}: '{src.get('company')}' → '{main_company}'", flush=True)
            reclass_count += 1
        enriched.append({
            "category": "company",
            "company": main_company,
            "sector": None,
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

    # --- Event-group dedupe (방어적: AI 지시에 더해 코드에서도 한 번 더 확인) ---
    grouped = {}
    no_group = []
    for it in enriched:
        eg = (it.get("event_group") or "").strip().lower()
        if not eg:
            no_group.append(it)
            continue
        if eg not in grouped:
            grouped[eg] = it
        else:
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


def process_sector_news(articles: list[dict]) -> list[dict]:
    """업권 카테고리(category='sector') 기사를 업권별로 필터링·요약하여 반환.

    업권별 최대 3건. 중요도 기준 상위 3개. 단순 경쟁사 홍보·시황 분석은 제외.

    Returns:
        list of dicts with fields:
        - category: "sector"
        - sector: 업권명 (예: "증권업")
        - title, link, is_naver, media, sentiment, summary, importance, event_group
    """
    if not articles:
        return []

    # 업권 카테고리만 추림.
    sector_articles = [a for a in articles if a.get("category") == "sector"]
    if not sector_articles:
        print("[INFO] No sector-category articles to process.", flush=True)
        return []

    selected = _preselect_articles(sector_articles, MAX_ARTICLES_PER_CALL)
    indexed_articles = [{"idx": i, **a} for i, a in enumerate(selected)]

    system_prompt = _load_prompt()

    user_message = (
        "아래는 직전 24시간 동안 수집된 한국투자금융그룹과 동일 업권의 거시 뉴스 목록입니다.\n"
        "각 기사는 'idx' 번호가 부여되어 있으며, 검색 시점에 매칭된 'sector' 필드가 있습니다.\n"
        "시스템 프롬프트의 §4.5 (업권 거시 뉴스 판정 기준)을 적용하세요.\n\n"
        f"기사 목록 ({len(indexed_articles)}건, JSON):\n"
        f"{json.dumps(indexed_articles, ensure_ascii=False, indent=2)}\n\n"
        "다음 JSON 형식으로만 응답. 다른 텍스트 절대 금지:\n"
        "{\n"
        '  "filtered": [\n'
        "    {\n"
        '      "idx": 기사의 idx 번호 (정수),\n'
        '      "main_sector": "기사의 실질 영향 업권명 (아래 목록 중 하나만)",\n'
        '      "summary": "40자 이내 한 줄 요약 (주술 구조)",\n'
        '      "importance": 1-10,\n'
        '      "event_group": "이 기사가 다루는 사건의 고유 식별자 (영문 슬러그, 30자 이내)"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "업권 뉴스는 정책·규제·시장 구조 변경 정보가 대부분이므로 감성(긍정/부정) 분류는 하지 않음. "
        "회사 입장에 따라 같은 정책이 기회일 수도 위협일 수도 있어 일률적 감성 부여가 부적절. "
        "중요도(importance)로만 우선순위 결정.\n\n"
        "main_sector는 반드시 다음 7개 중 하나의 정확한 업권명으로 응답:\n"
        "- 증권업\n"
        "- 자산운용업\n"
        "- 신탁업\n"
        "- 저축은행업\n"
        "- 캐피탈·여전업\n"
        "- 벤처투자업\n"
        "- 금융지주업\n\n"
        "업권 뉴스 포함 기준 (이 중 하나라도 해당하면 포함):\n"
        "1. 정부·감독기관 활동: 금융위원회·금융감독원·기획재정부·한국은행의 정책 발표, 검사, 제재, 인허가\n"
        "2. 법령·제도 변경: 자본시장법, 금융투자업감독규정, 여신전문금융업법, 신탁업법 등의 개정·시행\n"
        "3. 업권 단체 결정: 금융투자협회·자산운용협회·여신금융협회·저축은행중앙회 등의 자율규제·합의\n"
        "4. 시장 구조 변화: 업권 전체에 영향을 주는 거래소 규정 변경, 신상품군 도입, 시장 점유율 빅 시프트\n"
        "5. 거시경제 이슈 중 해당 업권에 직접 영향: 기준금리, 환율, 부동산 정책, 가계부채 정책 등\n\n"
        "제외 기준 (엄격히 적용):\n"
        "1. 특정 경쟁사의 단순 상품 출시·홍보 (정부 정책과 무관한 회사 자체 활동)\n"
        "2. 일반 시황 분석·종목 추천 글\n"
        "3. 특정 종목의 목표주가·투자의견 (리서치센터 보고서 인용)\n"
        "4. 단순 기업 정보 (인사·실적 등)이지만 업권 전반 영향 없는 것\n"
        "5. 정부 정책이지만 본 업권과 무관한 것 (예: 부동산 정책은 신탁업·증권업에 영향, 통신 정책은 무관)\n\n"
        "★ 회사명·주체 정확성 (매우 중요):\n"
        "- summary에 등장하는 회사명·기관명은 반드시 기사 본문에 실제로 나온 것만 사용. 추측·날조 절대 금지.\n"
        "- '한국형', '국내 최대 금융지주', '국내 증권사' 같은 일반 표현을 특정 회사명으로 바꾸지 말 것.\n"
        "- 특히 '한국투자금융지주'/'한국투자증권' 등 한국투자그룹 계열사명은 본문에 그 정식 명칭이 명시된 경우에만 사용. "
        "'금융지주', '증권사' 같은 일반명사를 한국투자 계열사로 임의 해석 금지.\n"
        "- 예: 본문이 'KB금융그룹과 협력'인데 summary를 '한국금융지주 협력'으로 쓰면 심각한 오류. "
        "본문의 실제 주체(KB금융그룹)를 그대로 표기하거나, 업권 일반 이슈로 요약할 것.\n"
        "- 기사의 실제 주체가 한국투자 계열사가 아니면 summary에도 한국투자 계열사를 언급하지 말 것.\n\n"
        "중요도(importance) 판정 가중치:\n"
        "- 8-10: 즉각적인 업권 전반 영향 (예: 금감원 일제 검사, 자본시장법 개정)\n"
        "- 5-7: 중기 정책 방향 변경 또는 시장 구조 변화 (예: 신상품 가이드라인 발표)\n"
        "- 1-4: 간접 영향 또는 후속 보도 (예: 정책 해설 기사)\n\n"
        "event_group 규칙: 같은 사건을 다른 매체가 보도한 경우 동일 슬러그 부여. "
        "한 event_group에서 가장 정보량 많고 신뢰도 높은 매체 1건만 filtered 배열에 포함.\n"
        "예: 'fsc-etf-regulation-2026', 'bok-rate-decision-may2026'\n\n"
        "summary는 반드시 40자 이내, 군더더기 없이 간결하게.\n"
        "보도 가치가 조금이라도 있으면 포함하되 위 제외 기준 5번을 엄격히 적용하여 본 업권과 무관한 기사는 빼세요.\n"
    )

    print(f"[INFO] Calling Gemini ({GEMINI_MODEL}) for sector news...", flush=True)
    try:
        text = _call_gemini(system_prompt, user_message)
    except Exception as e:
        print(f"[ERROR] Gemini sector API call failed: {type(e).__name__}: {e}", flush=True)
        return []

    result = _extract_json(text)
    if result is None:
        print(f"[ERROR] Failed to parse Gemini sector response. Length={len(text)}", flush=True)
        return []

    ai_items = result.get("filtered", [])

    VALID_SECTORS = {
        "증권업", "자산운용업", "신탁업", "저축은행업",
        "캐피탈·여전업", "벤처투자업", "금융지주업",
    }

    enriched = []
    for ai_item in ai_items:
        idx = ai_item.get("idx")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(selected):
            print(f"[WARN] Invalid idx in AI sector response: {idx}", flush=True)
            continue
        src = selected[idx]
        main_sector = ai_item.get("main_sector") or src.get("sector")
        if main_sector not in VALID_SECTORS:
            print(f"[WARN] Invalid main_sector '{main_sector}' for idx={idx}; using src sector.", flush=True)
            main_sector = src.get("sector")
        enriched.append({
            "category": "sector",
            "company": None,
            "sector": main_sector,
            "title": src.get("title"),
            "link": src.get("link"),
            "is_naver": src.get("is_naver", False),
            "media": src.get("media", ""),
            # sentiment 필드는 의도적으로 비움 (업권 뉴스는 감성 분류 안 함).
            # 다운스트림 렌더러는 sentiment가 빈 문자열/None이면 이모지 표시 안 함.
            "sentiment": "",
            "summary": ai_item.get("summary", ""),
            "importance": ai_item.get("importance", 5),
            "event_group": ai_item.get("event_group", ""),
        })

    # --- Event-group dedupe (방어적) ---
    grouped = {}
    no_group = []
    for it in enriched:
        eg = (it.get("event_group") or "").strip().lower()
        if not eg:
            no_group.append(it)
            continue
        if eg not in grouped:
            grouped[eg] = it
        else:
            current = grouped[eg]
            cur_score = (1 if current.get("is_naver") else 0, current.get("importance", 0))
            new_score = (1 if it.get("is_naver") else 0, it.get("importance", 0))
            if new_score > cur_score:
                grouped[eg] = it
    deduped = list(grouped.values()) + no_group

    # --- 업권별 최대 3건 제한 (중요도 상위) ---
    by_sector = {}
    for it in deduped:
        sec = it.get("sector")
        if sec not in by_sector:
            by_sector[sec] = []
        by_sector[sec].append(it)

    final = []
    for sec, items in by_sector.items():
        items.sort(key=lambda x: -x.get("importance", 0))
        before = len(items)
        items = items[:3]
        if before > 3:
            print(f"[INFO] Sector '{sec}': trimmed {before}→3 (top importance)", flush=True)
        final.extend(items)

    # 최종 정렬: 중요도만 (업권 뉴스는 감성 분류를 하지 않으므로)
    final.sort(key=lambda x: -x.get("importance", 0))

    print(f"[INFO] Sector filter result: {len(final)} items across {len(by_sector)} sector(s)", flush=True)
    for sec, items in by_sector.items():
        kept = [it for it in final if it.get("sector") == sec]
        print(f"[INFO]   - {sec}: {len(kept)} kept", flush=True)

    return final


def process_weekly_digest(articles: list[dict]) -> dict:
    if not articles:
        return {"by_company": {}, "keywords": []}

    selected = _preselect_articles(articles, MAX_ARTICLES_PER_CALL * 2)
    indexed_articles = [{"idx": i, **a} for i, a in enumerate(selected)]

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

    print(f"[INFO] Calling Gemini ({GEMINI_MODEL}) for weekly digest...", flush=True)
    try:
        text = _call_gemini(system_prompt, user_message)
    except Exception as e:
        print(f"[ERROR] Gemini API call failed (weekly): {type(e).__name__}: {e}", flush=True)
        return {"by_company": {}, "keywords": []}

    result = _extract_json(text)
    if result is None:
        print("[ERROR] Failed to parse weekly digest response.", flush=True)
        return {"by_company": {}, "keywords": []}

    by_company = result.get("by_company", {})
    for company, items in by_company.items():
        for item in items:
            idx = item.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(selected):
                item["link"] = selected[idx].get("link", "")
            else:
                item["link"] = ""
    return {"by_company": by_company, "keywords": result.get("keywords", [])}
