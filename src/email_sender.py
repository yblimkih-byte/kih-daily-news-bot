"""Email sender for KIH Daily News Bot via SMTP.

- Uses Python stdlib (smtplib, email) — no additional dependency.
- Sends one email containing all items, formatted in both plain text and HTML.
- Recipients are configured via EMAIL_RECIPIENTS env var (comma-separated).
- All recipients receive the same email (To: line lists everyone).
  If you prefer individual sends or BCC, set EMAIL_DELIVERY_MODE accordingly.

Env vars required when ENABLE_EMAIL=true:
    SMTP_HOST           e.g. smtp.gmail.com
    SMTP_PORT           e.g. 587
    SMTP_USER           sender email account
    SMTP_PASSWORD       app password (Gmail) or account password
    EMAIL_RECIPIENTS    comma-separated recipient list

Optional:
    EMAIL_FROM_NAME     display name in From: header (default: 'KIH News Bot')
    EMAIL_DELIVERY_MODE 'to' (default, all listed in To:)
                        'bcc' (To: sender only, all hidden in Bcc:)
                        'individual' (separate email per recipient)
"""
import os
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from email.header import Header
from html import escape as html_escape


SENTIMENT_EMOJI = {"negative": "🔴", "neutral": "🟡", "positive": "🟢"}
SENTIMENT_LABEL = {"negative": "부정", "neutral": "중립", "positive": "긍정"}
SENTIMENT_COLOR = {"negative": "#d93025", "neutral": "#b88500", "positive": "#1e8e3e"}
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


def _get_recipients() -> list[str]:
    raw = os.environ.get("EMAIL_RECIPIENTS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _subject_from_header(header_title: str, prefix: str = "[KIH News]") -> str:
    """Convert internal header (📅 05-12(화) 07:40 🔴2 🟡5 🟢1) → email subject."""
    cleaned = header_title.replace("📅 ", "").strip()
    return f"{prefix} {cleaned}"


def _counts(items: list[dict]) -> dict:
    c = {"negative": 0, "neutral": 0, "positive": 0}
    for it in items:
        s = it.get("sentiment", "neutral")
        c[s] = c.get(s, 0) + 1
    return c


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: (
            SENTIMENT_ORDER.get(x.get("sentiment", "neutral"), 1),
            -x.get("importance", 0),
        ),
    )


# --------------------------------------------------------------------------- #
# Body builders                                                                #
# --------------------------------------------------------------------------- #

def _build_text_body(items: list[dict], header_title: str) -> str:
    """Plain text fallback."""
    lines = [header_title, ""]
    sorted_items = _sort_items(items)
    for it in sorted_items:
        emoji = SENTIMENT_EMOJI.get(it.get("sentiment", "neutral"), "🟡")
        company = _shorten_company(it.get("company", ""))
        title = it.get("title", "")
        summary = it.get("summary", "")
        link = it.get("link", "")
        media = it.get("media") or ""
        media_tag = f" ({media})" if media and media != "네이버뉴스" else ""
        lines.append(f"{emoji} [{company}] {title}{media_tag}")
        if summary:
            lines.append(f"    → {summary}")
        if link:
            lines.append(f"    {link}")
        lines.append("")
    lines.append("---")
    lines.append("KIH Daily News Bot")
    return "\n".join(lines)


def _build_html_body(items: list[dict], header_title: str) -> str:
    """HTML email body. Mobile-friendly, no external CSS."""
    counts = _counts(items)
    sorted_items = _sort_items(items)

    # Header strip
    header_html = html_escape(header_title)

    rows_html_parts = []
    for it in sorted_items:
        sent = it.get("sentiment", "neutral")
        emoji = SENTIMENT_EMOJI.get(sent, "🟡")
        color = SENTIMENT_COLOR.get(sent, "#666")
        company = _shorten_company(it.get("company", ""))
        title = html_escape(it.get("title", ""))
        summary = html_escape(it.get("summary", ""))
        link = it.get("link", "") or "#"
        link_attr = html_escape(link, quote=True)
        media = it.get("media") or ""
        media_html = (
            f' <span style="color:#888;font-size:12px;">({html_escape(media)})</span>'
            if media and media != "네이버뉴스" else ""
        )
        is_naver = it.get("is_naver", False)
        naver_badge = (
            ' <span style="background:#1ec800;color:#fff;font-size:10px;'
            'padding:1px 5px;border-radius:3px;vertical-align:middle;">N</span>'
            if is_naver else ""
        )

        rows_html_parts.append(f"""
<div style="border-left:4px solid {color};padding:10px 14px;margin:10px 0;background:#fafafa;border-radius:0 4px 4px 0;">
  <div style="font-size:15px;line-height:1.45;margin-bottom:4px;">
    <span style="font-size:16px;">{emoji}</span>
    <span style="color:{color};font-weight:600;">[{html_escape(company)}]</span>
    <a href="{link_attr}" style="color:#1a0dab;text-decoration:none;font-weight:500;">{title}</a>
    {naver_badge}{media_html}
  </div>
  <div style="color:#555;font-size:13px;line-height:1.4;margin-left:2px;">
    {summary}
  </div>
</div>
""")

    items_html = "".join(rows_html_parts) if rows_html_parts else (
        '<div style="color:#888;padding:20px;text-align:center;">금일 보고 대상 신규 기사 없음.</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>KIH Daily News</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;">
<div style="max-width:640px;margin:0 auto;padding:16px;background:#ffffff;">

  <!-- Header -->
  <div style="border-bottom:2px solid #222;padding-bottom:10px;margin-bottom:14px;">
    <div style="font-size:18px;font-weight:700;color:#222;">{header_html}</div>
    <div style="font-size:13px;color:#666;margin-top:6px;">
      🔴 부정 {counts['negative']}건 &nbsp;·&nbsp;
      🟡 중립 {counts['neutral']}건 &nbsp;·&nbsp;
      🟢 긍정 {counts['positive']}건 &nbsp;·&nbsp;
      총 {sum(counts.values())}건
    </div>
  </div>

  <!-- Items -->
  {items_html}

  <!-- Footer -->
  <div style="border-top:1px solid #ddd;margin-top:20px;padding-top:10px;color:#999;font-size:11px;text-align:center;">
    KIH Daily News Bot · 자동 발송 메시지<br>
    수신을 원치 않으시면 관리자에게 문의하세요.
  </div>

</div>
</body>
</html>"""


def _build_weekly_html(digest: dict, period_label: str) -> str:
    by_company = digest.get("by_company", {}) or {}
    keywords = digest.get("keywords", []) or []
    circled = "①②③④⑤⑥⑦⑧⑨⑩"

    company_blocks = []
    for company, issues in by_company.items():
        if not issues:
            continue
        item_lis = []
        for idx, item in enumerate(issues, 1):
            mark = circled[idx - 1] if idx <= 10 else f"({idx})"
            summary = html_escape(item.get("summary", ""))
            link = item.get("link", "") or "#"
            link_attr = html_escape(link, quote=True)
            item_lis.append(
                f'<li style="margin:6px 0;line-height:1.45;">'
                f'<span style="color:#1e8e3e;font-weight:600;">{mark}</span> '
                f'<a href="{link_attr}" style="color:#1a0dab;text-decoration:none;">{summary}</a>'
                f'</li>'
            )
        company_blocks.append(
            f'<div style="margin:14px 0;padding:10px 14px;background:#f7f7f7;border-radius:4px;">'
            f'<div style="font-weight:700;color:#222;margin-bottom:6px;">[{html_escape(company)}]</div>'
            f'<ul style="margin:0;padding-left:18px;font-size:14px;">{"".join(item_lis)}</ul>'
            f'</div>'
        )

    keyword_html = " ".join(
        f'<span style="display:inline-block;background:#e8f0fe;color:#1967d2;padding:3px 8px;border-radius:12px;margin:2px;font-size:12px;">#{html_escape(k)}</span>'
        for k in keywords
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;">
<div style="max-width:640px;margin:0 auto;padding:16px;background:#ffffff;">
  <div style="border-bottom:2px solid #1967d2;padding-bottom:10px;margin-bottom:14px;">
    <div style="font-size:18px;font-weight:700;color:#1967d2;">📊 한국투자금융그룹 주간 종합</div>
    <div style="font-size:13px;color:#666;margin-top:4px;">{html_escape(period_label)}</div>
  </div>

  <div style="font-size:15px;font-weight:600;margin:14px 0 6px;color:#222;">▶ 회사별 주요 이슈</div>
  {"".join(company_blocks) if company_blocks else '<div style="color:#888;padding:14px;">지난 1주간 주요 이슈 없음.</div>'}

  <div style="font-size:15px;font-weight:600;margin:18px 0 8px;color:#222;">▶ 주간 핵심 키워드</div>
  <div>{keyword_html if keyword_html else '<span style="color:#888;">N/A</span>'}</div>

  <div style="border-top:1px solid #ddd;margin-top:20px;padding-top:10px;color:#999;font-size:11px;text-align:center;">
    KIH Daily News Bot · 자동 발송 메시지
  </div>
</div>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# SMTP transport                                                               #
# --------------------------------------------------------------------------- #

def _smtp_send(msg: MIMEMultipart, smtp_user: str, smtp_password: str,
               recipients: list[str]) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    context = ssl.create_default_context()
    if port == 465:
        # SSL on connect
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
    else:
        # STARTTLS (587, 25)
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())


def _build_message(subject: str, text_body: str, html_body: str,
                   smtp_user: str, from_name: str,
                   to_list: list[str], bcc_list: list[str] | None = None) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), smtp_user))
    msg["To"] = ", ".join(to_list)
    if bcc_list:
        # Note: SMTP-level recipients include Bcc, but Bcc header is intentionally
        # NOT set on the message itself (that would expose recipients).
        pass
    msg["Message-ID"] = make_msgid(domain=smtp_user.split("@")[-1] if "@" in smtp_user else "kih-bot")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def send_daily_news_email(items: list[dict], header_title: str) -> int:
    """Send daily news to all configured recipients. Returns number of sends."""
    recipients = _get_recipients()
    if not recipients:
        print("[INFO][email] EMAIL_RECIPIENTS empty. Skipping email send.", flush=True)
        return 0

    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_user or not smtp_password:
        print("[ERROR][email] SMTP_USER or SMTP_PASSWORD not set. Skipping.", flush=True)
        return 0

    from_name = os.environ.get("EMAIL_FROM_NAME", "KIH News Bot")
    mode = os.environ.get("EMAIL_DELIVERY_MODE", "to").lower()

    subject = _subject_from_header(header_title)
    text_body = _build_text_body(items, header_title)
    html_body = _build_html_body(items, header_title)

    sent = 0
    try:
        if mode == "individual":
            for r in recipients:
                msg = _build_message(subject, text_body, html_body,
                                     smtp_user, from_name, [r])
                _smtp_send(msg, smtp_user, smtp_password, [r])
                sent += 1
                time.sleep(0.3)
        elif mode == "bcc":
            # To: sender; Bcc: everyone (hidden)
            msg = _build_message(subject, text_body, html_body,
                                 smtp_user, from_name, [smtp_user])
            # SMTP-level recipients include Bcc
            _smtp_send(msg, smtp_user, smtp_password, [smtp_user] + recipients)
            sent = 1
        else:
            # 'to': all listed openly
            msg = _build_message(subject, text_body, html_body,
                                 smtp_user, from_name, recipients)
            _smtp_send(msg, smtp_user, smtp_password, recipients)
            sent = 1
        print(f"[INFO][email] Sent daily news (mode={mode}, recipients={len(recipients)}, sends={sent}).", flush=True)
    except Exception as e:
        print(f"[ERROR][email] Daily send failed: {type(e).__name__}: {e}", flush=True)
        raise
    return sent


def send_weekly_digest_email(digest: dict, period_label: str) -> int:
    """Send weekly digest (Friday morning)."""
    recipients = _get_recipients()
    if not recipients:
        print("[INFO][email] EMAIL_RECIPIENTS empty. Skipping weekly email.", flush=True)
        return 0

    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_user or not smtp_password:
        print("[ERROR][email] SMTP creds missing for weekly digest. Skipping.", flush=True)
        return 0

    from_name = os.environ.get("EMAIL_FROM_NAME", "KIH News Bot")
    mode = os.environ.get("EMAIL_DELIVERY_MODE", "to").lower()

    subject = f"[KIH News 주간 종합] {period_label}"

    # Plain text version
    text_lines = [f"📊 한국투자금융그룹 주간 종합 ({period_label})", ""]
    for company, issues in (digest.get("by_company") or {}).items():
        if not issues:
            continue
        text_lines.append(f"[{company}]")
        for i, item in enumerate(issues, 1):
            text_lines.append(f"  {i}. {item.get('summary', '')}")
            link = item.get("link", "")
            if link:
                text_lines.append(f"     {link}")
        text_lines.append("")
    keywords = digest.get("keywords") or []
    if keywords:
        text_lines.append("주간 핵심 키워드:")
        text_lines.append(" ".join(f"#{k}" for k in keywords))
    text_body = "\n".join(text_lines)
    html_body = _build_weekly_html(digest, period_label)

    try:
        if mode == "individual":
            sent = 0
            for r in recipients:
                msg = _build_message(subject, text_body, html_body, smtp_user, from_name, [r])
                _smtp_send(msg, smtp_user, smtp_password, [r])
                sent += 1
                time.sleep(0.3)
        elif mode == "bcc":
            msg = _build_message(subject, text_body, html_body, smtp_user, from_name, [smtp_user])
            _smtp_send(msg, smtp_user, smtp_password, [smtp_user] + recipients)
            sent = 1
        else:
            msg = _build_message(subject, text_body, html_body, smtp_user, from_name, recipients)
            _smtp_send(msg, smtp_user, smtp_password, recipients)
            sent = 1
        print(f"[INFO][email] Weekly digest sent (mode={mode}, recipients={len(recipients)}).", flush=True)
        return sent
    except Exception as e:
        print(f"[ERROR][email] Weekly send failed: {type(e).__name__}: {e}", flush=True)
        raise
