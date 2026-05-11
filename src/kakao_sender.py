"""Kakao memo (talk-to-self) message sender with auto Naver/external split."""
import json
import requests
import time
from urllib.parse import urlparse


KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 카카오 list 템플릿 제약
MAX_PER_LIST = 5
MIN_PER_LIST = 2

# 카카오 text 템플릿 본문 한도 (안전 마진)
TEXT_BODY_LIMIT = 195

# 기본 헤더 링크 (반드시 카카오 [제품 링크 관리]에 등록된 도메인이어야 함)
DEFAULT_HEADER_LINK = "https://n.news.naver.com"

# Placeholder 이미지 (카카오 공개 CDN)
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


def split_for_list_template(items: list, max_per_chunk: int = MAX_PER_LIST) -> list[list]:
    """Split items into balanced chunks of size 2-5 (1 only when total==1)."""
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


def _build_list_template(items: list[dict], header_title: str) -> dict:
    assert MIN_PER_LIST <= len(items) <= MAX_PER_LIST
    contents = []
    for item in items:
        emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
        title = f"{emoji} [{item.get('company', '')}] {item.get('title', '')}"
        if len(title) > 100:
            title = title[:97] + "..."
        link_url = item.get("link", DEFAULT_HEADER_LINK)
        contents.append({
            "title": title,
            "description": item.get("summary", ""),
            "image_url": DEFAULT_IMAGE_URL,
            "link": {"web_url": link_url, "mobile_web_url": link_url},
        })
    return {
        "object_type": "list",
        "header_title": header_title,
        "header_link": {
            "web_url": DEFAULT_HEADER_LINK,
            "mobile_web_url": DEFAULT_HEADER_LINK,
        },
        "contents": contents,
        "buttons": [{
            "title": "네이버 뉴스 더보기",
            "link": {
                "web_url": DEFAULT_HEADER_LINK,
                "mobile_web_url": DEFAULT_HEADER_LINK,
            },
        }],
    }


def _build_feed_template(item: dict, header_title: str) -> dict:
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    link_url = item.get("link", DEFAULT_HEADER_LINK)
    return {
        "object_type": "feed",
        "content": {
            "title": f"{emoji} [{item.get('company', '')}] {item.get('title', '')}",
            "description": f"{header_title}\n\n{item.get('summary', '')}",
            "image_url": DEFAULT_IMAGE_URL,
            "link": {"web_url": link_url, "mobile_web_url": link_url},
        },
        "buttons": [{
            "title": "기사 보기",
            "link": {"web_url": link_url, "mobile_web_url": link_url},
        }],
    }


def _build_text_template(text: str) -> dict:
    """Text body up to ~200 chars. URLs in body become tappable links in KakaoTalk."""
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


def _pack_external_entries(items: list[dict]) -> list[str]:
    """Pack external-media items into text chunks <= TEXT_BODY_LIMIT chars each.

    Each entry format (compact, URL-tappable in KakaoTalk):
        🔴 [회사] 제목 (40자내)
        📰 매체  URL
    """
    entries = []
    for it in items:
        emoji = SENTIMENT_EMOJI.get(it.get("sentiment", "neutral"), "🟡")
        company = it.get("company", "")
        title = it.get("title", "")
        if len(title) > 45:
            title = title[:43] + ".."
        media = _media_name_from_url(it.get("link", ""))
        url = it.get("link", "")
        entry = f"{emoji} [{company}] {title}\n📰 {media}\n{url}"
        entries.append(entry)

    # Pack greedily into <=TEXT_BODY_LIMIT char chunks
    chunks = []
    current = ""
    for e in entries:
        sep = "\n\n" if current else ""
        if len(current) + len(sep) + len(e) > TEXT_BODY_LIMIT:
            if current:
                chunks.append(current)
            # Single entry larger than limit? Truncate the title further
            if len(e) > TEXT_BODY_LIMIT:
                e = e[:TEXT_BODY_LIMIT - 3] + "..."
            current = e
        else:
            current += sep + e
    if current:
        chunks.append(current)
    return chunks


def send_daily_news(
    access_token: str,
    items: list[dict],
    header_title_base: str,
) -> int:
    """Send daily news: Naver-URL items as list cards, external items as text.

    Returns: total number of Kakao messages sent.
    """
    if not items:
        _send_template(access_token, _build_text_template(
            f"{header_title_base}\n금일 보고 대상 신규 기사 없음."
        ))
        return 1

    # Split by Naver vs external
    naver_items = [it for it in items if _is_naver_link(it.get("link", ""))]
    external_items = [it for it in items if not _is_naver_link(it.get("link", ""))]

    print(
        f"[INFO] Send split: naver={len(naver_items)}, external={len(external_items)}",
        flush=True,
    )

    sent_count = 0

    # 1) Naver items -> list/feed templates
    if naver_items:
        chunks = split_for_list_template(naver_items)
        total = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            if total == 1:
                header = header_title_base
            elif i == 1:
                header = f"{header_title_base} (네이버 {i}/{total})"
            else:
                header = f"한국투자금융그룹 데일리 뉴스 (네이버 {i}/{total})"

            template = _build_feed_template(chunk[0], header) if len(chunk) == 1 \
                else _build_list_template(chunk, header)
            try:
                _send_template(access_token, template)
                sent_count += 1
            except requests.HTTPError as e:
                print(f"[ERROR] list send failed ({i}/{total}): {e}", flush=True)
                if e.response is not None:
                    print(f"[ERROR] Body: {e.response.text}", flush=True)
                raise
            time.sleep(0.4)

    # 2) External items -> text chunks
    if external_items:
        ext_chunks = _pack_external_entries(external_items)
        total_ext = len(ext_chunks)
        for i, body in enumerate(ext_chunks, 1):
            if total_ext == 1:
                header_line = "📰 외부 매체 기사"
            else:
                header_line = f"📰 외부 매체 기사 ({i}/{total_ext})"
            full = f"{header_line}\n\n{body}"
            try:
                _send_template(access_token, _build_text_template(full))
                sent_count += 1
            except requests.HTTPError as e:
                print(f"[ERROR] text send failed ({i}/{total_ext}): {e}", flush=True)
                if e.response is not None:
                    print(f"[ERROR] Body: {e.response.text}", flush=True)
                raise
            time.sleep(0.4)

    return sent_count


def send_weekly_digest_text(access_token: str, text: str) -> int:
    """Send weekly digest as text chunks. URLs in body remain tappable in KakaoTalk."""
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
