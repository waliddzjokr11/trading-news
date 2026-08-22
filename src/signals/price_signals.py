"""
price_signals.py — % change, volume spike, RSI(14), MACD(12,26,9)
Manual RSI/MACD via pandas EWM (no pandas_ta) for maximum reliability.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def _rsi(series, period=14):
    """Wilder's RSI. Returns last RSI value or None."""
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    # Wilder's smoothing = EMA with alpha=1/period
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


def _macd(series, fast=12, slow=26, signal=9):
    """MACD. Returns (macd_line, signal_line, histogram, direction) or None."""
    if len(series) < slow + signal:
        return None
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    # need at least 2 points to detect cross
    if len(hist) < 2:
        return None
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    direction = "bullish" if last_hist > 0 and prev_hist <= 0 else "bearish" if last_hist < 0 and prev_hist >= 0 else "neutral"
    # also if hist >0 bullish overall
    if direction == "neutral":
        direction = "bullish" if last_hist > 0 else "bearish" if last_hist < 0 else "neutral"
    return {
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "histogram": float(last_hist),
        "direction": direction,
    }


def evaluate_price(coin_id, current_price, current_volume, price_history, config):
    """
    price_history: list of {timestamp, price, volume} last 200
    config: dict
    Returns: {score, details, rsi, macd}
    """
    p_cfg = config.get("price", {})
    short_pct = p_cfg.get("short_change_pct", 3.0)
    medium_pct = p_cfg.get("medium_change_pct", 6.0)
    vol_mult = p_cfg.get("volume_multiplier", 2.5)
    rsi_oversold = p_cfg.get("rsi_oversold", 30)
    rsi_overbought = p_cfg.get("rsi_overbought", 70)
    rsi_period = p_cfg.get("rsi_period", 14)

    score = 0.0
    details = []

    # Build price series from history + current
    prices = [h["price"] for h in price_history] + [current_price]
    volumes = [h.get("volume", 0) for h in price_history] + [current_volume or 0]
    s = pd.Series(prices, dtype=float)

    # % change short (15m): need at least 2 points; approximate by last point vs current if history has 1 per run every 30m
    # Use 1-point change if history has 1 previous, else look back N points where N = short_window/poll_interval
    poll = config.get("poll_interval_minutes", 30)
    short_window = p_cfg.get("short_window_minutes", 15)
    medium_window = p_cfg.get("medium_window_minutes", 60)
    short_lookback = max(1, round(short_window / poll))
    medium_lookback = max(1, round(medium_window / poll))

    if len(prices) > short_lookback:
        old = prices[-(short_lookback + 1)]
        if old and old != 0:
            pct = (current_price - old) / old * 100
            if abs(pct) >= short_pct:
                # bearish if dump, bullish if pump — but magnitude matters
                if pct <= -short_pct:
                    score -= 2
                    details.append(f"short_change {pct:.2f}% (< -{short_pct}%)")
                elif pct >= short_pct:
                    score += 1.5
                    details.append(f"short_pump {pct:.2f}% (> +{short_pct}%)")

    if len(prices) > medium_lookback:
        old = prices[-(medium_lookback + 1)]
        if old and old != 0:
            pct = (current_price - old) / old * 100
            if abs(pct) >= medium_pct:
                if pct <= -medium_pct:
                    score -= 2
                    details.append(f"medium_dump {pct:.2f}%")
                else:
                    score += 2
                    details.append(f"medium_pump {pct:.2f}%")

    # volume spike
    if len(volumes) >= 6:
        avg_vol = sum(volumes[-6:-1]) / max(1, len(volumes[-6:-1]))
        if avg_vol > 0 and current_volume and current_volume / avg_vol >= vol_mult:
            # volume spike amplifies whichever direction price moved
            # if price down + volume up => bearish
            # if price up + volume up => bullish
            # Determine last price direction
            if len(prices) >= 2 and prices[-2] != 0:
                dir_up = current_price > prices[-2]
                if dir_up:
                    score += 1
                    details.append(f"volume_spike {current_volume/avg_vol:.1f}x bullish")
                else:
                    score -= 1
                    details.append(f"volume_spike {current_volume/avg_vol:.1f}x bearish")
            else:
                details.append(f"volume_spike {current_volume/avg_vol:.1f}x")

    # RSI
    rsi_val = _rsi(s, period=rsi_period)
    rsi_score = 0.0
    if rsi_val is not None:
        if rsi_val < rsi_oversold:
            rsi_score = 1.5
            details.append(f"RSI {rsi_val:.1f} oversold (<{rsi_oversold})")
        elif rsi_val > rsi_overbought:
            rsi_score = -1.5
            details.append(f"RSI {rsi_val:.1f} overbought (>{rsi_overbought})")

    # MACD
    macd_info = _macd(s, fast=p_cfg.get("macd_fast", 12), slow=p_cfg.get("macd_slow", 26), signal=p_cfg.get("macd_signal", 9))
    macd_score = 0.0
    macd_rising = False
    if macd_info:
        hist = macd_info.get("histogram", 0)
        # determine rising by comparing to previous hist if available
        try:
            # recompute hist series for rising check
            ema_fast = s.ewm(span=p_cfg.get("macd_fast", 12), adjust=False).mean()
            ema_slow = s.ewm(span=p_cfg.get("macd_slow", 26), adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=p_cfg.get("macd_signal", 9), adjust=False).mean()
            hist_series = macd_line - signal_line
            if len(hist_series) >= 2:
                macd_rising = hist_series.iloc[-1] > hist_series.iloc[-2]
        except:
            macd_rising = hist > 0
        if hist > 0 and macd_rising:
            macd_score = 1.0
            details.append(f"MACD bullish")
        elif hist < 0 and not macd_rising:
            macd_score = -1.0
            details.append(f"MACD bearish")
        # store rising for gate
        macd_info["rising"] = macd_rising

    # NEW: MACD+RSI agreement gate — require confirmation
    require_agreement = p_cfg.get("require_macd_rsi_agreement", True)
    if require_agreement:
        # For bullish price score, need at least one of RSI or MACD bullish
        if score > 0 and rsi_score <= 0 and macd_score <= 0:
            score *= 0.3
            details.append(f"price unconfirmed (no RSI/MACD) → score penalized 0.3x")
        elif score < 0 and rsi_score >= 0 and macd_score >= 0:
            score *= 0.3
            details.append(f"price unconfirmed (no RSI/MACD) → score penalized 0.3x")
        else:
            score += rsi_score + macd_score
    else:
        score += rsi_score + macd_score

    # cap
    score = max(-5, min(5, score))
    # compute volume vs avg for gate (also for quality gate)
    vol_vs_avg = 1.0
    if len(volumes) >= 6:
        avg_vol = sum(volumes[-6:-1]) / max(1, len(volumes[-6:-1]))
        if avg_vol > 0 and current_volume:
            vol_vs_avg = current_volume / avg_vol
    return {
        "score": float(score),
        "details": details,
        "rsi": rsi_val,
        "macd": macd_info,
        "short_lookback": short_lookback,
        "medium_lookback": medium_lookback,
        "volume_vs_avg": float(vol_vs_avg),
        "rsi_score": float(rsi_score),
        "macd_score": float(macd_score),
    }
