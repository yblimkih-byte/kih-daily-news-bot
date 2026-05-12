"""Main entry point for KIH Daily News Bot.

Sends 4 times per day in KST: 07:40, 09:30, 14:00, 17:30.
Each run fetches only news from since the previous run to avoid duplicates.
On Fridays at 07:40, additionally sends a weekly digest.
If there are no qualified items, the run is skipped silently (no message sent).
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from token_manager import refresh_kakao_access_token
from naver_news import fetch_recent_news
from ai_processor import process_daily_news, process_weekly_digest
from kakao_sender import send_daily_news, send_weekly_digest_text


KST = timezone(timedelta(hours=9))
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

# Send slots in KST: (hour, minute) -> hours_back covers from previous slot + safety margin.
# Slot rotation: 17:30 (prev) -> 07:40 -> 09:30 -> 14:00 -> 17:30
# The 07:40 slot covers overnight (~14h10m) since the 17:30 slot of the prev day.
SLOT_CONFIG = [
    # (hour, minute, hours_back, label)
    (7, 40, 14.5, "morning"),       # since prev 17:30 (14h10m + 20m margin)
    (9, 30, 2.0, "pre_open"),       # since 07:40 (1h50m + 10m margin)
    (14, 0, 4.7, "midday"),         # since 09:30 (4h30m + 12m margin)
    (17, 30, 3.7, "close"),         # since 14:00 (3h30m + 12m margin)
]


def determine_slot(now: datetime) -> dict | None:
    """Find which configured slot this run falls into.

    Matches within +/- 25 minutes of a slot time. GitHub Actions cron has
    irregular delay, so a wide window is needed. If no slot matches (e.g. manual
    workflow_dispatch at noon), defaults to a 4-hour window.
    """
    for hour, minute, hours_back, label in SLOT_CONFIG:
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_min = abs((now - slot_dt).total_seconds()) / 60
        if delta_min <= 25:
            return {
                "label": label,
                "hours_back": hours_back,
                "is_morning": label == "morning",
            }
    # Fallback for manual runs outside any slot
    return {
        "label": "manual",
        "hours_back": 4.0,
        "is_morning": False,
    }


def build_header_title(items: list[dict], now: datetime, slot_label: str) -> str:
    weekday = WEEKDAYS_KR[now.weekday()]
    counts = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        counts[it.get("sentiment", "neutral")] += 1
    time_str = now.strftime("%H:%M")
    return (
        f"📅 {now.strftime('%Y-%m-%d')} ({weekday}) {time_str} "
        f"🔴{counts['negative']} 🟡{counts['neutral']} 🟢{counts['positive']}"
    )


def format_weekly_digest_text(digest: dict, now: datetime) -> str:
    week_ago = now - timedelta(days=7)
    period = f"{week_ago.strftime('%m/%d')} ~ {now.strftime('%m/%d')}"

    msg = f"📊 [{now.strftime('%Y-%m-%d')} 금] 한국투자금융그룹 주간 종합 ({period})\n"
    msg += "━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "▶ 회사별 주요 이슈\n\n"

    by_company = digest.get("by_company", {})
    if not by_company:
        msg += "지난 1주간 주요 이슈 없음.\n"
    else:
        for company, issues in by_company.items():
            if not issues:
                continue
            msg += f"[{company}]\n"
            for idx, item in enumerate(issues, 1):
                circled = "①②③④⑤⑥⑦⑧⑨⑩"[idx - 1] if idx <= 10 else f"({idx})"
                summary = item.get("summary", "")
                link = item.get("link", "")
                msg += f"  {circled} {summary}\n     → {link}\n"
            msg += "\n"

    keywords = digest.get("keywords", [])
    if keywords:
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        msg += "▶ 주간 핵심 키워드\n"
        msg += " ".join(f"#{kw}" for kw in keywords)

    return msg


def main():
    now = datetime.now(KST)
    slot = determine_slot(now)
    is_friday_morning = now.weekday() == 4 and slot["is_morning"]

    print(f"[INFO] Run started at {now.isoformat()}", flush=True)
    print(f"[INFO] Slot: {slot['label']}, hours_back: {slot['hours_back']}", flush=True)
    print(f"[INFO] is_friday_morning: {is_friday_morning}", flush=True)

    print("[STEP 1] Refreshing Kakao access token...", flush=True)
    token_data = refresh_kakao_access_token()
    access_token = token_data["access_token"]
    print(f"[STEP 1] Access token acquired (expires in {token_data['expires_in']}s).", flush=True)

    print(f"[STEP 2] Fetching news from Naver (window: {slot['hours_back']}h)...", flush=True)
    daily_articles = fetch_recent_news(hours_back=slot["hours_back"])

    if not daily_articles:
        print("[INFO] No articles in window. Skipping send for this slot.", flush=True)
        # Still send Friday weekly digest even if daily window is empty
        if is_friday_morning:
            _send_weekly_digest(access_token, now)
        else:
            print("[INFO] Run completed: nothing to send.", flush=True)
        return

    print("[STEP 3] Processing daily news with Claude...", flush=True)
    daily_items = process_daily_news(daily_articles)
    print(f"[STEP 3] After AI filter: {len(daily_items)} items.", flush=True)

    if not daily_items:
        print("[INFO] AI filter returned 0 items. Skipping send for this slot.", flush=True)
        if is_friday_morning:
            _send_weekly_digest(access_token, now)
        else:
            print("[INFO] Run completed: nothing to send.", flush=True)
        return

    print("[STEP 4] Sending daily news...", flush=True)
    header_title = build_header_title(daily_items, now, slot["label"])
    msgs_sent = send_daily_news(access_token, daily_items, header_title)
    print(f"[STEP 4] Sent {msgs_sent} message(s).", flush=True)

    if is_friday_morning:
        _send_weekly_digest(access_token, now)

    print("[INFO] Run completed successfully.", flush=True)


def _send_weekly_digest(access_token: str, now: datetime) -> None:
    """Send the Friday weekly digest. Independent of daily slot."""
    print("[STEP 5] Fetching weekly news (Friday morning)...", flush=True)
    weekly_articles = fetch_recent_news(hours_back=24 * 7)
    print(f"[STEP 5] Weekly articles collected: {len(weekly_articles)}", flush=True)

    if not weekly_articles:
        print("[INFO] No weekly articles. Skipping weekly digest.", flush=True)
        return

    print("[STEP 5] Generating weekly digest with Claude...", flush=True)
    digest = process_weekly_digest(weekly_articles)
    if not digest.get("by_company"):
        print("[INFO] Weekly digest empty. Skipping send.", flush=True)
        return

    print("[STEP 5] Sending weekly digest (text format)...", flush=True)
    weekly_text = format_weekly_digest_text(digest, now)
    msgs_sent = send_weekly_digest_text(access_token, weekly_text)
    print(f"[STEP 5] Weekly digest sent in {msgs_sent} chunks.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
