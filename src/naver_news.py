"""Naver News search client."""
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


NAVER_NEWS_API = "https://openapi.naver.com/v1/search/news.json"

# 10개 모니터링 대상 회사 검색어
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
    {"name": "한국투자액셀러레이터", "queries": ["한국투자액셀러레이터"]},
]

KST = timezone(timedelta(hours=9))


def _strip_html(text: str) -> str:
    """Remove HTML tags from Naver API response."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return text.strip()


def _parse_pub_date(pub_date_str: str) -> datetime:
    """Parse RFC 2822 datetime to KST-aware datetime."""
    dt = parsedate_to_datetime(pub_date_str)
    return dt.astimezone(KST)


def _is_naver_news_link(url: str) -> bool:
    """Check if URL is a Naver News article."""
    return "n.news.naver.com" in url or "m.news.naver.com" in url or "news.naver.com" in url


def _search_one_query(query: str, display: int = 30) -> list[dict]:
    """Call Naver News API for a single query."""
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


def fetch_recent_news(hours_back: int = 24) -> list[dict]:
    """Fetch news articles from the last N hours for all target companies.

    Returns:
        list[dict]: Each article has:
            - company: str
            - title: str (HTML-stripped)
            - description: str (HTML-stripped)
            - link: str (Naver news link if available, else original)
            - original_link: str
            - pub_date: ISO 8601 datetime string (KST)
            - is_naver: bool
    """
    cutoff = datetime.now(KST) - timedelta(hours=hours_back)
    all_articles = []
    seen_links = set()

    for company in TARGET_COMPANIES:
        company_articles = []
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

                if not title or not link:
                    continue

                try:
                    pub_dt = _parse_pub_date(pub_date_raw)
                except Exception:
                    continue

                if pub_dt < cutoff:
                    continue

                # Naver link preference
                primary_link = link if _is_naver_news_link(link) else original_link or link
                if primary_link in seen_links:
                    continue
                seen_links.add(primary_link)

                # Filter: must mention company name in title or description
                company_name_short = company["name"].replace("한국투자", "")
                if (company["name"] not in title
                        and company["name"] not in description
                        and company_name_short not in title
                        and company_name_short not in description):
                    continue

                company_articles.append({
                    "company": company["name"],
                    "title": title,
                    "description": description,
                    "link": primary_link,
                    "original_link": original_link,
                    "pub_date": pub_dt.isoformat(),
                    "is_naver": _is_naver_news_link(primary_link),
                })

            time.sleep(0.3)  # Be polite to Naver API

        all_articles.extend(company_articles)

    print(f"[INFO] Collected {len(all_articles)} articles across {len(TARGET_COMPANIES)} companies.", flush=True)
    return all_articles
