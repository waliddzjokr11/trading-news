"""
signal_engine.py — weighted composite score -> signal label
"""
import logging

logger = logging.getLogger(__name__)


LABELS = [
    ("DUMP_WARNING", -4, "low"),
    ("BEARISH", -1.5, "low_mid"),
    ("NEUTRAL", 1.5, "mid"),
    ("BULLISH", 4, "high_mid"),
    ("STRONG_BUY", float("inf"), "high"),
]

COLOR = {
    "DUMP_WARNING": "#ff2a2a",
    "BEARISH": "#ff8c00",
    "NEUTRAL": "#f5c518",
    "BULLISH": "#2ecc71",
    "STRONG_BUY": "#00ff88",
}
EMOJI = {
    "DUMP_WARNING": "🔴",
    "BEARISH": "🟠",
    "NEUTRAL": "🟡",
    "BULLISH": "🟢",
    "STRONG_BUY": "🚀",
}
LABEL_RANK = {"DUMP_WARNING": 0, "BEARISH": 1, "NEUTRAL": 2, "BULLISH": 3, "STRONG_BUY": 4}


def composite_label(score):
    """
    Maps composite_score to label.
    ≤-4 DUMP_WARNING, -3..-1.5 BEARISH, -1.5..1.5 NEUTRAL, 1.5..3 BULLISH, ≥4 STRONG_BUY  (spec)
    Spec says -3 to -1.5 BEARISH; we implement inclusive.
    """
    if score <= -4:
        return "DUMP_WARNING"
    elif score < -1.5:
        return "BEARISH"
    elif score <= 1.5:
        return "NEUTRAL"
    elif score < 4:
        return "BULLISH"
    else:
        return "STRONG_BUY"


def should_alert_for_level(signal_label, min_level):
    """Check if signal meets min_signal_to_alert threshold."""
    order = ["DUMP_WARNING", "BEARISH", "NEUTRAL", "BULLISH", "STRONG_BUY"]
    # But DUMP_WARNING is most bearish; alert thresholds are from bearish upward.
    # For simplicity, use rank where DUMP_WARNING=0 lowest. Min level defines lowest rank to alert.
    # However spec says min_signal_to_alert options: DUMP_WARNING, BEARISH, NEUTRAL, BULLISH, STRONG_BUY
    # We interpret: alert if rank >= rank(min) OR if bearish side and rank <= rank(min) when min is bearish?
    # Simpler: define urgency: DUMP_WARNING most urgent, so if min is BEARISH, we alert BEARISH, DUMP_WARNING, BULLISH, STRONG_BUY? No, that's all.
    # Instead, implement as: alert if label != NEUTRAL and meets threshold per spec's intent (min_signal is minimum severity to alert).
    # For crypto, both extremes are alerts. So we use: if min is BEARISH, alert BEARISH, DUMP_WARNING, BULLISH, STRONG_BUY (all except NEUTRAL)
    # If min is STRONG_BUY, only STRONG_BUY; if BULLISH, BULLISH+STRONG_BUY, etc.
    # To support bearish filtering, we treat both sides separately.
    # Practical: use explicit sets
    thresholds = {
        "DUMP_WARNING": {"DUMP_WARNING"},
        "BEARISH": {"DUMP_WARNING", "BEARISH", "BULLISH", "STRONG_BUY"},
        "NEUTRAL": {"DUMP_WARNING", "BEARISH", "NEUTRAL", "BULLISH", "STRONG_BUY"},  # all (daily digest)
        "BULLISH": {"BULLISH", "STRONG_BUY"},
        "STRONG_BUY": {"STRONG_BUY"},
    }
    allowed = thresholds.get(min_level, thresholds["BEARISH"])
    return signal_label in allowed


def evaluate(coin_id, price_result, news_result, onchain_result, config):
    """
    Combine weighted scores.
    price_result, news_result, onchain_result: each has 'score'
    Returns dict with composite_score, label, color, emoji, breakdown
    """
    weights = config.get("weights", {})
    wp = float(weights.get("price", 0.40))
    wn = float(weights.get("news", 0.35))
    wo = float(weights.get("onchain", 0.25))
    # validate sum
    s = wp + wn + wo
    if abs(s - 1.0) > 0.01:
        logger.warning(f"Weights sum {s:.2f} !=1.0 — normalizing")
        wp, wn, wo = wp/s, wn/s, wo/s

    ps = float(price_result.get("score", 0))
    ns = float(news_result.get("score", 0))
    os = float(onchain_result.get("score", 0))

    composite = ps * wp + ns * wn + os * wo
    # scale to -5..5 then map; but composite already weighted sum of -5..5 -> -5..5
    # To match spec mapping where thresholds are at ±1.5, ±4, we keep as is
    label = composite_label(composite)

    return {
        "coin": coin_id,
        "composite_score": float(round(composite, 2)),
        "price_score": ps,
        "news_score": ns,
        "onchain_score": os,
        "weights": {"price": wp, "news": wn, "onchain": wo},
        "signal": label,
        "color": COLOR.get(label, "#888"),
        "emoji": EMOJI.get(label, "⚪"),
        "details": {
            "price": price_result.get("details", []),
            "news": news_result.get("details", []),
            "onchain": onchain_result.get("details", []),
        },
        "rsi": price_result.get("rsi"),
        "macd": price_result.get("macd"),
        "top_news": news_result.get("top_news", []),
        "onchain_events": onchain_result.get("events", []),
    }


def meets_min_signal(signal, min_signal):
    return should_alert_for_level(signal, min_signal)
