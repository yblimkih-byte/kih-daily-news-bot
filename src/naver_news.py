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

# 업권별 거시 뉴스 검색 대상.
# 정부 활동(검사·정책·규제) 키워드 중심으로 노이즈 최소화.
# 본문에 한국투자 계열사명이 없어도 잡히도록 회사명 매칭 필터를 우회한다.
# 회사 검색이 먼저 실행되므로 회사와 업권 모두 매칭되는 기사는 회사로 분류됨 (회사 우선).
TARGET_SECTORS = [
    {
        "name": "증권업",
        "tag": "증권업",
        "queries": ["증권사 검사", "자본시장법", "금융투자업 규제"],
    },
    {
        "name": "자산운용업",
        "tag": "운용업",
        "queries": ["자산운용사 규제", "공모펀드 정책", "ETF 정책"],
    },
    {
        "name": "신탁업",
        "tag": "신탁업",
        "queries": ["신탁업 규제", "부동산신탁 정책", "금융위 신탁"],
    },
    {
        "name": "저축은행업",
        "tag": "저축은행업",
        "queries": ["저축은행 검사", "저축은행 부실", "예금자보호"],
    },
    {
        "name": "캐피탈·여전업",
        "tag": "여전업",
        "queries": ["여신전문금융업", "캐피탈사 규제", "할부금융"],
    },
    {
        "name": "벤처투자업",
        "tag": "VC업",
        "queries": ["벤처투자 정책", "모태펀드", "벤처캐피탈 규제"],
    },
    {
        "name": "금융지주업",
        "tag": "지주업",
        "queries": ["금융지주 규제", "금융지주회사법", "금감원 지주"],
    },
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
             "dup_url": 0, "dup_title": 0,
             "sector_naver": 0, "sector_external": 0}

    # === Phase 1: 회사 검색 (회사명 본문 매칭 필수) ===
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
                    "category": "company",
                    "company": company["name"],
                    "sector": None,
                    "title": title,
                    "description": description,
                    "link": primary_link,
                    "original_link": original_link,
                    "pub_date": pub_dt.isoformat(),
                    "is_naver": is_naver,
                    "media": media,
                    "_url_key": url_key,
                    "_title_key": title_key,
                })
                if is_naver:
                    stats["naver"] += 1
                else:
                    stats["external"] += 1

            time.sleep(0.3)

    print(
        f"[INFO] Naver search (companies): total={len(all_articles)} "
        f"(naver={stats['naver']}, external={stats['external']}), "
        f"skipped_old={stats['old']}, skipped_no_match={stats['no_match']}, "
        f"skipped_dup_url={stats['dup_url']}, skipped_dup_title={stats['dup_title']}",
        flush=True,
    )

    # === Phase 2: 업권 검색 (회사명 매칭 우회) ===
    # 회사 검색에서 등록된 seen_url_keys / seen_title_keys를 그대로 사용하므로
    # 회사와 업권 모두에 잡히는 기사는 자동으로 회사 카테고리로 분류된다 (회사 우선).
    sector_start_idx = len(all_articles)
    for sector in TARGET_SECTORS:
        for query in sector["queries"]:
            try:
                items = _search_one_query(query)
            except Exception as e:
                print(f"[ERROR] Naver sector search failed for '{query}': {e}", flush=True)
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

                url_key = normalize_url(primary_link)
                if url_key and url_key in seen_url_keys:
                    stats["dup_url"] += 1
                    continue

                title_key = normalize_title(title)
                if title_key and title_key in seen_title_keys:
                    stats["dup_title"] += 1
                    continue

                # 업권 검색은 회사명 매칭 필터를 우회.
                # 단, 회사 카테고리에 잡혔어야 할 기사가 누락되지 않도록
                # 본문에 한투 계열사명이 명시되어 있으면 그 회사 카테고리로 재분류한다.
                matched_company = None
                for company in TARGET_COMPANIES:
                    company_name_short = company["name"].replace("한국투자", "")
                    if (company["name"] in title
                            or company["name"] in description
                            or company_name_short in title):
                        matched_company = company["name"]
                        break

                if url_key:
                    seen_url_keys.add(url_key)
                if title_key:
                    seen_title_keys.add(title_key)

                media = extract_media_name(primary_link) if not is_naver else "네이버뉴스"

                if matched_company:
                    # 회사 검색에서 누락된 회사 기사를 업권 검색이 잡은 경우 회사 카테고리로 등록
                    article_record = {
                        "category": "company",
                        "company": matched_company,
                        "sector": None,
                    }
                else:
                    article_record = {
                        "category": "sector",
                        "company": None,
                        "sector": sector["name"],
                    }

                article_record.update({
                    "title": title,
                    "description": description,
                    "link": primary_link,
                    "original_link": original_link,
                    "pub_date": pub_dt.isoformat(),
                    "is_naver": is_naver,
                    "media": media,
                    "_url_key": url_key,
                    "_title_key": title_key,
                })
                all_articles.append(article_record)

                if article_record["category"] == "sector":
                    if is_naver:
                        stats["sector_naver"] += 1
                    else:
                        stats["sector_external"] += 1
                else:
                    # 회사 매칭으로 재분류된 경우 회사 카운트에 추가
                    if is_naver:
                        stats["naver"] += 1
                    else:
                        stats["external"] += 1

            time.sleep(0.3)

    sector_count = len(all_articles) - sector_start_idx
    sector_only = sum(1 for a in all_articles[sector_start_idx:] if a.get("category") == "sector")
    reclassified = sector_count - sector_only
    print(
        f"[INFO] Naver search (sectors): added={sector_count} "
        f"(sector_only={sector_only} [naver={stats['sector_naver']}, "
        f"external={stats['sector_external']}], reclassified_to_company={reclassified})",
        flush=True,
    )
    print(
        f"[INFO] Naver search (TOTAL): total={len(all_articles)} "
        f"(company={sum(1 for a in all_articles if a.get('category')=='company')}, "
        f"sector={sum(1 for a in all_articles if a.get('category')=='sector')})",
        flush=True,
    )

    for i, a in enumerate(all_articles[:30], 1):
        tag = "N" if a["is_naver"] else "E"
        label = a.get("company") or f"[업권]{a.get('sector')}"
        print(f"[DEBUG] {i:2d}[{tag}] [{label}] {a['link'][:60]}", flush=True)

    return all_articles
