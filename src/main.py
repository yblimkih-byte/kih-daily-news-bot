"""Main entry point for KIH Daily News Bot — multi-channel + cross-slot dedupe.

Behavior:
- Weekdays (Mon-Fri, non-holiday): send at all 4 slots (07:40/09:10/13:30/17:00 KST)
- Weekends (Sat-Sun) & Korean public holidays: send only at the 17:00 'close' slot
  - Other slots still trigger via cron, but exit early with no send
- Manual triggers always proceed regardless of weekday/holiday

Channel toggles (env vars, default true for Kakao, false for the others):
    ENABLE_KAKAO     'true'/'false' (default true)
    ENABLE_EMAIL     'true'/'false' (default false)
    ENABLE_TELEGRAM  'true'/'false' (default false)

Cross-slot dedupe:
    Articles already sent in the last SENT_HISTORY_DAYS (default 3) days are
    filtered out before AI processing.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import holidays

from naver_news import fetch_recent_news
from ai_processor import process_daily_news, process_sector_news, process_weekly_digest
from sent_history import filter_unseen, record_sent


KST = timezone(timedelta(hours=9))
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

LOCK_FILE = Path(__file__).parent.parent / ".last_send.json"

SLOT_CONFIG = [
    (7, 40, 14.8, "morning"),
    (9, 10, 1.6, "pre_open"),
    (13, 30, 4.5, "midday"),
    (17, 0, 3.7, "close"),
]

# On weekends/holidays, only this slot label sends. Others skip.
OFF_DAY_SEND_SLOT = "close"

# Korean public holidays (incl. substitute holidays). The library auto-updates yearly.
_KR_HOLIDAYS = holidays.country_holidays("KR")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on", "y", "t")


ENABLE_KAKAO = _flag("ENABLE_KAKAO", True)
ENABLE_EMAIL = _flag("ENABLE_EMAIL", False)
ENABLE_TELEGRAM = _flag("ENABLE_TELEGRAM", False)


# --------------------------------------------------------------------------- #
# Slot / off-day / lock helpers                                                #
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


def is_off_day(now: datetime) -> tuple[bool, str]:
    """Return (is_off, reason_string). Off-day = weekend or Korean holiday."""
    wd = now.weekday()
    if wd == 5:
        return True, "토요일"
    if wd == 6:
        return True, "일요일"
    d = now.date()
    if d in _KR_HOLIDAYS:
        name = _KR_HOLIDAYS.get(d, "공휴일")
        return True, f"공휴일({name})"
    return False, ""


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
    week_ago = now - timedelta(days=7)
    period = f"{week_ago.strftime('%m/%d')} ~ {now.strftime('%m/%d')}"

    msg = f"📊 [{now.strftime('%Y-%m-%d')} 금] 한국투자금융그룹 금주 종합 ({period})\n"
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
# Channel dispatchers (each isolated by try/except)                            #
# --------------------------------------------------------------------------- #

def _dispatch_kakao_daily(daily_items, header_title) -> int:
    if not ENABLE_KAKAO or not daily_items:
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


def _dispatch_kakao_sector(sector_items, header_title) -> int:
    if not ENABLE_KAKAO or not sector_items:
        return 0
    try:
        from token_manager import refresh_kakao_access_token
        from kakao_sender import send_sector_news
        print("[CH:KAKAO][sector] Refreshing token...", flush=True)
        token_data = refresh_kakao_access_token()
        access_token = token_data["access_token"]
        msgs = send_sector_news(access_token, sector_items, header_title)
        print(f"[CH:KAKAO][sector] Sent {msgs} message(s).", flush=True)
        return msgs
    except Exception as e:
        print(f"[CH:KAKAO][sector][ERROR] {type(e).__name__}: {e}", flush=True)
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
        # 주간 종합은 회사별 이슈만 다루므로 업권 기사 제외.
        company_weekly = _company_only(weekly_articles)
        print(f"[CH:KAKAO][weekly] Filtered to {len(company_weekly)} company articles "
              f"(from {len(weekly_articles)} total).", flush=True)
        if not company_weekly:
            print("[CH:KAKAO][weekly] No company articles. Skipping.", flush=True)
            return 0
        digest = process_weekly_digest(company_weekly)
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


def _dispatch_email_unified(daily_items, sector_items, header_title) -> int:
    """이메일은 회사 + 업권을 한 메일에 통합 발송."""
    if not ENABLE_EMAIL:
        return 0
    if not daily_items and not sector_items:
        return 0
    try:
        from email_sender import send_unified_email
        return send_unified_email(daily_items, sector_items, header_title)
    except Exception as e:
        print(f"[CH:EMAIL][unified][ERROR] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


def _dispatch_email_weekly(digest: dict, now: datetime) -> int:
    if not ENABLE_EMAIL or not digest.get("by_company"):
        return 0
    try:
        from email_sender import send_weekly_digest_email
        return send_weekly_digest_email(digest, weekly_period_label(now))
    except Exception as e:
        print(f"[CH:EMAIL][weekly][ERROR] {type(e).__name__}: {e}", flush=True)
        return 0


def _dispatch_telegram_unified(daily_items, sector_items, header_title) -> int:
    """텔레그램은 회사 + 업권을 한 메시지(또는 자동 분할)에 통합 발송."""
    if not ENABLE_TELEGRAM:
        return 0
    if not daily_items and not sector_items:
        return 0
    try:
        from telegram_sender import send_unified_telegram
        return send_unified_telegram(daily_items, sector_items, header_title)
    except Exception as e:
        print(f"[CH:TG][unified][ERROR] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 0


def _dispatch_telegram_weekly(digest: dict, now: datetime) -> int:
    if not ENABLE_TELEGRAM or not digest.get("by_company"):
        return 0
    try:
        from telegram_sender import send_weekly_digest_telegram
        return send_weekly_digest_telegram(digest, weekly_period_label(now))
    except Exception as e:
        print(f"[CH:TG][weekly][ERROR] {type(e).__name__}: {e}", flush=True)
        return 0


def _company_only(articles: list[dict]) -> list[dict]:
    """Filter to company-category articles only.

    Used by weekly digest: 주간 종합은 회사별 이슈 정리가 목적이므로
    업권 거시 뉴스는 입력에서 제외. 토큰 절감 + AI 집중도 향상.
    category 필드 없는 구버전 데이터는 안전하게 회사로 간주.
    """
    return [a for a in articles if a.get("category", "company") == "company"]


def _link_items_to_articles(items: list[dict], articles: list[dict]) -> list[dict]:
    """Re-attach _url_key / _title_key from collected articles back onto AI-filtered items."""
    url_to_keys = {}
    for a in articles:
        link = a.get("link") or ""
        if link:
            url_to_keys[link] = (a.get("_url_key", ""), a.get("_title_key", ""))
    for it in items:
        link = it.get("link") or ""
        u_key, t_key = url_to_keys.get(link, ("", ""))
        if u_key and "_url_key" not in it:
            it["_url_key"] = u_key
        if t_key and "_title_key" not in it:
            it["_title_key"] = t_key
    return items


# --------------------------------------------------------------------------- #
# Main pipeline                                                                #
# --------------------------------------------------------------------------- #

def main():
    now = datetime.now(KST)
    slot = determine_slot(now)
    off, off_reason = is_off_day(now)
    is_friday_morning = (now.weekday() == 4) and slot["is_morning"] and not off

    print(f"[INFO] Run started at {now.isoformat()}", flush=True)
    print(f"[INFO] Slot: {slot['label']}, hours_back: {slot['hours_back']}", flush=True)
    print(f"[INFO] Off-day: {off} ({off_reason or 'N/A'})", flush=True)
    print(f"[INFO] Channels: KAKAO={ENABLE_KAKAO}, EMAIL={ENABLE_EMAIL}, TELEGRAM={ENABLE_TELEGRAM}", flush=True)
    print(f"[INFO] is_friday_morning: {is_friday_morning}", flush=True)

    if not (ENABLE_KAKAO or ENABLE_EMAIL or ENABLE_TELEGRAM):
        print("[FATAL] No channel enabled. Set at least one of ENABLE_KAKAO/EMAIL/TELEGRAM.", flush=True)
        return

    # === Off-day gating ===
    # On weekends and Korean public holidays, send only at the OFF_DAY_SEND_SLOT (default 'close' = 17:00).
    # Manual triggers bypass this rule.
    if off and slot["label"] != "manual" and slot["label"] != OFF_DAY_SEND_SLOT:
        print(
            f"[INFO] Off-day ({off_reason}). Slot '{slot['label']}' is skipped — "
            f"only the '{OFF_DAY_SEND_SLOT}' slot sends on off-days. Exiting.",
            flush=True,
        )
        # Still update lock file so duplicate triggers within the same slot don't re-run.
        update_lock_file(slot["label"], now)
        return

    if is_duplicate_trigger(slot["label"], now):
        print("[INFO] Skipping duplicate trigger.", flush=True)
        return

    # 1. Collect news
    print(f"[STEP 1] Fetching news from Naver (window: {slot['hours_back']}h)...", flush=True)
    daily_articles = fetch_recent_news(hours_back=slot["hours_back"])

    # 2. Cross-slot dedupe
    print("[STEP 2] Cross-slot dedupe (history file)...", flush=True)
    daily_articles, hist_dropped = filter_unseen(daily_articles)
    print(f"[STEP 2] After history dedupe: {len(daily_articles)} articles "
          f"(dropped {hist_dropped} previously-sent).", flush=True)

    # 3. AI filter — 회사 카테고리
    daily_items = []
    if daily_articles:
        print("[STEP 3] Processing daily (company) news with AI...", flush=True)
        daily_items = process_daily_news(daily_articles)
        daily_items = _link_items_to_articles(daily_items, daily_articles)
        print(f"[STEP 3] After AI filter (company): {len(daily_items)} items.", flush=True)
        # 진단: 입력에 회사 기사가 있었는데 AI 결과가 0이면 비정상 가능성 강조
        company_input_count = sum(1 for a in daily_articles if a.get("category", "company") == "company")
        if company_input_count > 0 and len(daily_items) == 0:
            print(f"[WARN][STEP 3] ⚠️ Suspicious: {company_input_count} company articles in input "
                  f"but AI returned 0. 가능 원인: AI 응답 파싱 실패 또는 모든 기사가 단순 홍보로 분류됨. "
                  f"위의 [STEP 3][diag] 로그를 확인하세요.", flush=True)
    else:
        print("[INFO] No fresh articles to process.", flush=True)

    # 3b. AI filter — 업권 카테고리 (별도 AI 호출)
    sector_items = []
    if daily_articles:
        print("[STEP 3b] Processing sector news with AI...", flush=True)
        sector_items = process_sector_news(daily_articles)
        sector_items = _link_items_to_articles(sector_items, daily_articles)
        print(f"[STEP 3b] After AI filter (sector): {len(sector_items)} items.", flush=True)

    header_title = build_header_title(daily_items, now, slot["label"])
    # 카카오용 업권 헤더 (카카오만 분리 발송)
    sector_header_title = (
        f"🏛️ {now.strftime('%m-%d')}({WEEKDAYS_KR[now.weekday()]}) "
        f"{now.strftime('%H:%M')} 업권 뉴스 ({len(sector_items)}건)"
    )

    # 4. Dispatch
    # 카카오: 회사 + 업권을 별도 메시지로 분리 발송 (카카오 200자 한도 때문)
    # 이메일·텔레그램: 회사 + 업권을 한 메시지에 통합 발송
    sent_summary = {"kakao": 0, "email": 0, "telegram": 0}
    sector_sent_summary = {"kakao": 0, "email": 0, "telegram": 0}

    # --- 카카오 (분리 발송 유지) ---
    if daily_items or sector_items:
        print("[STEP 4-kakao] Dispatching to Kakao (separate messages)...", flush=True)
        if daily_items:
            sent_summary["kakao"] = _dispatch_kakao_daily(daily_items, header_title)
        if sector_items:
            sector_sent_summary["kakao"] = _dispatch_kakao_sector(sector_items, sector_header_title)

    # --- 이메일 (통합 발송) ---
    if daily_items or sector_items:
        print("[STEP 4-email] Dispatching to Email (unified message)...", flush=True)
        email_total = _dispatch_email_unified(daily_items, sector_items, header_title)
        # 통합 발송은 한 번이므로 카운트 분할은 의미상 daily/sector 합산.
        # sent_summary에는 회사 + 업권 합산값을 기록 (sector_sent_summary는 0 유지하여 중복 방지).
        sent_summary["email"] = email_total

    # --- 텔레그램 (통합 발송) ---
    if daily_items or sector_items:
        print("[STEP 4-telegram] Dispatching to Telegram (unified message)...", flush=True)
        telegram_total = _dispatch_telegram_unified(daily_items, sector_items, header_title)
        sent_summary["telegram"] = telegram_total

    if not daily_items and not sector_items:
        print("[INFO] No items (company or sector) to dispatch.", flush=True)

    # 5. Record sent items into history (회사 + 업권 모두)
    any_sent = sum(sent_summary.values()) + sum(sector_sent_summary.values()) > 0
    all_sent_items = daily_items + sector_items
    if any_sent and all_sent_items:
        print("[STEP 5] Recording sent items to history...", flush=True)
        record_sent(all_sent_items)
    else:
        print("[STEP 5] No successful sends. History not updated.", flush=True)

    # 6. Friday weekly digest (skips automatically if Friday is an off-day)
    if is_friday_morning:
        print("[STEP 6] Friday weekly digest...", flush=True)
        weekly_articles = fetch_recent_news(hours_back=24 * 7)
        digest = {}
        if weekly_articles:
            # 주간 종합은 회사별 이슈 정리가 목적이므로 업권 기사 제외 (토큰 절감 + AI 집중).
            company_weekly = _company_only(weekly_articles)
            print(f"[STEP 6] Filtered to {len(company_weekly)} company articles "
                  f"(from {len(weekly_articles)} total).", flush=True)
            if company_weekly:
                digest = process_weekly_digest(company_weekly)
            else:
                print("[STEP 6] No company articles. Skipping digest generation.", flush=True)
        weekly_summary = {"kakao": 0, "email": 0, "telegram": 0}
        if ENABLE_KAKAO:
            weekly_summary["kakao"] = _dispatch_kakao_weekly(now)
        if digest.get("by_company"):
            weekly_summary["email"] = _dispatch_email_weekly(digest, now)
            weekly_summary["telegram"] = _dispatch_telegram_weekly(digest, now)
        print(f"[STEP 6] Weekly summary: {weekly_summary}", flush=True)

    # 7. Update lock file
    if slot["label"] != "manual":
        update_lock_file(slot["label"], now)

    # 채널별 로그:
    # - kakao: 분리 발송이라 sent_summary['kakao'] = 회사 메시지 수, sector_sent_summary['kakao'] = 업권 메시지 수
    # - email/telegram: 통합 발송이라 sent_summary['email'/'telegram'] = 통합 메시지 수 (sector_sent_summary은 0)
    print(
        f"[INFO] Run completed. "
        f"Kakao: company={sent_summary['kakao']}, sector={sector_sent_summary['kakao']} (분리). "
        f"Email: {sent_summary['email']} (통합, 회사 {len(daily_items)} + 업권 {len(sector_items)}). "
        f"Telegram: {sent_summary['telegram']} (통합).",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
