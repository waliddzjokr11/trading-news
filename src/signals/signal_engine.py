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


def composite_label(score, config=None):
    """
    Maps composite_score to label.
    Uses signal_thresholds from config if present (hardened winrate fix), else defaults.
    Hardened: ≤-4.5 DUMP, ≤-2.5 BEARISH, -2.5..1.5 NEUTRAL, ≥2.5 BULLISH, ≥4.5 STRONG_BUY
    """
    th = config.get("signal_thresholds", {}) if config else {}
    # defaults depend on whether hardened thresholds exist
    has_hard = bool(th)
    dump = float(th.get("dump_warning", -4.5 if has_hard else -4.0))
    bear = float(th.get("bearish", -2.5 if has_hard else -1.5))
    neut_high = float(th.get("neutral_high", 1.5))
    bull = float(th.get("bullish", 2.5 if has_hard else 1.5))
    strong = float(th.get("strong_buy", 4.5 if has_hard else 4.0))
    if score <= dump:
        return "DUMP_WARNING"
    elif score <= bear:
        return "BEARISH"
    elif score <= neut_high:
        return "NEUTRAL"
    elif score < strong:
        return "BULLISH" if score >= bull else "NEUTRAL"
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
    label = composite_label(composite, config)
    # Strength / stars for Telegram (how strong is it)
    ab = abs(composite)
    if ab >= 4:
        stars = 5
        strength = "VERY STRONG"
    elif ab >= 3:
        stars = 4
        strength = "STRONG"
    elif ab >= 1.5:
        stars = 3
        strength = "MODERATE"
    elif ab >= 0.7:
        stars = 2
        strength = "WEAK"
    else:
        stars = 1
        strength = "VERY WEAK"
    stars_str = "★" * stars + "☆" * (5 - stars)

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
        "strength": strength,
        "stars": stars,
        "stars_str": stars_str,
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


def passes_quality_gate(signal_direction: str, composite_score: float, tv_data: dict, price_data: dict, config: dict) -> tuple[bool, list[str]]:
    """
    Returns (passes: bool, reasons_failed: list[str])
    ALL conditions in gate must pass for an alert to fire.
    """
    gate = config.get("alert_quality_gate", {})
    if not gate.get("enabled", True):
        return True, []
    failed = []
    # Determine direction: BUY/BULLISH vs SELL/BEARISH
    upper = str(signal_direction).upper()
    is_buy = upper in ("BUY", "STRONG_BUY", "BULLISH", "STRONG BUY")
    # fallback: composite positive = buy, negative = sell if label is NEUTRAL? Use composite
    if upper == "NEUTRAL":
        is_buy = composite_score >= 0
    reqs = gate.get("buy_requires" if is_buy else "sell_requires", {})
    # 1. Composite score threshold
    min_score = reqs.get("min_composite_score", 2.5)
    # For sell, min_score is negative, check absolute
    if is_buy and composite_score < min_score:
        failed.append(f"composite {composite_score:.2f} < {min_score} required")
    elif not is_buy and composite_score > min_score:
        # min_score for sell is negative like -2.5, so check > -2.5 fails
        failed.append(f"composite {composite_score:.2f} > {min_score} required")
    # 2. TradingView quality stars (only if TV data present)
    if tv_data:
        stars = tv_data.get("quality_stars", 0)
        try:
            stars = int(stars)
        except:
            stars = 0
        min_stars = reqs.get("min_tv_quality_stars", 4)
        if stars < min_stars:
            failed.append(f"TV quality {stars}★ < {min_stars}★ required")
        # 3. MTF confluence
        mtf_raw = tv_data.get("mtf_score", "0/8")
        try:
            mtf_bull = int(str(mtf_raw).split("/")[0])
        except Exception:
            mtf_bull = 0
        min_mtf = reqs.get("min_mtf_confluence", 5)
        mtf_aligned = mtf_bull if is_buy else (8 - mtf_bull)
        if mtf_aligned < min_mtf:
            failed.append(f"MTF {mtf_raw} only {mtf_aligned}/8 aligned ({min_mtf} required)")
        # 4. MACD direction
        macd_dir = tv_data.get("macd_direction", "")
        if reqs.get("macd_bullish") and is_buy and macd_dir != "bullish":
            failed.append(f"MACD not bullish (is: {macd_dir})")
        if reqs.get("macd_bearish") and not is_buy and macd_dir != "bearish":
            failed.append(f"MACD not bearish (is: {macd_dir})")
    # 5. EMA200 direction (from price_data)
    price = price_data.get("price", 0) if price_data else 0
    ema200 = price_data.get("ema200", 0) if price_data else 0
    if ema200 and ema200 > 0:
        if reqs.get("price_above_ema200") and is_buy and price < ema200:
            failed.append(f"price {price} below EMA200 {ema200:.2f}")
        if reqs.get("price_below_ema200") and not is_buy and price > ema200:
            failed.append(f"price {price} above EMA200 {ema200:.2f}")
    # 6. Volume check
    vol = price_data.get("volume_vs_avg", 1.0) if price_data else 1.0
    try:
        vol = float(vol)
    except:
        vol = 1.0
    if reqs.get("volume_above_average") and vol < 1.2:
        failed.append(f"volume {vol:.2f}x average (need > 1.2x)")
    passes = len(failed) == 0
    return passes, failed


def meets_min_signal(signal, min_signal):
    return should_alert_for_level(signal, min_signal)
