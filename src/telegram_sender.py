"""Telegram sender for KIH Daily News Bot.

- Uses Telegram Bot API (requires a bot from @BotFather).
- Sends to one or more chat_ids (users, groups, or channels).
- HTML parse_mode for clickable titles.
- 4096 char limit per message; auto-splits if exceeded.

Env vars required when ENABLE_TELEGRAM=true:
    TELEGRAM_BOT_TOKEN  e.g. 123456789:ABC-DEF...
    TELEGRAM_CHAT_IDS   comma-separated chat_ids (e.g. '12345678,-100987654321')

How to get chat_id:
    1. Send any message to your bot first (so the bot can see you).
    2. Visit https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
    3. Look for "chat":{"id": ...} in the JSON response.
    For channels: bot must be added as admin; chat_id is negative (e.g. -1001234567890).
"""
import os
import time
import requests
from html import escape as html_escape


TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_LENGTH = 4000  # safety margin under 4096 hard limit

SENTIMENT_EMOJI = {"negative": "🔴", "neutral": "🟡", "positive": "🟢"}
SENTIMENT_ORDER = {"negative": 0, "neutral": 1, "positive": 2}


def _shorten_company(name: str) -> str:
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
    """Return the bracket tag: '[증권]' for company, '[운용업]' for sector."""
    if item.get("category") == "sector":
        return _shorten_sector(item.get("sector", "") or "")
    return _shorten_company(item.get("company", "") or "")


def _get_chat_ids() -> list[str]:
    raw = os.environ.get("TELEGRAM_CHAT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: (
            SENTIMENT_ORDER.get(x.get("sentiment", "neutral"), 1),
            -x.get("importance", 0),
        ),
    )


def _counts(items: list[dict]) -> dict:
    c = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        c[it.get("sentiment", "neutral")] = c.get(it.get("sentiment", "neutral"), 0) + 1
    return c


def _format_item_html(it: dict) -> str:
    emoji = SENTIMENT_EMOJI.get(it.get("sentiment", "neutral"), "🟡")
    company = _get_item_tag(it)
    title = it.get("title", "")
    summary = it.get("summary", "")
    link = it.get("link", "")

    # Telegram supported HTML: <b><i><u><s><a><code><pre>
    title_safe = html_escape(title)
    company_safe = html_escape(company)
    summary_safe = html_escape(summary)

    if link:
        link_safe = html_escape(link, quote=True)
        title_html = f'<a href="{link_safe}">{title_safe}</a>'
    else:
        title_html = title_safe

    media = it.get("media") or ""
    media_tag = ""
    if media and media != "네이버뉴스":
        media_tag = f' <i>({html_escape(media)})</i>'

    line1 = f"{emoji} <b>[{company_safe}]</b> {title_html}{media_tag}"
    if summary_safe:
        line2 = f"   <i>↳ {summary_safe}</i>"
        return f"{line1}\n{line2}"
    return line1


def _build_messages_daily(items: list[dict], header_title: str) -> list[str]:
    """Build one or more Telegram messages (auto-split if > 4000 chars)."""
    counts = _counts(items)
    sorted_items = _sort_items(items)

    header = (
        f"<b>{html_escape(header_title)}</b>\n"
        f"🔴 {counts['negative']} · 🟡 {counts['neutral']} · 🟢 {counts['positive']} · "
        f"총 {sum(counts.values())}건\n"
    )

    if not sorted_items:
        return [header + "\n금일 보고 대상 신규 기사 없음."]

    messages = []
    current = header + "\n"
    for it in sorted_items:
        block = _format_item_html(it) + "\n\n"
        if len(current) + len(block) > TELEGRAM_MAX_LENGTH:
            messages.append(current.rstrip())
            # Subsequent messages get a small continuation header
            current = f"<b>{html_escape(header_title)} (계속)</b>\n\n"
        current += block

    if current.strip():
        messages.append(current.rstrip())

    # Mark with (i/n) if multiple
    if len(messages) > 1:
        total = len(messages)
        messages = [
            f"<i>({i}/{total})</i>\n{m}" for i, m in enumerate(messages, 1)
        ]
    return messages


def _build_messages_weekly(digest: dict, period_label: str) -> list[str]:
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    header = (
        f"<b>📊 한국투자금융그룹 금주 종합</b>\n"
        f"<i>{html_escape(period_label)}</i>\n\n"
        f"<b>▶ 회사별 주요 이슈</b>\n"
    )

    by_company = digest.get("by_company", {}) or {}
    keywords = digest.get("keywords", []) or []

    messages = []
    current = header

    if not by_company:
        current += "\n지난 1주간 주요 이슈 없음.\n"
    else:
        for company, issues in by_company.items():
            if not issues:
                continue
            company_block = f"\n<b>[{html_escape(company)}]</b>\n"
            for idx, item in enumerate(issues, 1):
                mark = circled[idx - 1] if idx <= 10 else f"({idx})"
                summary = html_escape(item.get("summary", ""))
                link = item.get("link", "") or ""
                if link:
                    link_safe = html_escape(link, quote=True)
                    company_block += f'  {mark} <a href="{link_safe}">{summary}</a>\n'
                else:
                    company_block += f"  {mark} {summary}\n"

            if len(current) + len(company_block) > TELEGRAM_MAX_LENGTH:
                messages.append(current.rstrip())
                current = "<b>📊 금주 종합 (계속)</b>\n"
            current += company_block

    if keywords:
        kw_block = "\n<b>▶ 주간 핵심 키워드</b>\n" + " ".join(
            f"<code>#{html_escape(k)}</code>" for k in keywords
        )
        if len(current) + len(kw_block) > TELEGRAM_MAX_LENGTH:
            messages.append(current.rstrip())
            current = "<b>📊 금주 종합 (계속)</b>\n"
        current += kw_block

    if current.strip():
        messages.append(current.rstrip())

    if len(messages) > 1:
        total = len(messages)
        messages = [f"<i>({i}/{total})</i>\n{m}" for i, m in enumerate(messages, 1)]
    return messages


# --------------------------------------------------------------------------- #
# Transport                                                                    #
# --------------------------------------------------------------------------- #

def _send_message(token: str, chat_id: str, text: str,
                  parse_mode: str = "HTML",
                  disable_preview: bool = True) -> dict:
    url = TELEGRAM_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    response = requests.post(url, json=payload, timeout=15)
    if response.status_code != 200:
        # Telegram returns descriptive error JSON; include it
        try:
            err = response.json()
        except Exception:
            err = response.text
        raise RuntimeError(f"Telegram API error {response.status_code}: {err}")
    return response.json()


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def send_daily_news_telegram(items: list[dict], header_title: str) -> int:
    """Send daily news to all configured chat_ids. Returns total send count."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[ERROR][telegram] TELEGRAM_BOT_TOKEN not set. Skipping.", flush=True)
        return 0

    chat_ids = _get_chat_ids()
    if not chat_ids:
        print("[INFO][telegram] TELEGRAM_CHAT_IDS empty. Skipping.", flush=True)
        return 0

    messages = _build_messages_daily(items, header_title)
    total_per_chat = len(messages)
    print(
        f"[INFO][telegram] Sending {total_per_chat} message(s) × {len(chat_ids)} chat(s).",
        flush=True,
    )

    sent = 0
    for chat_id in chat_ids:
        for i, msg in enumerate(messages, 1):
            try:
                _send_message(token, chat_id, msg)
                sent += 1
            except Exception as e:
                print(
                    f"[ERROR][telegram] send failed (chat={chat_id}, {i}/{total_per_chat}): "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )
                # Don't raise; try other chats/messages
            time.sleep(0.4)  # rate-limit safety (Telegram: max 30 msg/sec global)
    print(f"[INFO][telegram] Total sent: {sent}", flush=True)
    return sent


def send_sector_news_telegram(items: list[dict], header_title: str) -> int:
    """Send sector news. Same logic as daily, header text differs."""
    return send_daily_news_telegram(items, header_title)


def send_weekly_digest_telegram(digest: dict, period_label: str) -> int:
    """Send weekly digest. Returns total send count."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[ERROR][telegram] TELEGRAM_BOT_TOKEN not set. Skipping weekly.", flush=True)
        return 0

    chat_ids = _get_chat_ids()
    if not chat_ids:
        print("[INFO][telegram] TELEGRAM_CHAT_IDS empty. Skipping weekly.", flush=True)
        return 0

    messages = _build_messages_weekly(digest, period_label)
    total_per_chat = len(messages)

    sent = 0
    for chat_id in chat_ids:
        for i, msg in enumerate(messages, 1):
            try:
                _send_message(token, chat_id, msg)
                sent += 1
            except Exception as e:
                print(
                    f"[ERROR][telegram] weekly send failed (chat={chat_id}, {i}/{total_per_chat}): "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )
            time.sleep(0.4)
    print(f"[INFO][telegram] Weekly total sent: {sent}", flush=True)
    return sent
