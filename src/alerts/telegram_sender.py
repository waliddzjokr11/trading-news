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
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def format_telegram(coin, price, change_24h, signal_info, price_details, news_top, onchain_events, disclaimer=True):
    import html as _html
    emoji = signal_info.get("emoji", "⚪")
    signal = signal_info.get("signal", "NEUTRAL")
    score = signal_info.get("composite_score", 0)
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
    lines = []
    lines.append(f"{emoji} <b>{_esc_html(coin.upper())} — {_esc_html(label)}</b>")
    lines.append(f"<code>Price: ${price:,.2f}  24h: {change_24h:+.2f}%  Score: {score:+.2f}</code>")
    lines.append(f"<code>RSI: {rsi_str}  MACD: {macd_str}</code>")
    if price_details:
        pd = ", ".join(price_details[:2])
        lines.append(f"Price triggers: {_esc_html(pd)}")
    if news_top:
        lines.append("<b>Top news:</b>")
        for n in news_top[:2]:
            title = _esc_html(n.get("title","")[:90])
            link = n.get("link","")
            # Telegram HTML link
            if link:
                lines.append(f"• {title} <a href=\"{link}\">link</a>")
            else:
                lines.append(f"• {title}")
    if onchain_events:
        oc = _esc_html(onchain_events[0].get('title','')[:80])
        lines.append(f"On-chain: {oc}")
    lines.append(f"<i>Time: {_esc_html(str(signal_info.get('timestamp','now')))}  Next in {signal_info.get('poll_interval',30)}m</i>")
    if disclaimer:
        lines.append("⚠️ <i>Rule-based signal only. Not financial advice. DYOR.</i>")
    return "\n".join(lines)
