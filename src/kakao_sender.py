"""Kakao memo (talk-to-self) message sender using list template."""
import json
import requests
import time


KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 카카오 list 템플릿 제약
MAX_PER_LIST = 5  # 한 list 메시지에 최대 5개 contents
MIN_PER_LIST = 2  # 한 list 메시지에 최소 2개 contents

# 기본 헤더 링크 (카카오 앱 [제품 링크 관리]에 등록된 도메인이어야 함)
DEFAULT_HEADER_LINK = "https://n.news.naver.com"

# 회사별 placeholder 이미지 (카카오 공개 CDN, 도메인 등록 불필요)
DEFAULT_IMAGE_URL = (
    "https://mud-kage.kakao.com/dn/bDPMIb/btqgeoTRQvd/"
    "49BuF1gNo6UXkdbKecx600/kakaolink40_original.png"
)

SENTIMENT_EMOJI = {
    "negative": "🔴",
    "neutral": "🟡",
    "positive": "🟢",
}


def split_for_list_template(items: list, max_per_chunk: int = MAX_PER_LIST) -> list[list]:
    """Split items into chunks of size 2-5 (carry-over to balance, last >= 2).

    Examples:
        17 items -> [5, 4, 4, 4]
        11 items -> [4, 4, 3]
        6 items  -> [3, 3]
        2 items  -> [2]
        1 item   -> [1]  (caller should switch to feed template)
        0 items  -> []
    """
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


def _build_list_template(
    items: list[dict],
    header_title: str,
    header_link_url: str = DEFAULT_HEADER_LINK,
) -> dict:
    """Build a list template object for 2-5 news items."""
    assert MIN_PER_LIST <= len(items) <= MAX_PER_LIST, \
        f"list template needs {MIN_PER_LIST}-{MAX_PER_LIST} contents, got {len(items)}"

    contents = []
    for item in items:
        emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
        company = item.get("company", "")
        title = item.get("title", "")
        summary = item.get("summary", "")
        link_url = item.get("link", header_link_url)

        list_title = f"{emoji} [{company}] {title}"
        if len(list_title) > 100:
            list_title = list_title[:97] + "..."

        contents.append({
            "title": list_title,
            "description": summary,
            "image_url": DEFAULT_IMAGE_URL,
            "link": {
                "web_url": link_url,
                "mobile_web_url": link_url,
            },
        })

    return {
        "object_type": "list",
        "header_title": header_title,
        "header_link": {
            "web_url": header_link_url,
            "mobile_web_url": header_link_url,
        },
        "contents": contents,
        "buttons": [
            {
                "title": "네이버 뉴스 더보기",
                "link": {
                    "web_url": header_link_url,
                    "mobile_web_url": header_link_url,
                },
            }
        ],
    }


def _build_feed_template(item: dict, header_title: str) -> dict:
    """Fallback to feed template when there's only 1 item."""
    emoji = SENTIMENT_EMOJI.get(item.get("sentiment", "neutral"), "🟡")
    company = item.get("company", "")
    title = item.get("title", "")
    summary = item.get("summary", "")
    link_url = item.get("link", DEFAULT_HEADER_LINK)

    return {
        "object_type": "feed",
        "content": {
            "title": f"{emoji} [{company}] {title}",
            "description": f"{header_title}\n\n{summary}",
            "image_url": DEFAULT_IMAGE_URL,
            "link": {
                "web_url": link_url,
                "mobile_web_url": link_url,
            },
        },
        "buttons": [
            {
                "title": "기사 보기",
                "link": {
                    "web_url": link_url,
                    "mobile_web_url": link_url,
                },
            }
        ],
    }


def _build_text_template(text: str) -> dict:
    """Used only for empty-day or header announcement."""
    return {
        "object_type": "text",
        "text": text[:200],
        "link": {
            "web_url": DEFAULT_HEADER_LINK,
            "mobile_web_url": DEFAULT_HEADER_LINK,
        },
    }


def _send_template(access_token: str, template: dict) -> dict:
    """Post a single template to Kakao memo API."""
    response = requests.post(
        KAKAO_MEMO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def send_daily_news(
    access_token: str,
    items: list[dict],
    header_title_base: str,
) -> int:
    """Send daily news as list-template messages (auto-split, with feed fallback).

    Returns: total number of messages sent.
    """
    if not items:
        _send_template(access_token, _build_text_template(
            f"{header_title_base}\n금일 보고 대상 신규 기사 없음."
        ))
        return 1

    chunks = split_for_list_template(items)
    sent_count = 0
    total_chunks = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        if total_chunks == 1:
            header = header_title_base
        elif i == 1:
            header = f"{header_title_base} (1/{total_chunks})"
        else:
            header = f"한국투자금융그룹 데일리 뉴스 ({i}/{total_chunks})"

        if len(chunk) == 1:
            template = _build_feed_template(chunk[0], header)
        else:
            template = _build_list_template(chunk, header)

        try:
            _send_template(access_token, template)
            sent_count += 1
        except requests.HTTPError as e:
            print(f"[ERROR] Kakao send failed (msg {i}/{total_chunks}): {e}", flush=True)
            if e.response is not None:
                print(f"[ERROR] Response: {e.response.text}", flush=True)
            raise
        time.sleep(0.4)

    return sent_count


def send_weekly_digest_text(access_token: str, text: str) -> int:
    """Send weekly digest as text-chunk messages (hierarchical content doesn't fit list template)."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 195:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())

    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        if total > 1:
            body = f"({i}/{total}) {chunk}"
            if len(body) > 200:
                body = body[:200]
        else:
            body = chunk
        _send_template(access_token, _build_text_template(body))
        time.sleep(0.4)

    return total
