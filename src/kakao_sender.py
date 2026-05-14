"""Kakao memo (talk-to-self) message sender."""
import json
import re
import requests
import time
import urllib.parse
from urllib.parse import urlparse


KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

MAX_PER_LIST = 5
MIN_PER_LIST = 2
# 카카오 text 템플릿 본문 한도 (실측 200자). 묶기 알고리즘이 이 값 안에서
# 헤더·기사 entry·구분 빈 줄을 모두 관리한다.
TEXT_BODY_LIMIT = 200
# 외부 매체 entry의 제목 최대 길이 (회사태그 제외, 풀 URL과 함께 한 메시지에 묶기 위한 균형값)
EXT_TITLE_MAX = 25

DEFAULT_HEADER_LINK = "https://n.news.naver.com"
SEARCH_BASE = "https://search.naver.com/search.naver?where=news&query="

DEFAULT_IMAGE_URL = (
    "https://mud-kage.kakao.com/dn/bDPMIb/btqgeoTRQvd/"
    "49BuF1gNo6UXkdbKecx600/kakaolink40_original.png"
)

SENTIMENT_EMOJI = {
    "negative": "🔴",
    "neutral": "🟡",
    "positive": "🟢",
}


def _is_naver_link(url: str) -> bool:
    if not url:
        return False
    return any(d in url for d in (
        "://n.news.naver.com",
        "://m.news.naver.com",
        "://news.naver.com",
    ))


def _media_name_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return "외부 매체"
    if not host:
        return "외부 매체"
    for prefix in ("www.", "m.", "biz.", "news.", "view.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host or "외부 매체"


def _clean_search_query(title: str) -> str:
    """Strip quotes/ellipsis/truncation markers so Naver search matches more articles."""
    s = title
    # Remove various quote characters (straight and curly)
    s = re.sub(r"[\"'\u201C\u201D\u2018\u2019\u00B4`]", "", s)
    # Replace ellipsis and dot-runs with space
    s = re.sub(r"[\u2026\u22EF\u30FB\u2027]+", " ", s)  # …  ⋯  ・  ‧
    s = re.sub(r"\.{2,}", " ", s)                         # ...
    # 가운데점(·) 2개 이상 연속만 공백으로 (단일 ·는 회사명 구분에 정상 사용되므로 보존)
    s = re.sub(r"\u00B7{2,}", " ", s)
    # Drop bracket markers but keep their content
    s = re.sub(r"[\[\]\u3010\u3011\u300C\u300D\u300E\u300F<>()()]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Drop trailing 1-2 char fragment (likely truncated word like "엑", "격돌")
    parts = s.split(" ")
    if parts and len(parts[-1]) <= 2:
        parts = parts[:-1]
    # Cap to 6 keywords for broader matching (long queries match too narrowly)
    if len(parts) > 6:
        parts = parts[:6]
    s = " ".join(parts)
    # Hard length cap as safety net
    if len(s) > 30:
        s = s[:30].rsplit(" ", 1)[0] or s[:30]
    return s


def _naver_search_url(title: str) -> str:
    cleaned = _clean_search_query(title)
    return SEARCH_BASE + urllib.parse.quote(cleaned)


def split_for_list_template(items: list, max_per_chunk: int = MAX_PER_LIST) -> list[list]:
    n = len(items)
    if n == 0:
        return []
    if n == 1:
        return [items]
    num_chunks = (n + max_per_chunk - 1) // max_per_chunk
    base_size = n // num_chunks
    remainder = n % num_chunks
    chunks = []
    idx = 0
    for i in range(num_chunks):
        size = base_size + (1 if i < remainder else 0)
        chunks.append(items[idx:idx + size])
        idx += size
    return chunks


def _build_card_naver(item: dict) -> dict:
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    company = _get_item_tag(item)
    title = f"{emoji} [{company}] {item.get('title', '')}"
    if len(title) > 100:
        title = title[:97] + "..."
    link_url = item.get("link", DEFAULT_HEADER_LINK)
    return {
        "title": title,
        "description": item.get("summary", ""),
        "image_url": DEFAULT_IMAGE_URL,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
    }


def _build_card_external(item: dict) -> dict:
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    company = _get_item_tag(item)
    title = f"{emoji} [{company}] {item.get('title', '')}"
    if len(title) > 100:
        title = title[:97] + "..."
    media = item.get("media") or _media_name_from_url(item.get("link", ""))
    summary = item.get("summary", "")
    description = f"📰 {media} | {summary}" if summary else f"📰 {media}"
    if len(description) > 80:
        description = description[:77] + "..."
    search_url = _naver_search_url(item.get("title", ""))
    return {
        "title": title,
        "description": description,
        "image_url": DEFAULT_IMAGE_URL,
        "link": {"web_url": search_url, "mobile_web_url": search_url},
    }


def _build_list_template(items, header_title, is_external=False):
    assert MIN_PER_LIST <= len(items) <= MAX_PER_LIST
    build = _build_card_external if is_external else _build_card_naver
    contents = [build(it) for it in items]
    button_url = "https://search.naver.com" if is_external else DEFAULT_HEADER_LINK
    button_title = "네이버 뉴스 검색" if is_external else "네이버 뉴스 더보기"
    return {
        "object_type": "list",
        "header_title": header_title,
        "header_link": {
            "web_url": DEFAULT_HEADER_LINK,
            "mobile_web_url": DEFAULT_HEADER_LINK,
        },
        "contents": contents,
        "buttons": [{
            "title": button_title,
            "link": {"web_url": button_url, "mobile_web_url": button_url},
        }],
    }


def _build_feed_template(item, header_title, is_external=False):
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    company = _get_item_tag(item)
    if is_external:
        link_url = _naver_search_url(item.get("title", ""))
        media = item.get("media") or _media_name_from_url(item.get("link", ""))
        desc_extra = f"\n📰 {media}"
        btn_title = "네이버 뉴스 검색"
    else:
        link_url = item.get("link", DEFAULT_HEADER_LINK)
        desc_extra = ""
        btn_title = "기사 보기"
    return {
        "object_type": "feed",
        "content": {
            "title": f"{emoji} [{company}] {item.get('title', '')}",
            "description": f"{header_title}\n{item.get('summary', '')}{desc_extra}",
            "image_url": DEFAULT_IMAGE_URL,
            "link": {"web_url": link_url, "mobile_web_url": link_url},
        },
        "buttons": [{
            "title": btn_title,
            "link": {"web_url": link_url, "mobile_web_url": link_url},
        }],
    }


def _build_text_template(text: str) -> dict:
    return {
        "object_type": "text",
        "text": text[:200],
        "link": {
            "web_url": DEFAULT_HEADER_LINK,
            "mobile_web_url": DEFAULT_HEADER_LINK,
        },
    }


def _build_text_template_with_link(text: str, link_url: str) -> dict:
    """text 템플릿에 link 지정. '자세히 보기' 버튼이 이 URL로 이동.
    카카오 [제품 링크 관리]에 등록되지 않은 도메인일 경우 폴백되지만, 본문 URL을
    클릭하도록 사용자에게 안내했으므로 보조적인 동작."""
    return {
        "object_type": "text",
        "text": text[:200],
        "link": {
            "web_url": link_url or DEFAULT_HEADER_LINK,
            "mobile_web_url": link_url or DEFAULT_HEADER_LINK,
        },
    }


def _send_template(access_token: str, template: dict) -> dict:
    response = requests.post(
        KAKAO_MEMO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _send_group(access_token, items, header_base, is_external):
    if not items:
        return 0
    chunks = split_for_list_template(items)
    total = len(chunks)
    sent = 0
    for i, chunk in enumerate(chunks, 1):
        header = header_base if total == 1 else f"{header_base} ({i}/{total})"
        if len(chunk) == 1:
            template = _build_feed_template(chunk[0], header, is_external=is_external)
        else:
            template = _build_list_template(chunk, header, is_external=is_external)
        try:
            _send_template(access_token, template)
            sent += 1
        except requests.HTTPError as e:
            print(f"[ERROR] send failed ({i}/{total}, external={is_external}): {e}", flush=True)
            if e.response is not None:
                print(f"[ERROR] Body: {e.response.text}", flush=True)
            raise
        time.sleep(0.4)
    return sent


def _shorten_company(name: str) -> str:
    """User-defined company tag abbreviations."""
    mapping = {
        "한국투자금융지주": "지주",
        "한국투자증권": "증권",
        "한국투자신탁운용": "한투운용",
        "한국투자밸류자산운용": "밸류운용",
        "한국투자리얼에셋운용": "리얼에셋",
        "한국투자저축은행": "저축은행",
        "한국투자부동산신탁": "부동산신탁",
        "한국투자캐피탈": "캐피탈",
        "한국투자프라이빗에쿼티": "PE",
        "한국투자PE": "PE",
        "한국투자파트너스": "파트너스",
        "한국투자액셀러레이터": "AC",
    }
    return mapping.get(name, name)


def _shorten_sector(name: str) -> str:
    """Sector tag abbreviations (always with '업' suffix to distinguish from company tags)."""
    mapping = {
        "증권업": "증권업",
        "자산운용업": "운용업",
        "신탁업": "신탁업",
        "저축은행업": "저축은행업",
        "캐피탈·여전업": "여전업",
        "벤처투자업": "VC업",
        "금융지주업": "지주업",
    }
    return mapping.get(name, name)


def _get_item_tag(item: dict) -> str:
    """Return the bracket tag for an item: '[증권]' for company or '[운용업]' for sector."""
    if item.get("category") == "sector":
        return _shorten_sector(item.get("sector", "") or "")
    return _shorten_company(item.get("company", "") or "")


def _build_external_entry(item: dict) -> tuple[str, str]:
    """Build one external-media entry string + its primary URL.

    Returns: (entry_text, url). entry_text format:
        🔴[증권] 한국투자증권, 분기 영업이익 36..
        https://www.example.com/news/12345
    """
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    company = _get_item_tag(item)
    title = item.get("title", "") or ""
    url = item.get("link", "") or ""

    # 제목은 EXT_TITLE_MAX(25자)까지. 회사태그 길이와 무관하게 제목 자체의 글자수 기준.
    if len(title) > EXT_TITLE_MAX:
        title = title[:EXT_TITLE_MAX - 2] + ".."

    return f"{emoji}[{company}] {title}\n{url}", url


def _pack_external_to_text(items: list[dict]) -> list[tuple[str, str]]:
    """Pack external-media items into as few 200-char messages as possible.

    Greedy algorithm:
      1. Build each item's entry (emoji + tag + truncated title + URL).
      2. First pass with a worst-case header reserve (22 chars for "📰 외부 매체 (99/99)\\n\\n")
         to estimate chunk boundaries.
      3. Second pass rebuilds each chunk with its actual header — guaranteed under
         TEXT_BODY_LIMIT because real header is always ≤ worst-case.

    Returns: list of (full_message_text, primary_entry_url).
      - full_message_text already includes the header.
      - primary_entry_url is the URL of the FIRST entry in the chunk (used as
        text-template link target).
    """
    if not items:
        return []

    # Build all entries first
    entries = []  # list of (entry_text, url)
    for it in items:
        e, u = _build_external_entry(it)
        # Defensive: a single entry must fit in TEXT_BODY_LIMIT minus header reserve.
        # If URL alone is too long, we already can't help that; just include it.
        entries.append((e, u))

    # Worst-case header for two-digit / two-digit: "📰 외부 매체 (10/10)\n\n"
    # = '📰' (1) + ' 외부 매체 (' (8) + '10/10) ' part... compute exactly:
    sample_header = "📰 외부 매체 (10/10)\n\n"
    header_reserve = len(sample_header)
    # Per-entry separator: "\n\n" between adjacent entries (2 chars)
    SEP = "\n\n"

    # Pass 1: pack into groups under (LIMIT - header_reserve)
    groups = []
    current_group = []
    current_len = 0
    for entry_text, url in entries:
        addition = len(entry_text) + (len(SEP) if current_group else 0)
        if current_len + addition > (TEXT_BODY_LIMIT - header_reserve):
            if current_group:
                groups.append(current_group)
            current_group = [(entry_text, url)]
            current_len = len(entry_text)
        else:
            current_group.append((entry_text, url))
            current_len += addition
    if current_group:
        groups.append(current_group)

    # Pass 2: build final messages with real headers
    total = len(groups)
    results = []
    for i, group in enumerate(groups, 1):
        if total == 1:
            header = "📰 외부 매체\n\n"
        else:
            header = f"📰 외부 매체 ({i}/{total})\n\n"
        body = header + SEP.join(e for e, _ in group)

        # Safety check (should always pass given header_reserve in pass 1)
        if len(body) > TEXT_BODY_LIMIT:
            # Fallback: drop entries from the end until it fits
            while len(group) > 1 and len(body) > TEXT_BODY_LIMIT:
                dropped = group.pop()
                print(f"[WARN] msg {i}/{total} overflow; dropped entry: {dropped[1][:60]}",
                      flush=True)
                body = header + SEP.join(e for e, _ in group)
            # If still overflowing with 1 entry, the URL itself is huge — truncate body.
            if len(body) > TEXT_BODY_LIMIT:
                print(f"[ERROR] msg {i}/{total} still over {TEXT_BODY_LIMIT} chars "
                      f"with single entry; hard-truncating.", flush=True)
                body = body[:TEXT_BODY_LIMIT]

        # Primary URL for the text template's link.web_url = first entry's URL
        primary_url = group[0][1] if group else ""
        results.append((body, primary_url))

    return results


def send_daily_news(access_token, items, header_title_base):
    if not items:
        _send_template(access_token, _build_text_template(
            f"{header_title_base}\n금일 보고 대상 신규 기사 없음."
        ))
        return 1

    naver_items = [it for it in items if _is_naver_link(it.get("link", ""))]
    external_items = [it for it in items if not _is_naver_link(it.get("link", ""))]
    print(f"[INFO] Send split: naver={len(naver_items)}, external={len(external_items)}", flush=True)

    # Sentiment breakdown logging
    total_counts = {"negative": 0, "neutral": 0, "positive": 0}
    naver_counts = {"negative": 0, "neutral": 0, "positive": 0}
    ext_counts = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        s = it.get("sentiment", "neutral")
        total_counts[s] = total_counts.get(s, 0) + 1
    for it in naver_items:
        s = it.get("sentiment", "neutral")
        naver_counts[s] = naver_counts.get(s, 0) + 1
    for it in external_items:
        s = it.get("sentiment", "neutral")
        ext_counts[s] = ext_counts.get(s, 0) + 1
    print(
        f"[INFO] Sentiment total: 🔴{total_counts['negative']} 🟡{total_counts['neutral']} 🟢{total_counts['positive']}; "
        f"naver: 🔴{naver_counts['negative']} 🟡{naver_counts['neutral']} 🟢{naver_counts['positive']}; "
        f"external: 🔴{ext_counts['negative']} 🟡{ext_counts['neutral']} 🟢{ext_counts['positive']}",
        flush=True,
    )

    sent = 0
    # 1) Naver: list 카드 (▶ 버튼이 정상 작동)
    sent += _send_group(access_token, naver_items, header_title_base, is_external=False)

    # 2) External: text 메시지 (URL 본문 포함, 카톡이 자동 하이퍼링크)
    if external_items:
        text_chunks = _pack_external_to_text(external_items)
        total_ext = len(text_chunks)
        n_entries = len(external_items)
        print(
            f"[INFO] External packed: {n_entries} entries → {total_ext} text message(s) "
            f"(avg {n_entries/max(total_ext,1):.1f} entries/msg).",
            flush=True,
        )
        for i, (full_body, entry_url) in enumerate(text_chunks, 1):
            # Defensive final check (should already be ≤ TEXT_BODY_LIMIT from packer)
            if len(full_body) > TEXT_BODY_LIMIT:
                print(f"[ERROR] msg {i}/{total_ext} over {TEXT_BODY_LIMIT} chars: {len(full_body)}",
                      flush=True)
                full_body = full_body[:TEXT_BODY_LIMIT]
            try:
                # text 템플릿의 link.web_url을 그룹 첫 entry의 실제 기사 URL로 설정.
                # 카카오 [제품 링크 관리]에 등록된 도메인이 아니면 폴백되지만,
                # 본문의 URL이 카톡 자동 하이퍼링크 처리로 클릭 가능하므로 사용자에게는
                # 본문 URL 클릭을 유도. '자세히 보기' 버튼은 카카오 정책상 제거 불가.
                _send_template(access_token, _build_text_template_with_link(full_body, entry_url))
                sent += 1
            except requests.HTTPError as e:
                print(f"[ERROR] external text send failed ({i}/{total_ext}): {e}", flush=True)
                if e.response is not None:
                    print(f"[ERROR] Body: {e.response.text}", flush=True)
                raise
            time.sleep(0.4)
    return sent


def send_sector_news(access_token, items, header_title_base):
    """업권 거시 뉴스 발송. send_daily_news와 동일한 로직, 헤더만 다름.

    items의 각 element는 category='sector'를 가지고 있어야 정확한 태그 표시됨.
    """
    return send_daily_news(access_token, items, header_title_base)


def send_weekly_digest_text(access_token, text):
    # (i/total) prefix가 최대 8자 정도 추가되므로 본문 분할 한도는 그만큼 짧게.
    WEEKLY_BODY_LIMIT = TEXT_BODY_LIMIT - 10
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > WEEKLY_BODY_LIMIT:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())

    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        body = f"({i}/{total}) {chunk}" if total > 1 else chunk
        if len(body) > TEXT_BODY_LIMIT:
            body = body[:TEXT_BODY_LIMIT]
        _send_template(access_token, _build_text_template(body))
        time.sleep(0.4)
    return total
