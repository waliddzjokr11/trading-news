"""
adaptive_tuner.py — learns from losing trades and suggests/adjusts config for higher winrate.
Called after each run from main.py. Analyzes trade_history, winrate, SL reasons.
"""
import logging
logger = logging.getLogger(__name__)

def analyze_and_tune(state, config):
    """
    Analyze recent trades and auto-tune thresholds.
    Returns dict of suggested adjustments (also logs).
    Does NOT auto-write config.yaml, but returns suggestions and updates in-memory performance.
    """
    perf = state.get("performance", {})
    trade_hist = state.get("trade_history", [])[-50:]  # last 50
    total = perf.get("wins", 0) + perf.get("losses", 0)
    winrate = perf.get("winrate", 0)
    suggestions = []

    if total < 10:
        logger.info(f"Adaptive tuner: not enough trades ({total}) for learning")
        return {"winrate": winrate, "suggestions": []}

    # If winrate low, suggest tightening
    if winrate < 55 and total >= 10:
        # Check why losing: many SL tight?
        learn = state.get("learning", {})
        sl_by_reason = learn.get("sl_by_reason", {})
        tight = sl_by_reason.get("tight SL", 0)
        if tight >= 3:
            suggestions.append("Widen SL: ATR 1.5 → 2.0 (tight SL losses)")
            # auto-adjust in-memory price risk? For now just suggest
        # Check news: many losses with bearish news false?
        # If news is noisy, suggest raising news threshold
        # For now, suggest raising min_composite
        suggestions.append("Raise alert_quality_gate min_composite 0.4 → 0.8 to reduce noise")
        # Check per-coin worst
        by_coin = {}
        for t in trade_hist:
            if t.get("hit") == "SL":
                by_coin[t.get("coin")] = by_coin.get(t.get("coin"), 0) + 1
        worst = sorted(by_coin.items(), key=lambda x: x[1], reverse=True)[:2]
        for coin, cnt in worst:
            if cnt >= 3:
                suggestions.append(f"Raise threshold for {coin} ({cnt} SL) or exclude")
        logger.info(f"Adaptive tuner: winrate {winrate}% low, suggestions: {suggestions}")
    elif winrate > 70 and total >= 10:
        suggestions.append("Winrate high — can relax gate 0.4 → 0.3 for more signals")
        logger.info(f"Adaptive tuner: winrate {winrate}% high, relax")

    # Store suggestions in state for dashboard
    state.setdefault("learning", {})["suggestions"] = suggestions[-3:]
    state["learning"]["last_winrate"] = winrate
    state["learning"]["total_trades"] = total
    return {"winrate": winrate, "suggestions": suggestions, "tight_sl": tight if 'tight' in locals() else 0}
