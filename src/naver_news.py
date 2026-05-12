"""Naver News search client. Collects both Naver-hosted and external-media articles.

Dedupe layers (in order):
  1. URL normalization (strip tracking params, unify host) → blocks identical-article duplicates
  2. Title fingerprint → blocks same-event-different-media duplicates within one slot
"""
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

# Reuse normalization helpers from sent_history so dedupe keys are consistent
# across in-slot dedupe and cross-slot history checks.
from sent_history import normalize_url, normalize_title


NAVER_NEWS_API = "https://openapi.naver.com/v1/search/news.json"

TARGET_COMPANIES = [
    {"name": "한국투자금융지주", "queries": ["한국투자금융지주"]},
    {"name": "한국투자증권", "queries": ["한국투자증권"]},
    {"name": "한국투자신탁운용", "queries": ["한국투자신탁운용", "ACE ETF"]},
    {"name": "한국투자밸류자산운용", "queries": ["한국투자밸류자산운용"]},
    {"name": "한국투자파트너스", "queries": ["한국투자파트너스"]},
    {"name": "한국투자프라이빗에쿼티", "queries": ["한국투자프라이빗에쿼티", "한국투자PE"]},
    {"name": "한국투자캐피탈", "queries": ["한국투자캐피탈"]},
    {"name": "한국투자저축은행", "queries": ["한국투자저축은행"]},
    {"name": "한국투자리얼에셋운용", "queries": ["한국투자리얼에셋운용"]},
    {"name": "한국투자부동산신탁", "queries": ["한국투자부동산신탁"]},
    {"name": "한국투자액셀러레이터", "queries": ["한국투자액셀러레이터"]},
]

KST = timezone(timedelta(hours=9))


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return text.strip()


def _parse_pub_date(pub_date_str: str) -> datetime:
    dt = parsedate_to_datetime(pub_date_str)
    return dt.astimezone(KST)


def _is_naver_news_link(url: str) -> bool:
    if not url:
        return False
    return any(d in url for d in (
        "://n.news.naver.com",
        "://m.news.naver.com",
        "://news.naver.com",
    ))


def extract_media_name(url: str) -> str:
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


def _search_one_query(query: str, display: int = 30) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
        "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"],
    }
    params = {
        "query": query,
        "display": display,
        "start": 1,
        "sort": "date",
    }
    response = requests.get(NAVER_NEWS_API, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("items", [])


def fetch_recent_news(hours_back: float = 24) -> list[dict]:
    """Fetch news articles from the last N hours for all target companies.

    In-slot dedupe is enforced by:
      - seen_url_keys: normalized URLs (handles tracking params, m./www., scheme)
      - seen_title_keys: normalized titles (handles same event reported by multiple media)
    """
    cutoff = datetime.now(KST) - timedelta(hours=hours_back)
    all_articles = []
    seen_url_keys = set()
    seen_title_keys = set()
    stats = {"naver": 0, "external": 0, "old": 0, "no_match": 0,
             "dup_url": 0, "dup_title": 0}

    for company in TARGET_COMPANIES:
        for query in company["queries"]:
            try:
                items = _search_one_query(query)
            except Exception as e:
                print(f"[ERROR] Naver search failed for '{query}': {e}", flush=True)
                continue

            for item in items:
                title = _strip_html(item.get("title", ""))
                description = _strip_html(item.get("description", ""))
                link = item.get("link", "")
                original_link = item.get("originallink", "")
                pub_date_raw = item.get("pubDate", "")

                if not title or not (link or original_link):
                    continue

                try:
                    pub_dt = _parse_pub_date(pub_date_raw)
                except Exception:
                    continue

                if pub_dt < cutoff:
                    stats["old"] += 1
                    continue

                is_naver = _is_naver_news_link(link)
                primary_link = link if is_naver else (original_link or link)
                if not primary_link:
                    continue

                # --- Layer 1: URL fingerprint (canonical) ---
                url_key = normalize_url(primary_link)
                if url_key and url_key in seen_url_keys:
                    stats["dup_url"] += 1
                    continue

                # --- Layer 2: title fingerprint (same event, different media) ---
                title_key = normalize_title(title)
                if title_key and title_key in seen_title_keys:
                    stats["dup_title"] += 1
                    continue

                # --- Company match in title or description ---
                company_name_short = company["name"].replace("한국투자", "")
                if (company["name"] not in title
                        and company["name"] not in description
                        and company_name_short not in title
                        and company_name_short not in description):
                    stats["no_match"] += 1
                    continue

                if url_key:
                    seen_url_keys.add(url_key)
                if title_key:
                    seen_title_keys.add(title_key)

                media = extract_media_name(primary_link) if not is_naver else "네이버뉴스"

                all_articles.append({
                    "company": company["name"],
                    "title": title,
                    "description": description,
                    "link": primary_link,
                    "original_link": original_link,
                    "pub_date": pub_dt.isoformat(),
                    "is_naver": is_naver,
                    "media": media,
                    # Carry the dedupe keys so downstream code (sent_history) can re-use them
                    "_url_key": url_key,
                    "_title_key": title_key,
                })
                if is_naver:
                    stats["naver"] += 1
                else:
                    stats["external"] += 1

            time.sleep(0.3)

    print(
        f"[INFO] Naver search: total={len(all_articles)} "
        f"(naver={stats['naver']}, external={stats['external']}), "
        f"skipped_old={stats['old']}, skipped_no_match={stats['no_match']}, "
        f"skipped_dup_url={stats['dup_url']}, skipped_dup_title={stats['dup_title']}",
        flush=True,
    )
    for i, a in enumerate(all_articles[:30], 1):
        tag = "N" if a["is_naver"] else "E"
        print(f"[DEBUG] {i:2d}[{tag}] [{a['company']}] {a['link'][:60]}", flush=True)

    return all_articles
