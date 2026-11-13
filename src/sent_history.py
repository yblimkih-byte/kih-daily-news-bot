"""Sent article history tracker.

Records every article URL and normalized title key for items that were
included in any send (Kakao/Email/Telegram), then blocks re-sending them
in subsequent slots within a retention window (default 3 days).

Storage: `.sent_history.json` at repo root, committed by daily.yml.

Schema:
{
  "entries": [
    {
      "url_key": "<normalized url>",
      "title_key": "<normalized title>",
      "sent_at": "2026-05-12T17:00:00+09:00",
      "title_preview": "한국투자증권 부산 디지털사이니지 ...",
      "story": "kis-busan-signage",          # 여러 회차에 걸친 큰 사건 단위 (AI 부여, 회차 간 재사용)
      "event_group": "kis-busan-signage",    # 개별 보도 단위 (회차 내 중복 제거용)
      "kind": "new_fact",                    # new_fact | analysis
      "summary": "한투증권 부산 디지털사이니지 광고 집행"
    },
    ...
  ]
}
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


KST = timezone(timedelta(hours=9))

HISTORY_FILE = Path(__file__).parent.parent / ".sent_history.json"

# Days to retain in history. After this, entries are pruned.
RETENTION_DAYS = int(os.environ.get("SENT_HISTORY_DAYS", "3"))

# Query params to strip when normalizing URLs (tracking junk).
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "inflow", "outurl", "mibextid", "fbclid", "gclid", "yclid",
    "_ga", "_gl", "ref", "ref_src", "source", "from",
}


# --------------------------------------------------------------------------- #
# Normalization                                                                #
# --------------------------------------------------------------------------- #

def normalize_url(url: str) -> str:
    """Strip tracking params, unify host prefixes, lower-case scheme/host."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip().lower()

    scheme = (p.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"

    host = (p.hostname or "").lower()
    # Unify naver mobile/desktop
    if host in ("m.news.naver.com", "news.naver.com"):
        host = "n.news.naver.com"
    # Drop common 'm.' / 'www.' prefixes for general matching
    if host.startswith("www."):
        host = host[4:]

    # Filter tracking params
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
          if k.lower() not in TRACKING_PARAMS]
    qs.sort()  # stable order

    path = p.path.rstrip("/")
    new = (scheme, host, path, "", urlencode(qs), "")
    return urlunparse(new)


def normalize_title(title: str) -> str:
    """Build a fingerprint key for a title.

    - Strip news-source tags like [속보], [단독], <제목>
    - Remove all punctuation, quotes, whitespace
    - Keep only Korean chars, digits, alphabet
    - Truncate to 40 chars (absorbs trailing media name / minor variations)
    """
    if not title:
        return ""
    s = title

    # Strip bracketed tags (front and inline)
    s = re.sub(r"[\[\<\【\「\『][^\]\>\】\」\』]{0,15}[\]\>\】\」\』]", " ", s)

    # Remove all quote chars
    s = re.sub(r"[\"'\u201C\u201D\u2018\u2019\u00B4`]", "", s)
    # Remove punctuation (keep Korean/English/digit/space)
    s = re.sub(r"[^\w\s가-힣]", " ", s, flags=re.UNICODE)
    # Collapse whitespace and lower
    s = re.sub(r"\s+", "", s).lower()

    if len(s) > 40:
        s = s[:40]
    return s


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #

def _load() -> dict:
    if not HISTORY_FILE.exists():
        return {"entries": []}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            return {"entries": []}
        return data
    except Exception as e:
        print(f"[WARN][history] Failed to read {HISTORY_FILE}: {e}", flush=True)
        return {"entries": []}


def _save(data: dict) -> None:
    try:
        HISTORY_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[WARN][history] Failed to write {HISTORY_FILE}: {e}", flush=True)


def _prune(data: dict, now: datetime) -> dict:
    """Drop entries older than RETENTION_DAYS."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = []
    for entry in data.get("entries", []):
        ts_raw = entry.get("sent_at")
        try:
            ts = datetime.fromisoformat(ts_raw)
        except Exception:
            continue  # malformed entry; drop
        if ts >= cutoff:
            kept.append(entry)
    return {"entries": kept}


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def load_known_keys() -> tuple[set, set]:
    """Return (known_url_keys, known_title_keys) from history file.

    Entries older than RETENTION_DAYS are pruned and the file is rewritten.
    """
    now = datetime.now(KST)
    data = _prune(_load(), now)
    _save(data)  # rewrite pruned

    urls, titles = set(), set()
    for e in data["entries"]:
        if e.get("url_key"):
            urls.add(e["url_key"])
        if e.get("title_key"):
            titles.add(e["title_key"])
    print(
        f"[INFO][history] Loaded {len(urls)} url keys, {len(titles)} title keys "
        f"(retention={RETENTION_DAYS}d).",
        flush=True,
    )
    return urls, titles


def filter_unseen(articles: list[dict]) -> tuple[list[dict], int]:
    """Drop articles whose url_key OR title_key is already in history.

    Returns (kept_articles, dropped_count).
    """
    known_urls, known_titles = load_known_keys()
    kept = []
    dropped = 0
    for a in articles:
        url_key = normalize_url(a.get("link", ""))
        title_key = normalize_title(a.get("title", ""))
        if url_key and url_key in known_urls:
            dropped += 1
            continue
        if title_key and title_key in known_titles:
            dropped += 1
            continue
        # Attach keys so caller can reuse them for save
        a["_url_key"] = url_key
        a["_title_key"] = title_key
        kept.append(a)
    if dropped:
        print(f"[INFO][history] Cross-slot dedupe dropped {dropped} previously-sent article(s).", flush=True)
    return kept, dropped


def to_context_entry(item: dict, sent_at: str) -> dict:
    """발송 항목을 이력/AI 문맥용 공통 형식으로 변환."""
    eg = (item.get("event_group") or "").strip().lower()
    return {
        "sent_at": sent_at,
        "title_preview": (item.get("title") or "")[:60],
        "story": (item.get("story") or "").strip().lower() or eg,
        "event_group": eg,
        "kind": item.get("novelty") or "new_fact",
        "summary": (item.get("summary") or "")[:60],
    }


def load_recent_sent() -> list[dict]:
    """최근 RETENTION_DAYS일 발송 이력 (오래된 순). AI의 사건 단위 중복 판정 문맥용.

    구버전 항목은 story/kind/summary가 없을 수 있음 (AI가 제목만으로 판정).
    """
    now = datetime.now(KST)
    entries = _prune(_load(), now)["entries"]
    entries.sort(key=lambda e: e.get("sent_at", ""))
    return entries


def record_sent(items: list[dict]) -> None:
    """Append the given items to history. Should be called after a successful send."""
    if not items:
        return
    now = datetime.now(KST)
    data = _prune(_load(), now)
    existing_urls = {e.get("url_key") for e in data["entries"] if e.get("url_key")}
    existing_titles = {e.get("title_key") for e in data["entries"] if e.get("title_key")}

    added = 0
    for it in items:
        url_key = it.get("_url_key") or normalize_url(it.get("link", ""))
        title_key = it.get("_title_key") or normalize_title(it.get("title", ""))
        if not url_key and not title_key:
            continue
        # Avoid duplicate rows
        if url_key in existing_urls or title_key in existing_titles:
            continue
        entry = {"url_key": url_key, "title_key": title_key}
        entry.update(to_context_entry(it, now.isoformat()))
        data["entries"].append(entry)
        existing_urls.add(url_key)
        existing_titles.add(title_key)
        added += 1

    _save(data)
    print(f"[INFO][history] Recorded {added} new sent article(s). Total entries: {len(data['entries'])}.", flush=True)
