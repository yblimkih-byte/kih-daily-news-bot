"""Main entry point for KIH Daily News Bot.

Run daily at 07:40 KST via GitHub Actions.
On Fridays, additionally sends a weekly digest.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure src module imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from token_manager import refresh_kakao_access_token
from naver_news import fetch_recent_news
from ai_processor import process_daily_news, process_weekly_digest
from kakao_sender import send_long_message


KST = timezone(timedelta(hours=9))

SENTIMENT_EMOJI = {
    "negative": "🔴",
    "neutral": "🟡",
    "positive": "🟢",
}

WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]


def format_daily_message(items: list[dict], now: datetime) -> str:
    """Format daily news into a single text message."""
    weekday = WEEKDAYS_KR[now.weekday()]
    header = f"📅 [{now.strftime('%Y-%m-%d')} ({weekday})] 한국투자금융그룹 데일리 뉴스\n"
    header += "━━━━━━━━━━━━━━━━━━━\n"

    if not items:
        return header + "금일 보고 대상 신규 기사 없음."

    counts = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        counts[it.get("sentiment", "neutral")] += 1
    header += (
        f"🔴 부정 {counts['negative']}건  "
        f"🟡 중립 {counts['neutral']}건  "
        f"🟢 긍정 {counts['positive']}건\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )

    body_lines = []
    for i, it in enumerate(items, 1):
        emoji = SENTIMENT_EMOJI.get(it.get("sentiment", "neutral"), "🟡")
        company = it.get("company", "")
        title = it.get("title", "")
        summary = it.get("summary", "")
        link = it.get("link", "")
        body_lines.append(
            f"{i}. {emoji} [{company}] {title}\n"
            f"   ▸ {summary}\n"
            f"   ▸ {link}\n"
        )

    return header + "\n".join(body_lines)


def format_weekly_digest(digest: dict, now: datetime) -> str:
    """Format weekly digest into a single text message."""
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
    is_friday = now.weekday() == 4

    print(f"[INFO] Run started at {now.isoformat()}", flush=True)
    print(f"[INFO] is_friday: {is_friday}", flush=True)

    # Step 1: Refresh Kakao access token
    print("[STEP 1] Refreshing Kakao access token...", flush=True)
    token_data = refresh_kakao_access_token()
    access_token = token_data["access_token"]
    print(f"[STEP 1] Access token acquired (expires in {token_data['expires_in']}s).", flush=True)

    # Step 2: Fetch news from last 24 hours
    print("[STEP 2] Fetching news from Naver...", flush=True)
    daily_articles = fetch_recent_news(hours_back=24)

    # Step 3: AI process daily news
    print("[STEP 3] Processing daily news with Claude...", flush=True)
    daily_items = process_daily_news(daily_articles)
    print(f"[STEP 3] After AI filter: {len(daily_items)} items.", flush=True)

    # Step 4: Send daily message
    print("[STEP 4] Sending daily message...", flush=True)
    daily_message = format_daily_message(daily_items, now)
    chunks_sent = send_long_message(access_token, daily_message)
    print(f"[STEP 4] Daily message sent in {chunks_sent} chunks.", flush=True)

    # Step 5 (Friday only): Weekly digest
    if is_friday:
        print("[STEP 5] Fetching weekly news (Friday)...", flush=True)
        weekly_articles = fetch_recent_news(hours_back=24 * 7)
        print(f"[STEP 5] Weekly articles collected: {len(weekly_articles)}", flush=True)

        print("[STEP 5] Generating weekly digest with Claude...", flush=True)
        digest = process_weekly_digest(weekly_articles)

        print("[STEP 5] Sending weekly digest...", flush=True)
        weekly_message = format_weekly_digest(digest, now)
        chunks_sent = send_long_message(access_token, weekly_message)
        print(f"[STEP 5] Weekly digest sent in {chunks_sent} chunks.", flush=True)

    print("[INFO] Run completed successfully.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
