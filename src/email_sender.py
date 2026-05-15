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


def _get_recipients() -> list[str]:
    raw = os.environ.get("EMAIL_RECIPIENTS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _subject_from_header(header_title: str, prefix: str = "[KIH News]") -> str:
    """Convert internal header (📅 05-12(화) 07:40 🔴2 🟡5 🟢1) → email subject.

    이모지를 Outlook에서도 안전하게 보이는 텍스트로 변환:
      📅 → 제거
      🔴 N → 부정 N
      🟡 N → 중립 N
      🟢 N → 긍정 N
    """
    cleaned = header_title.replace("📅 ", "").strip()
    # 이모지 → 텍스트 라벨 (제목 줄은 Outlook 받은편지함 목록에 그대로 노출되므로 안전한 표기)
    cleaned = cleaned.replace("🔴", "부정 ")
    cleaned = cleaned.replace("🟡", "중립 ")
    cleaned = cleaned.replace("🟢", "긍정 ")
    # 중복 공백 정리
    cleaned = " ".join(cleaned.split())
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
        company = _get_item_tag(it)
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
    """HTML email body — Outlook 데스크톱 호환 table 기반 구현.

    설계 원칙 (Outlook Word 엔진 호환):
      - div 대신 nested table 사용 (Outlook은 table을 가장 안정적으로 렌더)
      - border-radius / border-left 4px 대신 4px-wide bgcolor td 셀로 색깔 막대 구현
      - 이모지(🔴🟡🟢) 대신 ● (BLACK CIRCLE, U+25CF) + color 사용
      - 폰트는 '맑은 고딕'(Outlook 한글 표준) 우선
      - 모바일에서도 자연스럽게 보이도록 가로폭 max-width 활용
    """
    counts = _counts(items)
    sorted_items = _sort_items(items)
    # 본문 헤더 텍스트: 이모지를 텍스트로 변환 (Outlook 호환). 원본 header_title은
    # 다른 채널에서 사용 중이므로 이메일 본문 안에서만 변환.
    header_clean = header_title.replace("📅", "").replace("🔴", "부정 ")
    header_clean = header_clean.replace("🟡", "중립 ")
    header_clean = header_clean.replace("🟢", "긍정 ")
    header_clean = " ".join(header_clean.split())
    header_html = html_escape(header_clean)

    rows_html_parts = []
    for it in sorted_items:
        sent = it.get("sentiment", "neutral")
        color = SENTIMENT_COLOR.get(sent, "#666666")
        company = html_escape(_get_item_tag(it))
        title = html_escape(it.get("title", ""))
        summary = html_escape(it.get("summary", ""))
        link = it.get("link", "") or "#"
        link_attr = html_escape(link, quote=True)
        media = it.get("media") or ""
        media_html = (
            f' <span style="color:#888888;font-size:12px;">({html_escape(media)})</span>'
            if media and media != "네이버뉴스" else ""
        )
        is_naver = it.get("is_naver", False)
        # N 배지: Outlook에서도 보이도록 table 셀로 만듦
        naver_badge = (
            ' <span style="background-color:#1ec800;color:#ffffff;font-size:10px;'
            'padding:1px 5px;font-weight:bold;">N</span>'
            if is_naver else ""
        )

        # 카드 = 2-column table: 왼쪽 4px 색깔 막대 | 오른쪽 내용
        rows_html_parts.append(f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;margin-bottom:10px;background-color:#fafafa;">
  <tr>
    <td width="4" bgcolor="{color}" style="width:4px;background-color:{color};line-height:0;font-size:0;">&nbsp;</td>
    <td style="padding:10px 14px;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
      <div style="font-size:15px;line-height:1.45;margin-bottom:6px;">
        <span style="color:{color};font-weight:bold;font-size:16px;">●</span>
        <span style="color:{color};font-weight:bold;">[{company}]</span>
        <a href="{link_attr}" style="color:#1a0dab;text-decoration:none;font-weight:600;">{title}</a>
        {naver_badge}{media_html}
      </div>
      <div style="color:#555555;font-size:13px;line-height:1.4;">
        {summary}
      </div>
    </td>
  </tr>
</table>""")

    items_html = "".join(rows_html_parts) if rows_html_parts else (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        '<tr><td align="center" style="color:#888888;padding:20px;font-family:\'맑은 고딕\',Arial,sans-serif;">'
        '금일 보고 대상 신규 기사 없음.</td></tr></table>'
    )

    # 카운트 줄: 이모지 대신 색깔 점 ● 사용 (Outlook 호환)
    neg_color = SENTIMENT_COLOR["negative"]
    neu_color = SENTIMENT_COLOR["neutral"]
    pos_color = SENTIMENT_COLOR["positive"]
    count_line = (
        f'<span style="color:{neg_color};font-weight:bold;">●</span> 부정 {counts["negative"]}건 '
        f'&nbsp;·&nbsp; '
        f'<span style="color:{neu_color};font-weight:bold;">●</span> 중립 {counts["neutral"]}건 '
        f'&nbsp;·&nbsp; '
        f'<span style="color:{pos_color};font-weight:bold;">●</span> 긍정 {counts["positive"]}건 '
        f'&nbsp;·&nbsp; 총 {sum(counts.values())}건'
    )

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width,initial-scale=1.0" />
<title>KIH Daily News</title>
<!--[if mso]>
<style type="text/css">
table {{ border-collapse: collapse; }}
td, th, div, p, a {{ font-family: '맑은 고딕', 'Malgun Gothic', Arial, sans-serif !important; }}
</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">

<!-- Wrapper table (Outlook은 body style을 자주 무시하므로 wrapper로 한 번 더 감쌈) -->
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="#f5f5f5" style="background-color:#f5f5f5;">
  <tr>
    <td align="center" style="padding:16px;">

      <!-- Container -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="640" style="max-width:640px;background-color:#ffffff;" bgcolor="#ffffff">

        <!-- Header -->
        <tr>
          <td style="padding:16px;border-bottom:2px solid #222222;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            <div style="font-size:18px;font-weight:bold;color:#222222;">{header_html}</div>
            <div style="font-size:13px;color:#666666;margin-top:6px;">
              {count_line}
            </div>
          </td>
        </tr>

        <!-- Items -->
        <tr>
          <td style="padding:14px 16px 4px 16px;">
            {items_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:10px 16px 16px 16px;border-top:1px solid #dddddd;color:#999999;font-size:11px;text-align:center;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            KIH Daily News Bot · 자동 발송 메시지<br />
            수신을 원치 않으시면 관리자에게 문의하세요.
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>

</body>
</html>"""


def _build_weekly_html(digest: dict, period_label: str) -> str:
    """Weekly digest HTML — Outlook 데스크톱 호환 table 기반."""
    by_company = digest.get("by_company", {}) or {}
    keywords = digest.get("keywords", []) or []
    circled = "①②③④⑤⑥⑦⑧⑨⑩"

    company_blocks = []
    for company, issues in by_company.items():
        if not issues:
            continue
        # 각 회사 블록도 table로
        item_rows = []
        for idx, item in enumerate(issues, 1):
            mark = circled[idx - 1] if idx <= 10 else f"({idx})"
            summary = html_escape(item.get("summary", ""))
            link = item.get("link", "") or "#"
            link_attr = html_escape(link, quote=True)
            item_rows.append(
                f'<tr><td style="padding:3px 0;font-family:\'맑은 고딕\',Arial,sans-serif;font-size:14px;line-height:1.45;">'
                f'<span style="color:#1e8e3e;font-weight:bold;">{mark}</span> '
                f'<a href="{link_attr}" style="color:#1a0dab;text-decoration:none;">{summary}</a>'
                f'</td></tr>'
            )
        company_blocks.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;margin:10px 0;background-color:#f7f7f7;" bgcolor="#f7f7f7">'
            f'<tr><td style="padding:10px 14px;font-family:\'맑은 고딕\',Arial,sans-serif;">'
            f'<div style="font-weight:bold;color:#222222;margin-bottom:6px;font-size:14px;">[{html_escape(company)}]</div>'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'{"".join(item_rows)}'
            f'</table>'
            f'</td></tr></table>'
        )

    # 키워드: Outlook에서도 안전하게 보이도록 단순 span 나열
    keyword_html = " ".join(
        f'<span style="background-color:#e8f0fe;color:#1967d2;padding:3px 8px;font-size:12px;">#{html_escape(k)}</span>'
        for k in keywords
    )

    empty_msg = '<div style="color:#888888;padding:14px;font-family:\'맑은 고딕\',Arial,sans-serif;">지난 1주간 주요 이슈 없음.</div>'

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width,initial-scale=1.0" />
<title>KIH Weekly Digest</title>
<!--[if mso]>
<style type="text/css">
table {{ border-collapse: collapse; }}
td, th, div, p, a {{ font-family: '맑은 고딕', 'Malgun Gothic', Arial, sans-serif !important; }}
</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="#f5f5f5" style="background-color:#f5f5f5;">
  <tr>
    <td align="center" style="padding:16px;">

      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="640" style="max-width:640px;background-color:#ffffff;" bgcolor="#ffffff">

        <!-- Header -->
        <tr>
          <td style="padding:16px;border-bottom:2px solid #1967d2;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            <div style="font-size:18px;font-weight:bold;color:#1967d2;">[금주 종합] 한국투자금융그룹</div>
            <div style="font-size:13px;color:#666666;margin-top:4px;">{html_escape(period_label)}</div>
          </td>
        </tr>

        <!-- 회사별 이슈 -->
        <tr>
          <td style="padding:14px 16px 0 16px;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            <div style="font-size:15px;font-weight:bold;margin-bottom:6px;color:#222222;">▶ 회사별 주요 이슈</div>
            {"".join(company_blocks) if company_blocks else empty_msg}
          </td>
        </tr>

        <!-- 키워드 -->
        <tr>
          <td style="padding:14px 16px;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            <div style="font-size:15px;font-weight:bold;margin-bottom:8px;color:#222222;">▶ 주간 핵심 키워드</div>
            <div>{keyword_html if keyword_html else '<span style="color:#888888;">N/A</span>'}</div>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:10px 16px 16px 16px;border-top:1px solid #dddddd;color:#999999;font-size:11px;text-align:center;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;">
            KIH Daily News Bot · 자동 발송 메시지
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>

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


def send_sector_news_email(items: list[dict], header_title: str) -> int:
    """Send sector (industry-wide) news to recipients.

    같은 _build_html_body / _build_text_body를 사용하므로 _get_item_tag가
    items의 category 필드에 따라 자동으로 업권 태그를 표시한다.
    헤더 텍스트만 업권 전용으로 다르게 전달.
    """
    return send_daily_news_email(items, header_title)


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

    subject = f"[KIH News 금주 종합] {period_label}"

    # Plain text version
    text_lines = [f"📊 한국투자금융그룹 금주 종합 ({period_label})", ""]
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
