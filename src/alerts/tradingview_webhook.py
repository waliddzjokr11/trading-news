"""
tradingview_webhook.py — TV signal processor, computes combined score with news+onchain
Logs to state.json performance + tv_signals. Fires Telegram alerts.
"""
import logging
from datetime import datetime, timezone
from src.fetchers.price_fetcher import fetch_prices
from src.signals.news_signals import evaluate_news
from src.signals.onchain_signals import evaluate_onchain
from src.signals.signal_engine import composite_label
from src.fetchers.news_fetcher import fetch_news
from src.fetchers.onchain_fetcher import fetch_onchain, aggregate_onchain_score

logger = logging.getLogger(__name__)

# Map TV ticker like BTCUSDT or BINANCE:BTCUSDT to CoinGecko ID
TV_TICKER_MAP = {
    "BTCUSDT": "bitcoin", "BTCUSD": "bitcoin",
    "ETHUSDT": "ethereum", "ETHUSD": "ethereum",
    "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple", "XRPUSD": "ripple",
    "SOLUSDT": "solana",
    "TRXUSDT": "tron",
    "ADAUSDT": "cardano",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "DOTUSDT": "polkadot",
    "ATOMUSDT": "cosmos",
    "NEARUSDT": "near",
    "OPUSDT": "optimism",
    "ARBUSDT": "arbitrum",
    "TAOUSDT": "bittensor",
    "WLDUSDT": "worldcoin-wld",
    "SANDUSDT": "the-sandbox",
    "WAVESUSDT": "waves",
    "JTOUSDT": "jito-governance-token",
    "INJUSDT": "injective-protocol",
    "UNIUSDT": "uniswap",
    "CRVUSDT": "curve-dao-token",
    "1INCHUSDT": "1inch",
    "MINAUSDT": "mina-protocol",
    "CELOUSDT": "celo",
    "ROSEUSDT": "oasis-network",
    "NOTUSDT": "notcoin",
    "GLMRUSDT": "moonbeam",
    "ASTRUSDT": "astar",
    "DOGEUSDT": "dogecoin",
    "CROUSDT": "crypto-com-chain",
    # add all watchlist upper symbols
}

def normalize_coin(tv_coin, config_watchlist=None):
    """Convert TV ticker to CoinGecko ID. tv_coin may be BTCUSDT, BINANCE:BTCUSDT, btcusdt"""
    raw = tv_coin.strip().upper()
    # handle BINANCE: prefix
    if ":" in raw:
        raw = raw.split(":")[-1]
    # remove .P for perps
    raw = raw.replace(".P","")
    # direct map
    if raw in TV_TICKER_MAP:
        return TV_TICKER_MAP[raw]
    # try lower map for watchlist: if raw ends with USDT, strip
    base = raw.replace("USDT","").replace("USD","").lower()
    # search watchlist for symbol match: simplistic, use lower
    # if config_watchlist contains that id, return it if base matches first chars
    # fallback: try to map via symbol->id for 100 coins (create reverse)
    rev = {v.upper(): k for k,v in TV_TICKER_MAP.items()}
    if base.upper() in rev:
        return rev[base.upper()]
    # final: search config_watchlist for id containing base
    if config_watchlist:
        for cid in config_watchlist:
            if base in cid.lower() or cid.lower().startswith(base):
                return cid
    # fallback raw lower
    return raw.lower()


def compute_tv_combined_score(tv_signal: dict, coin_id: str, config, state_manager=None) -> dict:
    """
    Implements spec 2B: tv stars 0-5 -> -3..+3, direction multiplier, then 0.5/0.3/0.2 composite.
    Also pulls fresh news/onchain via existing fetchers (with fallback handling).
    """
    stars = int(tv_signal.get("quality_stars", 0))
    tv_base = (stars - 2.5) * 1.2  # -3 to +3
    direction = 1 if str(tv_signal.get("signal","")).upper() in ["BUY","STRONG_BUY","STRONG BUY","HIGH_QUALITY"] else -1
    # handle SELL signals: SELL, STRONG_SELL, DOWN
    sig_upper = str(tv_signal.get("signal","")).upper()
    if sig_upper in ["SELL","STRONG_SELL","DOWN","STRONG SELL","BEAR"]:
        direction = -1
    elif sig_upper in ["BUY","STRONG_BUY","STRONG BUY"]:
        direction = 1
    # MTF_BULL/BEAR etc.
    if "MTF_BULL" in sig_upper:
        direction = 1
        tv_base = abs(tv_base)
    if "MTF_BEAR" in sig_upper:
        direction = -1
        tv_base = abs(tv_base)
    tv_score = tv_base * direction

    # Fresh news score for that coin (lightweight: fetch global but filter)
    cfg_watch = config.get("watchlist", [])
    news_score = 0.0
    onchain_score = 0.0
    news_top = []
    onchain_events = []
    try:
        # Use existing fetch_news but we need score, not just items.
        # For performance, we call evaluate_news with current items if state_manager has recent news? Instead fetch fresh.
        # Keep it simple: fetch news (may be cached via dedup) and evaluate
        news_items, _ = fetch_news(config, state_manager=state_manager, api_key=None)
        # Filter to coin-relevant if possible: check title contains coin symbol
        coin_sym = coin_id.split("-")[0][:4].lower()
        relevant = [n for n in news_items if coin_sym in n.get("title","").lower() or coin_id[:3] in n.get("title","").lower()]
        eval_source = relevant if relevant else news_items
        from src.signals.news_signals import evaluate_news as eval_news
        news_res = eval_news(eval_source, config)
        news_score = float(news_res.get("score",0))
        news_top = news_res.get("top_news", [])[:2]
    except Exception as e:
        logger.warning(f"TV news enrich fail for {coin_id}: {e}")

    try:
        events = fetch_onchain(config)
        from src.signals.onchain_signals import evaluate_onchain as eval_oc
        oc_res = eval_oc(events, config)
        onchain_score = float(oc_res.get("score",0))
        onchain_events = oc_res.get("events", [])[:2]
    except Exception as e:
        logger.warning(f"TV onchain enrich fail for {coin_id}: {e}")

    # weights from config tradingview section or fallback
    tv_cfg = config.get("tradingview", {})
    w_tv = float(tv_cfg.get("tv_signal_weight", 0.50))
    w_news = float(tv_cfg.get("news_weight_tv", 0.30))
    w_oc = float(tv_cfg.get("onchain_weight_tv", 0.20))
    s = w_tv + w_news + w_oc
    if abs(s - 1.0) > 0.01 and s != 0:
        w_tv, w_news, w_oc = w_tv/s, w_news/s, w_oc/s

    composite = tv_score * w_tv + news_score * w_news + onchain_score * w_oc
    label = composite_label(composite)
    # strength for telegram
    ab = abs(composite)
    if ab >= 4:
        c_stars = 5
        c_strength = "VERY STRONG"
    elif ab >= 3:
        c_stars = 4
        c_strength = "STRONG"
    elif ab >= 1.5:
        c_stars = 3
        c_strength = "MODERATE"
    elif ab >= 0.7:
        c_stars = 2
        c_strength = "WEAK"
    else:
        c_stars = 1
        c_strength = "VERY WEAK"
    c_stars_str = "★" * c_stars + "☆" * (5 - c_stars)

    return {
        "composite_score": float(round(composite, 2)),
        "tv_score": float(round(tv_score,2)),
        "news_score": float(round(news_score,2)),
        "onchain_score": float(round(onchain_score,2)),
        "signal_label": label,
        "source": "tradingview_webhook",
        "news_top": news_top,
        "onchain_events": onchain_events,
        "stars": stars,
        "coin_id": coin_id,
        "direction": direction,
        "weights": {"tv": w_tv, "news": w_news, "onchain": w_oc},
        "c_stars": c_stars,
        "c_stars_str": c_stars_str,
        "c_strength": c_strength,
    }


def score_to_label(composite):
    return composite_label(composite)


def should_alert_tv(tv_signal, combined, config, state_manager, coin_id):
    """Check min_quality_stars + min_signal_to_alert + cooldown"""
    tv_cfg = config.get("tradingview", {})
    min_stars = int(tv_cfg.get("min_quality_stars", 3))
    stars = int(tv_signal.get("quality_stars", 0))
    if stars < min_stars:
        return False, f"stars {stars}<min {min_stars}"
    # also check composite label meets min_signal_to_alert from main alerts config
    from src.signals.signal_engine import meets_min_signal
    min_sig = config.get("alerts", {}).get("min_signal_to_alert", "BEARISH")
    label = combined.get("signal_label", "NEUTRAL")
    if not meets_min_signal(label, min_sig):
        return False, f"label {label} below min {min_sig}"
    # cooldown via state_manager (same as python signals)
    cooldown = config.get("alerts", {}).get("cooldown_minutes", 60)
    should, reason = state_manager.should_alert(coin_id, label, cooldown_minutes=cooldown)
    if not should:
        return False, f"cooldown {reason}"
    return True, f"stars {stars} label {label} {reason}"


def should_send_tv_alert(tv_signal: dict, coin_id: str, config: dict, state: dict) -> tuple[bool, str]:
    """
    Returns (should_send, reason)
    Requires Python-side confirmation of TV signal before alerting.
    """
    tv_cfg = config.get("tradingview", {})
    stars = tv_signal.get("quality_stars", 0)
    try:
        stars = int(stars)
    except:
        stars = 0
    min_stars = tv_cfg.get("min_quality_stars", 4)
    if stars < min_stars:
        return False, f"Quality {stars}★ below minimum {min_stars}★"
    if tv_cfg.get("require_python_confirmation", True):
        coin_state = state.get("price_history", {}).get(coin_id, [])
        if len(coin_state) < 2:
            return False, "Insufficient price history for confirmation"
        latest = coin_state[-1]
        python_score = latest.get("last_price_score", 0)
        # if not stored, try to compute fresh via price_signals if history available
        if python_score == 0 and len(coin_state) >= 2:
            try:
                from src.signals.price_signals import evaluate_price
                # need price/volume from latest
                price = latest.get("price", 0)
                vol = latest.get("volume", 0)
                hist = coin_state[:-1]
                # quick evaluate with current config
                res = evaluate_price(coin_id, price, vol, hist, config)
                python_score = res.get("score", 0)
            except Exception:
                python_score = 0
        min_confirm = tv_cfg.get("python_confirmation_min_score", 1.5)
        tv_direction = 1 if str(tv_signal.get("signal","")).upper() in ("BUY", "STRONG_BUY") else -1
        # handle SELL variations
        if str(tv_signal.get("signal","")).upper() in ("SELL","STRONG_SELL","DOWN"):
            tv_direction = -1
        python_direction = 1 if python_score > 0 else -1 if python_score < 0 else 0
        if python_direction == 0:
            return False, f"Python score {python_score:.2f} neutral, need confirmation"
        if tv_direction != python_direction:
            return False, f"TV says {tv_signal.get('signal')} but Python score is {python_score:.2f} (disagrees)"
        if abs(python_score) < min_confirm:
            return False, f"Python score {python_score:.2f} too weak to confirm TV signal"
    return True, "All checks passed"


def update_performance(state_manager, coin_id, tv_signal, combined, action_taken):
    """Append to tv_signals history, update performance stub (winrate tracked later by price resolver)."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin_id,
        "tv_signal": tv_signal.get("signal"),
        "strength": tv_signal.get("strength"),
        "quality_stars": tv_signal.get("quality_stars"),
        "tv_score": combined.get("tv_score"),
        "news_score": combined.get("news_score"),
        "onchain_score": combined.get("onchain_score"),
        "composite": combined.get("composite_score"),
        "label": combined.get("signal_label"),
        "price": tv_signal.get("price"),
        "mtf_score": tv_signal.get("mtf_score"),
        "winrate": tv_signal.get("winrate"),
        "action_taken": action_taken,
    }
    st = state_manager.state
    st.setdefault("tv_signals", []).append(entry)
    if len(st["tv_signals"]) > 200:
        st["tv_signals"] = st["tv_signals"][-200:]
    # also ensure performance key exists
    perf = st.setdefault("performance", {"total_signals":0,"wins":0,"losses":0,"tp1":0,"tp2":0,"tp3":0,"sl":0,"by_coin":{}})
    perf["total_signals"] = len(st["tv_signals"])
    # per-coin count
    by_coin = perf.setdefault("by_coin", {})
    by_coin[coin_id] = by_coin.get(coin_id, 0) + 1
    state_manager.save()
    return entry
