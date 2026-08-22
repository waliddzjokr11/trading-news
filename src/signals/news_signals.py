"""
news_signals.py — keyword scoring with deduplication boost.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Built-in keyword tiers — score per occurrence (headline + summary)
BEARISH = {
    "critical":  ["hack", "exploit", "breach", "sec", "lawsuit", "ban", "delisting", "seized", "bankrupt", "insolvent"],
    "moderate":  ["regulation", "investigation", "warning", "concern", "sell-off", "sell off", "crash", "dump", "fud"],
    "mild":      ["uncertainty", "delay", "postpone", "bearish", "decline"],
}
BULLISH = {
    "critical":  ["etf approved", "partnership", "listing", "integration", "adoption", "institutional"],
    "moderate":  ["upgrade", "launch", "bullish", "breakout", "accumulation", "whale bought"],
    "mild":      ["growth", "positive", "recovery", "support"],
}

SCORES = {
    "bearish_critical": -3,
    "bearish_moderate": -2,
    "bearish_mild": -0.5,
    "bullish_critical": 3,
    "bullish_moderate": 2,
    "bullish_mild": 0.5,
}


def build_keyword_lists(config):
    extra_bear = config.get("keywords", {}).get("bearish_critical_extra", []) or []
    extra_bull = config.get("keywords", {}).get("bullish_critical_extra", []) or []
    bear_crit = BEARISH["critical"] + [x.lower() for x in extra_bear]
    bull_crit = BULLISH["critical"] + [x.lower() for x in extra_bull]
    return {
        "bear_critical": bear_crit,
        "bear_moderate": BEARISH["moderate"],
        "bear_mild": BEARISH["mild"],
        "bull_critical": bull_crit,
        "bull_moderate": BULLISH["moderate"],
        "bull_mild": BULLISH["mild"],
    }


def score_text(text, kw):
    t = text.lower()
    score = 0
    hits = []
    # check each tier
    for phrase in kw["bear_critical"]:
        if phrase.lower() in t:
            score += SCORES["bearish_critical"]
            hits.append(f"bear_crit:{phrase}")
    for phrase in kw["bear_moderate"]:
        if phrase.lower() in t:
            score += SCORES["bearish_moderate"]
            hits.append(f"bear_mod:{phrase}")
    for phrase in kw["bear_mild"]:
        if phrase.lower() in t:
            score += SCORES["bearish_mild"]
            hits.append(f"bear_mild:{phrase}")
    for phrase in kw["bull_critical"]:
        if phrase.lower() in t:
            score += SCORES["bullish_critical"]
            hits.append(f"bull_crit:{phrase}")
    for phrase in kw["bull_moderate"]:
        if phrase.lower() in t:
            score += SCORES["bullish_moderate"]
            hits.append(f"bull_mod:{phrase}")
    for phrase in kw["bull_mild"]:
        if phrase.lower() in t:
            score += SCORES["bullish_mild"]
            hits.append(f"bull_mild:{phrase}")
    return score, hits


def evaluate_news(news_items, config):
    """
    news_items: list from news_fetcher (deduped, with source_count)
    Returns: {score, details, top_news}
    Hardened: require min_keyword_matches per headline and min_news_score_to_count magnitude.
    """
    if not news_items:
        return {"score": 0.0, "details": ["no news"], "top_news": []}
    kw = build_keyword_lists(config)
    news_cfg = config.get("news", {})
    min_sources_boost = news_cfg.get("min_sources_to_boost", 2)
    min_score_to_count = news_cfg.get("min_news_score_to_count", 2.0)
    min_matches = news_cfg.get("min_keyword_matches", 2)

    total = 0
    details = []
    scored = []
    for item in news_items:
        text = f"{item.get('title','')} {item.get('summary','')}"
        s, hits = score_text(text, kw)
        if s == 0:
            continue
        # NEW: require minimum keyword matches per headline (e.g., single "warning" no longer counts)
        if len(hits) < min_matches:
            continue
        # boost if multi-source
        if item.get("source_count", 1) >= min_sources_boost:
            s = s * 1.5
            hits.append(f"multi_source x1.5 ({item.get('source_count')} sources)")
        total += s
        scored.append({**item, "news_score": s, "hits": hits})

    # keep top 3 by abs(score)
    scored.sort(key=lambda x: abs(x["news_score"]), reverse=True)
    top = scored[:3]

    for t in top:
        details.append(f"{t['title'][:70]} -> {t['news_score']:+.1f} {t['hits'][:2]}")

    # NEW: require minimum total score magnitude — small noisy scores treated as neutral
    if abs(total) < min_score_to_count:
        return {"score": 0.0, "details": [f"news suppressed: total {total:.1f} < {min_score_to_count} threshold"], "top_news": [], "all_scored": scored}

    # cap total to -5..+5 for news component before weighting
    total = max(-5, min(5, total))
    # if no keyword hits, neutral
    if not top:
        details = ["no keyword hits"]

    return {"score": float(total), "details": details, "top_news": top, "all_scored": scored}
