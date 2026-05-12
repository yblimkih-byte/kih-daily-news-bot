"""Main entry point for KIH Daily News Bot — multi-channel version.

Sends to one or more channels (Kakao 'memo' / Email / Telegram) at 4 KST slots:
    07:40 / 09:10 / 13:30 / 17:00

Channel toggles via env vars (default true for Kakao, false for the others):
    ENABLE_KAKAO     'true'/'false' (default true)
    ENABLE_EMAIL     'true'/'false' (default false)
    ENABLE_TELEGRAM  'true'/'false' (default false)

Each channel is independent: failure in one does not block the others.
Same news collection + AI filter is shared across all channels.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from naver_news import fetch_recent_news
from ai_processor import process_daily_news, process_weekly_digest


KST = timezone(timedelta(hours=9))
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

# Lock file path (relative to repo root). Updated each successful run.
LOCK_FILE = Path(__file__).parent.parent / ".last_send.json"

SLOT_CONFIG = [
    (7, 40, 14.8, "morning"),
    (9, 10, 1.6, "pre_open"),
    (13, 30, 4.5, "midday"),
    (17, 0, 3.7, "close"),
]


# --------------------------------------------------------------------------- #
# Channel toggles                                                              #
# --------------------------------------------------------------------------- #

def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on", "y", "t")


ENABLE_KAKAO = _flag("ENABLE_KAKAO", True)
ENABLE_EMAIL = _flag("ENABLE_EMAIL", False)
ENABLE_TELEGRAM = _flag("ENABLE_TELEGRAM", False)


# --------------------------------------------------------------------------- #
# Slot / lock helpers                                                          #
# --------------------------------------------------------------------------- #

def determine_slot(now: datetime) -> dict:
    for hour, minute, hours_back, label in SLOT_CONFIG:
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_min = abs((now - slot_dt).total_seconds()) / 60
        if delta_min <= 40:
            return {
                "label": label,
                "hours_back": hours_back,
                "is_morning": label == "morning",
            }
    return {"label": "manual", "hours_back": 4.0, "is_morning": False}


def is_duplicate_trigger(slot_label: str, now: datetime) -> bool:
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
    last_date = data.get("date")
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
    return (
        f"📅 {now.strftime('%m-%d')}({weekday}) {now.strftime('%H:%M')} "
        f"🔴{counts['negative']} 🟡{counts['neutral']} 🟢{counts['positive']}"
    )


def format_weekly_digest_text(digest, now):
    """Plain-text weekly digest (used by Kakao only)."""
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


def weekly_period_label(now: datetime) -> str:
    week_ago = now - timedelta(days=7)
    return f"{week_ago.strftime('%m/%d')} ~ {now.strftime('%m/%d')}"


# --------------------------------------------------------------------------- #
# Channel dispatchers                                                          #
# --------------------------------------------------------------------------- #

def _dispatch_kakao_daily(daily_items, header_title) -> int:
    """Returns number of messages sent (0 if disabled or no items)."""
    if not ENABLE_KAKAO:
        return 0
    if not daily_items:
        return 0
    try:
        from token_manager import refresh_kakao_access_token
        from kakao_sender import send_daily_news
        print("[CH:KAKAO] Refreshing Kakao access token...", flush=True)
        token_data = refresh_kakao_access_token()
        access_token = token_data["access_token"]
        print(f"[CH:KAKAO] Token acquired (expires in {token_data['expires_in']}s).", flush=True)
        msgs = send_daily_news(access_token, daily_items, header_title)
        print(f"[CH:KAKAO] Sent {msgs} message(s).", flush=True)
        return msgs
    except Exception as e:
        print(f"[CH:KAKAO][ERROR] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


def _dispatch_kakao_weekly(now: datetime) -> int:
    if not ENABLE_KAKAO:
        return 0
    try:
        from token_manager import refresh_kakao_access_token
        from kakao_sender import send_weekly_digest_text
        print("[CH:KAKAO][weekly] Fetching weekly news...", flush=True)
        weekly_articles = fetch_recent_news(hours_back=24 * 7)
        if not weekly_articles:
            print("[CH:KAKAO][weekly] No articles. Skipping.", flush=True)
            return 0
        digest = process_weekly_digest(weekly_articles)
        if not digest.get("by_company"):
            print("[CH:KAKAO][weekly] Digest empty. Skipping.", flush=True)
            return 0
        token_data = refresh_kakao_access_token()
        weekly_text = format_weekly_digest_text(digest, now)
        msgs = send_weekly_digest_text(token_data["access_token"], weekly_text)
        print(f"[CH:KAKAO][weekly] Sent in {msgs} chunks.", flush=True)
        return msgs
    except Exception as e:
        print(f"[CH:KAKAO][weekly][ERROR] {type(e).__name__}: {e}", flush=True)
        return 0


def _dispatch_email_daily(daily_items, header_title) -> int:
    if not ENABLE_EMAIL:
        return 0
    if not daily_items:
        return 0
    try:
        from email_sender import send_daily_news_email
        return send_daily_news_email(daily_items, header_title)
    except Exception as e:
        print(f"[CH:EMAIL][ERROR] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


def _dispatch_email_weekly(digest: dict, now: datetime) -> int:
    if not ENABLE_EMAIL:
        return 0
    if not digest.get("by_company"):
        return 0
    try:
        from email_sender import send_weekly_digest_email
        return send_weekly_digest_email(digest, weekly_period_label(now))
    except Exception as e:
        print(f"[CH:EMAIL][weekly][ERROR] {type(e).__name__}: {e}", flush=True)
        return 0


def _dispatch_telegram_daily(daily_items, header_title) -> int:
    if not ENABLE_TELEGRAM:
        return 0
    if not daily_items:
        return 0
    try:
        from telegram_sender import send_daily_news_telegram
        return send_daily_news_telegram(daily_items, header_title)
    except Exception as e:
        print(f"[CH:TG][ERROR] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


def _dispatch_telegram_weekly(digest: dict, now: datetime) -> int:
    if not ENABLE_TELEGRAM:
        return 0
    if not digest.get("by_company"):
        return 0
    try:
        from telegram_sender import send_weekly_digest_telegram
        return send_weekly_digest_telegram(digest, weekly_period_label(now))
    except Exception as e:
        print(f"[CH:TG][weekly][ERROR] {type(e).__name__}: {e}", flush=True)
        return 0


# --------------------------------------------------------------------------- #
# Main pipeline                                                                #
# --------------------------------------------------------------------------- #

def main():
    now = datetime.now(KST)
    slot = determine_slot(now)
    is_friday_morning = now.weekday() == 4 and slot["is_morning"]

    print(f"[INFO] Run started at {now.isoformat()}", flush=True)
    print(f"[INFO] Slot: {slot['label']}, hours_back: {slot['hours_back']}", flush=True)
    print(f"[INFO] Channels: KAKAO={ENABLE_KAKAO}, EMAIL={ENABLE_EMAIL}, TELEGRAM={ENABLE_TELEGRAM}", flush=True)
    print(f"[INFO] is_friday_morning: {is_friday_morning}", flush=True)

    if not (ENABLE_KAKAO or ENABLE_EMAIL or ENABLE_TELEGRAM):
        print("[FATAL] No channel enabled. Set at least one of ENABLE_KAKAO/EMAIL/TELEGRAM.", flush=True)
        return

    if is_duplicate_trigger(slot["label"], now):
        print("[INFO] Skipping duplicate trigger.", flush=True)
        return

    # 1. Collect news
    print(f"[STEP 1] Fetching news from Naver (window: {slot['hours_back']}h)...", flush=True)
    daily_articles = fetch_recent_news(hours_back=slot["hours_back"])

    # 2. AI filter (shared across all channels)
    daily_items = []
    if daily_articles:
        print("[STEP 2] Processing daily news with Claude...", flush=True)
        daily_items = process_daily_news(daily_articles)
        print(f"[STEP 2] After AI filter: {len(daily_items)} items.", flush=True)
    else:
        print("[INFO] No articles in window.", flush=True)

    header_title = build_header_title(daily_items, now, slot["label"])

    # 3. Dispatch daily to each enabled channel
    sent_summary = {"kakao": 0, "email": 0, "telegram": 0}
    if daily_items:
        print("[STEP 3] Dispatching to channels...", flush=True)
        sent_summary["kakao"] = _dispatch_kakao_daily(daily_items, header_title)
        sent_summary["email"] = _dispatch_email_daily(daily_items, header_title)
        sent_summary["telegram"] = _dispatch_telegram_daily(daily_items, header_title)
    else:
        print("[INFO] No items to dispatch.", flush=True)

    # 4. Friday weekly digest (independent fetch — runs even if daily is empty)
    if is_friday_morning:
        print("[STEP 4] Friday weekly digest...", flush=True)
        # Reuse a single AI call across email + telegram; Kakao has its own path
        # because it pre-builds text. For efficiency, we fetch+process once and
        # share the digest result.
        weekly_articles = fetch_recent_news(hours_back=24 * 7)
        digest = {}
        if weekly_articles:
            digest = process_weekly_digest(weekly_articles)

        # Kakao uses its own text builder
        weekly_summary = {"kakao": 0, "email": 0, "telegram": 0}
        if ENABLE_KAKAO:
            weekly_summary["kakao"] = _dispatch_kakao_weekly(now)
        if digest.get("by_company"):
            weekly_summary["email"] = _dispatch_email_weekly(digest, now)
            weekly_summary["telegram"] = _dispatch_telegram_weekly(digest, now)
        print(f"[STEP 4] Weekly summary: {weekly_summary}", flush=True)

    # 5. Always update lock file on a matched slot
    if slot["label"] != "manual":
        update_lock_file(slot["label"], now)

    print(f"[INFO] Run completed. Daily sent: {sent_summary}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
