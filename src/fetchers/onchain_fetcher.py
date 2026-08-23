"""
onchain_fetcher.py — Whale Alert RSS + heuristics, free tier, never crashes.
"""
import re
import logging
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
try:
    from utils.rate_limiter import coingecko_limiter
    _onchain_limiter = coingecko_limiter
except ImportError:
    try:
        from src.utils.rate_limiter import coingecko_limiter
        _onchain_limiter = coingecko_limiter
    except:
        _onchain_limiter = None

logger = logging.getLogger(__name__)

WHALE_RSS = "https://whale-alert.io/rss"
# Additional free whalemap/alternative could be added later

# Patterns for scoring
INFLOW_KEYWORDS = ["to exchange", "to binance", "to coinbase", "to kraken", "to okx", "deposit", "inflow", "exchange inflow"]
OUTFLOW_KEYWORDS = ["from exchange", "from binance", "from coinbase", "withdrawal", "outflow", "exchange outflow", "accumulation"]
LARGE_TRANSFER_RE = re.compile(r"([\d,]+\.?\d*)\s*(BTC|ETH|USDT|USDC|XRP|SOL|ADA)", re.I)

# Quick scoring
def score_text(text):
    t = text.lower()
    score = 0
    reasons = []
    # inflow bearish
    if any(k in t for k in INFLOW_KEYWORDS):
        # large amount check
        m = LARGE_TRANSFER_RE.search(text)
        if m:
            amt = float(m.group(1).replace(",", ""))
            # heuristic: > 100 BTC or > 10M USDT is significant
            sym = m.group(2).upper()
            if (sym == "BTC" and amt > 100) or (sym in ("USDT","USDC") and amt > 5_000_000) or (sym == "ETH" and amt > 1000):
                score -= 2
                reasons.append(f"large inflow {amt} {sym}")
            else:
                score -= 1
                reasons.append("inflow")
        else:
            score -= 1
            reasons.append("inflow")
    if any(k in t for k in OUTFLOW_KEYWORDS):
        score += 2
        reasons.append("outflow/accumulation")
    if "whale bought" in t or "whale accumulation" in t:
        score += 1
        reasons.append("whale bought")
    if "whale sold" in t or "whale dump" in t:
        score -= 2
        reasons.append("whale dump")
    return score, reasons


def fetch_whale_alert(max_age_hours=4):
    events = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours + 2)
    try:
        # Use feedparser directly (handles http)
        feed = feedparser.parse(WHALE_RSS)
        if feed.bozo and not feed.entries:
            logger.warning(f"Whale Alert RSS parse issue: {feed.bozo_exception}")
            # fallback to requests text
            try:
                if _onchain_limiter:
                    _onchain_limiter.wait()
                r = requests.get(WHALE_RSS, timeout=10, headers={"User-Agent": "crypto-alert-system/1.0"})
                if r.status_code == 200:
                    feed = feedparser.parse(r.text)
            except Exception:
                pass
        for entry in feed.entries[:40]:
            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "") or title
            import re as _re
            summary = _re.sub("<[^<]+?>", "", summary)[:600]
            text = f"{title} {summary}"
            # date
            dt = datetime.now(timezone.utc)
            try:
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif entry.get("published"):
                    dt = parsedate_to_datetime(entry["published"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            if dt < cutoff:
                continue
            s, reasons = score_text(text)
            if s == 0:
                continue
            events.append({
                "title": title,
                "summary": summary,
                "text": text[:400],
                "published": dt,
                "score": s,
                "reasons": reasons,
                "source": "whale-alert",
                "link": entry.get("link", WHALE_RSS),
            })
        logger.info(f"Whale Alert: {len(events)} scored events")
    except Exception as e:
        logger.warning(f"Whale Alert exception: {e}")
    return events


def fetch_onchain(config):
    """Entry point. Returns list of events with score."""
    if not config.get("sources", {}).get("onchain_enabled", True):
        return []
    max_age = config.get("news", {}).get("max_age_hours", 4)
    events = fetch_whale_alert(max_age_hours=max_age)
    # aggregate score per run
    return events


def aggregate_onchain_score(events):
    """Sum scores, cap -5..+5"""
    total = sum(e["score"] for e in events)
    # cap
    total = max(-5, min(5, total))
    return total, events[:5]
