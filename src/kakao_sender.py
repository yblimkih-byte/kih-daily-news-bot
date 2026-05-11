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
TEXT_BODY_LIMIT = 195

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
    title = f"{emoji} [{item.get('company', '')}] {item.get('title', '')}"
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
    title = f"{emoji} [{item.get('company', '')}] {item.get('title', '')}"
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
            "title": f"{emoji} [{item.get('company', '')}] {item.get('title', '')}",
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
    """Very compact 2-3 char company codes."""
    mapping = {
        "한국투자금융지주": "지주",
        "한국투자증권": "증권",
        "한국투자신탁운용": "운용",
        "한국투자밸류자산운용": "밸류",
        "한국투자파트너스": "VC",
        "한국투자프라이빗에쿼티": "PE",
        "한국투자캐피탈": "캐피탈",
        "한국투자저축은행": "저축",
        "한국투자리얼에셋운용": "리얼",
        "한국투자액셀러레이터": "액셀",
    }
    return mapping.get(name, name)


def _pack_external_to_text(items: list[dict]) -> list[str]:
    """Pack external-media items into minimal text messages.

    Per-entry format (most compact possible):
        🔴[증권] 짧은 제목..
        URL
    Media name is omitted (URL host conveys it). Title capped to 25 chars.
    Each message ≤195 chars. Header lines for multi-message handled by caller.
    """
    entries = []
    for it in items:
        emoji = SENTIMENT_EMOJI.get(it.get("sentiment", "neutral"), "🟡")
        company = _shorten_company(it.get("company", ""))
        title = it.get("title", "")
        if len(title) > 25:
            title = title[:23] + ".."
        url = it.get("link", "")
        entry = f"{emoji}[{company}] {title}\n{url}"
        entries.append(entry)

    # Greedy pack <=TEXT_BODY_LIMIT chars per message
    chunks = []
    current = ""
    for e in entries:
        sep = "\n" if current else ""
        candidate = current + sep + e
        if len(candidate) > TEXT_BODY_LIMIT:
            if current:
                chunks.append(current)
            if len(e) > TEXT_BODY_LIMIT:
                e = e[:TEXT_BODY_LIMIT]
            current = e
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_daily_news(access_token, items, header_title_base):
    if not items:
        _send_template(access_token, _build_text_template(
            f"{header_title_base}\n금일 보고 대상 신규 기사 없음."
        ))
        return 1

    naver_items = [it for it in items if _is_naver_link(it.get("link", ""))]
    external_items = [it for it in items if not _is_naver_link(it.get("link", ""))]
    print(f"[INFO] Send split: naver={len(naver_items)}, external={len(external_items)}", flush=True)

    sent = 0
    # 1) Naver: list 카드 (▶ 버튼이 정상 작동)
    sent += _send_group(access_token, naver_items, header_title_base, is_external=False)

    # 2) External: text 메시지 (URL 본문 포함, 카톡이 자동 하이퍼링크)
    if external_items:
        text_chunks = _pack_external_to_text(external_items)
        total_ext = len(text_chunks)
        print(f"[INFO] External packed into {total_ext} text message(s).", flush=True)
        for i, body in enumerate(text_chunks, 1):
            header = "📰 외부 매체 기사" if total_ext == 1 else f"📰 외부 매체 기사 ({i}/{total_ext})"
            full = f"{header}\n\n{body}"
            if len(full) > 200:
                # Header가 들어가서 limit 초과 시 entry 다시 압축
                full = full[:200]
            try:
                _send_template(access_token, _build_text_template(full))
                sent += 1
            except requests.HTTPError as e:
                print(f"[ERROR] external text send failed ({i}/{total_ext}): {e}", flush=True)
                if e.response is not None:
                    print(f"[ERROR] Body: {e.response.text}", flush=True)
                raise
            time.sleep(0.4)
    return sent


def send_weekly_digest_text(access_token, text):
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TEXT_BODY_LIMIT:
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
        if len(body) > 200:
            body = body[:200]
        _send_template(access_token, _build_text_template(body))
        time.sleep(0.4)
    return total
