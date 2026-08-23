"""
news_fetcher.py — CryptoPanic + RSS fallbacks, deduplication, multi-source boost.
Never crashes.
"""
import hashlib
import logging
import time
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
try:
    from utils.rate_limiter import coingecko_limiter
    # reuse coingecko limiter for cryptopanic (also 30/min) — conservative
    _news_limiter = coingecko_limiter
except ImportError:
    try:
        from src.utils.rate_limiter import coingecko_limiter
        _news_limiter = coingecko_limiter
    except:
        _news_limiter = None

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
]

HEADERS = {"User-Agent": "crypto-alert-system/1.0"}


def _parse_date(entry):
    # try published_parsed then parse string
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    for key in ("published", "updated", "pubDate"):
        val = entry.get(key)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    return datetime.now(timezone.utc)


def _hash_news(title, link):
    return hashlib.md5(f"{title.strip().lower()}|{link.strip().lower()}".encode()).hexdigest()[:16]


def fetch_cryptopanic(api_key, currencies=None, filter_hours=4):
    """Fetch CryptoPanic if key provided. Returns list of news dicts or [] ."""
    if not api_key:
        return []
    try:
        # public=True includes free feed; auth_token required for filtered feed
        params = {"auth_token": api_key, "public": "true", "kind": "news"}
        if currencies:
            # currencies comma like BTC,ETH — but spec uses CoinGecko IDs, we pass common symbols
            # limit to first 10 to avoid URL length
            params["currencies"] = ",".join(currencies[:10])
        if _news_limiter:
            _news_limiter.wait()
        resp = requests.get("https://cryptopanic.com/api/v1/posts/", params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            logger.warning("CryptoPanic 429")
            return []
        if resp.status_code != 200:
            logger.info(f"CryptoPanic {resp.status_code}: {resp.text[:150]}")
            return []
        data = resp.json()
        items = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=filter_hours + 2)
        for post in data.get("results", []):
            title = post.get("title", "")
            url = post.get("url", "")
            published = post.get("published_at") or post.get("created_at")
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else datetime.now(timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            if dt < cutoff:
                continue
            items.append({
                "title": title,
                "summary": title,
                "link": url,
                "source": "cryptopanic",
                "published": dt,
                "hash": _hash_news(title, url),
            })
        logger.info(f"CryptoPanic: {len(items)} items")
        return items
    except Exception as e:
        logger.warning(f"CryptoPanic exception: {e}")
        return []


def fetch_rss(max_age_hours=4):
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours + 1)
    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                logger.warning(f"RSS {name} parse failed: {feed.bozo_exception}")
                continue
            for entry in feed.entries[:30]:  # limit per feed
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                summary = entry.get("summary", "") or entry.get("description", "") or title
                # strip html quick
                import re
                summary = re.sub("<[^<]+?>", "", summary)[:800]
                dt = _parse_date(entry)
                if dt < cutoff:
                    continue
                items.append({
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "source": name,
                    "published": dt,
                    "hash": _hash_news(title, link),
                })
            logger.info(f"RSS {name}: {len([i for i in items if i['source']==name])} items")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"RSS {name} exception: {e}")
    return items


def fetch_news(config, state_manager=None, api_key=None):
    """
    Main entry: fetch, deduplicate, apply TTL, boost multi-source.
    Returns (deduped_news_list, news_by_hash)
    """
    max_age = config.get("news", {}).get("max_age_hours", 4)
    ttl = config.get("news", {}).get("dedup_ttl_hours", 24)
    # currencies for CryptoPanic — map watchlist to symbols (first 10)
    watchlist = config.get("watchlist", [])
    # quick symbol map first 10
    sym_map = {"bitcoin":"BTC","ethereum":"ETH","binancecoin":"BNB","ripple":"XRP","solana":"SOL","tron":"TRX","cardano":"ADA","avalanche-2":"AVAX","chainlink":"LINK","polkadot":"DOT"}
    currencies = [sym_map.get(c, c[:4].upper()) for c in watchlist[:10]]

    raw = []
    if config.get("sources", {}).get("news_rss_enabled", True):
        raw.extend(fetch_rss(max_age_hours=max_age))
    # cryptopanic supplement
    raw.extend(fetch_cryptopanic(api_key, currencies=currencies, filter_hours=max_age))

    # dedup by hash, count sources
    by_hash = {}
    for item in raw:
        h = item["hash"]
        if h not in by_hash:
            by_hash[h] = {**item, "source_count": 1, "sources": [item["source"]]}
        else:
            # same story from multiple sources
            by_hash[h]["source_count"] += 1
            if item["source"] not in by_hash[h]["sources"]:
                by_hash[h]["sources"].append(item["source"])
            # keep earliest published
            if item["published"] < by_hash[h]["published"]:
                by_hash[h]["published"] = item["published"]

    # filter by seen hashes if state_manager provided (but keep count for boost)
    deduped = []
    for h, item in by_hash.items():
        is_seen = False
        if state_manager:
            # prune first
            state_manager.prune_seen(ttl_hours=ttl)
            if state_manager.is_seen_news(h, ttl_hours=ttl):
                # already seen but if source_count increased, still consider boosted
                # check if new source_count > previous? For now skip if seen
                continue
        deduped.append(item)

    # sort by published desc
    deduped.sort(key=lambda x: x["published"], reverse=True)
    # limit 100
    deduped = deduped[:100]
    logger.info(f"News: {len(raw)} raw -> {len(deduped)} deduped (boost candidates: {sum(1 for x in deduped if x['source_count']>=2)})")
    return deduped, by_hash
