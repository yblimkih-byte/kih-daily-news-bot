"""Main entry point for KIH Daily News Bot.

Sends 4 times per day in KST: 07:40, 09:30, 14:00, 17:30.
Each slot has 3 redundant cron triggers (5min apart) for reliability.
Duplicate-trigger guard: a lock file in the repo tracks last-sent slot+date.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from token_manager import refresh_kakao_access_token
from naver_news import fetch_recent_news
from ai_processor import process_daily_news, process_weekly_digest
from kakao_sender import send_daily_news, send_weekly_digest_text


KST = timezone(timedelta(hours=9))
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

# Lock file path (relative to repo root). Updated each successful send.
LOCK_FILE = Path(__file__).parent.parent / ".last_send.json"

SLOT_CONFIG = [
    (7, 40, 14.5, "morning"),
    (9, 30, 2.0, "pre_open"),
    (14, 0, 4.7, "midday"),
    (17, 30, 3.7, "close"),
]


def determine_slot(now: datetime) -> dict | None:
    for hour, minute, hours_back, label in SLOT_CONFIG:
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_min = abs((now - slot_dt).total_seconds()) / 60
        if delta_min <= 25:
            return {
                "label": label,
                "hours_back": hours_back,
                "is_morning": label == "morning",
            }
    return {
        "label": "manual",
        "hours_back": 4.0,
        "is_morning": False,
    }


def is_duplicate_trigger(slot_label: str, now: datetime) -> bool:
    """Return True if this same slot already sent successfully today.

    Manual runs (slot=='manual') always proceed.
    """
    if slot_label == "manual":
        return False
    if not LOCK_FILE.exists():
        return False
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Failed to read lock file: {e}", flush=True)
        return False
    last_slot = data.get("slot")
    last_date = data.get("date")  # YYYY-MM-DD KST
    today = now.strftime("%Y-%m-%d")
    if last_slot == slot_label and last_date == today:
        print(
            f"[INFO] Duplicate trigger for slot='{slot_label}' on {today}. "
            f"Lock file shows last send was at {data.get('time')}.",
            flush=True,
        )
        return True
    return False


def update_lock_file(slot_label: str, now: datetime) -> None:
    """Write lock file with this slot's send info."""
    data = {
        "slot": slot_label,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    try:
        LOCK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[INFO] Lock file updated: {data}", flush=True)
    except Exception as e:
        print(f"[WARN] Failed to write lock file: {e}", flush=True)


def build_header_title(items, now, slot_label):
    weekday = WEEKDAYS_KR[now.weekday()]
    counts = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        counts[it.get("sentiment", "neutral")] += 1
    # 형식: "05-12(화) 16:38 🔴3 🟡10 🟢2"
    return (
        f"📅 {now.strftime('%m-%d')}({weekday}) {now.strftime('%H:%M')} "
        f"🔴{counts['negative']} 🟡{counts['neutral']} 🟢{counts['positive']}"
    )


def format_weekly_digest_text(digest, now):
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

    # Duplicate-trigger guard
    if is_duplicate_trigger(slot["label"], now):
        print("[INFO] Skipping duplicate trigger. Exiting.", flush=True)
        return

    print("[STEP 1] Refreshing Kakao access token...", flush=True)
    token_data = refresh_kakao_access_token()
    access_token = token_data["access_token"]
    print(f"[STEP 1] Access token acquired (expires in {token_data['expires_in']}s).", flush=True)

    print(f"[STEP 2] Fetching news from Naver (window: {slot['hours_back']}h)...", flush=True)
    daily_articles = fetch_recent_news(hours_back=slot["hours_back"])

    sent_anything = False

    if daily_articles:
        print("[STEP 3] Processing daily news with Claude...", flush=True)
        daily_items = process_daily_news(daily_articles)
        print(f"[STEP 3] After AI filter: {len(daily_items)} items.", flush=True)

        if daily_items:
            print("[STEP 4] Sending daily news...", flush=True)
            header_title = build_header_title(daily_items, now, slot["label"])
            msgs_sent = send_daily_news(access_token, daily_items, header_title)
            print(f"[STEP 4] Sent {msgs_sent} message(s).", flush=True)
            sent_anything = True
        else:
            print("[INFO] AI filter returned 0 items. No daily message.", flush=True)
    else:
        print("[INFO] No articles in window. No daily message.", flush=True)

    if is_friday_morning:
        if _send_weekly_digest(access_token, now):
            sent_anything = True

    # Always update lock file when slot matched (even if 0 messages sent),
    # so duplicate cron triggers within the same slot don't re-run the pipeline.
    if slot["label"] != "manual":
        update_lock_file(slot["label"], now)

    print(f"[INFO] Run completed. sent_anything={sent_anything}", flush=True)


def _send_weekly_digest(access_token: str, now: datetime) -> bool:
    """Returns True if at least one weekly message was sent."""
    print("[STEP 5] Fetching weekly news (Friday morning)...", flush=True)
    weekly_articles = fetch_recent_news(hours_back=24 * 7)
    print(f"[STEP 5] Weekly articles collected: {len(weekly_articles)}", flush=True)
    if not weekly_articles:
        print("[INFO] No weekly articles. Skipping weekly digest.", flush=True)
        return False

    print("[STEP 5] Generating weekly digest with Claude...", flush=True)
    digest = process_weekly_digest(weekly_articles)
    if not digest.get("by_company"):
        print("[INFO] Weekly digest empty. Skipping send.", flush=True)
        return False

    print("[STEP 5] Sending weekly digest (text format)...", flush=True)
    weekly_text = format_weekly_digest_text(digest, now)
    msgs_sent = send_weekly_digest_text(access_token, weekly_text)
    print(f"[STEP 5] Weekly digest sent in {msgs_sent} chunks.", flush=True)
    return True


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
