"""
telegram_sender.py — Telegram BotFather, Markdown, retry
"""
import time
import logging
import requests

logger = logging.getLogger(__name__)


def send_telegram(bot_token, chat_id, message, parse_mode="HTML"):
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials missing — skip")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": False}
    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info("Telegram sent")
                return True
            # fallback to plain text if parse error
            if "can't parse" in resp.text.lower():
                logger.warning(f"Telegram HTML parse fail, retry plain: {resp.text[:200]}")
                payload_plain = {"chat_id": chat_id, "text": message, "disable_web_page_preview": False}
                # strip HTML tags for plain fallback: simple
                import re
                plain = re.sub(r"<[^>]+>", "", message)
                payload_plain["text"] = plain
                resp2 = requests.post(url, json=payload_plain, timeout=15)
                if resp2.status_code == 200:
                    logger.info("Telegram sent (plain fallback)")
                    return True
                logger.warning(f"Telegram plain also fail {resp2.status_code}: {resp2.text[:300]}")
            else:
                logger.warning(f"Telegram fail {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.warning(f"Telegram exception attempt {attempt+1}: {e}")
        if attempt == 0:
            time.sleep(30)
    return False


def _esc_html(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def format_telegram(coin, price, change_24h, signal_info, price_details, news_top, onchain_events, disclaimer=True):
    emoji = signal_info.get("emoji", "⚪")
    signal = signal_info.get("signal", "NEUTRAL")
    score = signal_info.get("composite_score", 0)
    strength = signal_info.get("strength", "")
    stars_str = signal_info.get("stars_str", "")
    # TV webhook already has quality_stars, prefer it if present
    if "quality_stars" in signal_info:
        try:
            qs = int(signal_info.get("quality_stars", 0))
            stars_str = "★"*qs + "☆"*(5-qs)
            strength = f"TV {qs}★"
        except: pass
    rsi = signal_info.get("rsi")
    macd = signal_info.get("macd")
    label_map = {
        "DUMP_WARNING": "DUMP WARNING — Consider Exiting",
        "BEARISH": "BEARISH — Caution / Watch",
        "NEUTRAL": "NEUTRAL — Hold / Monitor",
        "BULLISH": "BULLISH — Watch for Entry",
        "STRONG_BUY": "STRONG BUY — Entry Candidate",
    }
    label = label_map.get(signal, signal)
    rsi_str = f"{rsi:.1f}" if rsi is not None else "n/a"
    macd_str = macd.get("direction", "n/a") if macd else "n/a"
    macd_hist = f" {macd.get('histogram',0):+.3f}" if macd and macd.get('histogram') is not None else ""
    # price/volume details
    vol = signal_info.get("volume")
    vol_avg = signal_info.get("volume_avg")
    vol_str = ""
    if vol:
        vol_str = f"${vol:,.0f}"
        if vol_avg:
            vol_str += f" (avg {vol_avg:,.0f}, {vol/vol_avg:.1f}x)"
    # scores
    ps = signal_info.get("price_score", 0)
    ns = signal_info.get("news_score", 0)
    os_ = signal_info.get("onchain_score", 0)
    weights = signal_info.get("weights", {})
    lines = []
    # Header with strength
    header_strength = f" {strength} {stars_str}" if strength else f" {stars_str}" if stars_str else ""
    lines.append(f"{emoji} <b>{_esc_html(coin.upper())} — {_esc_html(label)}{_esc_html(header_strength)}</b>")
    lines.append(f"<code>Price: ${price:,.2f}  24h: {change_24h:+.2f}%  Score: {score:+.2f} ({strength})</code>")
    lines.append(f"<code>RSI: {rsi_str}  MACD: {macd_str}{macd_hist}  Vol: {vol_str or 'n/a'}</code>")
    lines.append(f"<code>Breakdown: price {ps:+.1f} news {ns:+.1f} onchain {os_:+.1f}  w {weights.get('price',0):.2f}/{weights.get('news',0):.2f}/{weights.get('onchain',0):.2f}</code>")
    if price_details:
        pd = ", ".join(price_details[:3])
        lines.append(f"Triggers: {_esc_html(pd)}")
    # News - up to 3 with scores
    if news_top:
        lines.append("<b>News:</b>")
        for n in news_top[:3]:
            title = _esc_html(n.get("title","")[:95])
            link = n.get("link","")
            src = ",".join(n.get("sources", [n.get("source","")])) if n.get("sources") else n.get("source","")
            score_n = n.get("news_score", n.get("score",""))
            try:
                score_n = f"{float(score_n):+.1f}"
            except:
                score_n = str(score_n)
            src_esc = _esc_html(src)
            if link:
                lines.append(f"• {title} <a href=\"{link}\">link</a> <i>({src_esc} {score_n})</i>")
            else:
                lines.append(f"• {title} <i>({src_esc} {score_n})</i>")
    else:
        lines.append("<i>No strong news this run</i>")
    if onchain_events:
        lines.append("<b>On-chain:</b>")
        for e in onchain_events[:2]:
            t = _esc_html(e.get('title','')[:90])
            sc = e.get('score','')
            lines.append(f"• {t} ({sc:+})")
    else:
        # only show if bearish/bullish signal to avoid clutter for NEUTRAL? But user wants all, so show placeholder
        if signal in ("DUMP_WARNING","BEARISH"):
            lines.append("<i>On-chain: none (check whale-alert)</i>")
    lines.append(f"<i>Time: {_esc_html(str(signal_info.get('timestamp','now'))[:16])}  Next in {signal_info.get('poll_interval',30)}m</i>")
    if disclaimer:
        lines.append("⚠️ <i>Rule-based. Not financial advice. DYOR. Africa/Algiers</i>")
    return "\n".join(lines)
