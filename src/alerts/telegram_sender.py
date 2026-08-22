"""
telegram_sender.py — Telegram BotFather, Markdown, retry
"""
import time
import logging
import requests

logger = logging.getLogger(__name__)


def send_telegram(bot_token, chat_id, message, parse_mode="Markdown"):
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
            logger.warning(f"Telegram fail {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.warning(f"Telegram exception attempt {attempt+1}: {e}")
        if attempt == 0:
            time.sleep(30)
    return False


def format_telegram(coin, price, change_24h, signal_info, price_details, news_top, onchain_events, disclaimer=True):
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
    lines.append(f"{emoji} *{coin.upper()} — {label}*")
    lines.append(f"`Price: ${price:,.2f}  24h: {change_24h:+.2f}%  Score: {score:+.2f}`")
    lines.append(f"`RSI: {rsi_str}  MACD: {macd_str}`")
    if price_details:
        lines.append(f"_Price triggers:_ {', '.join(price_details[:2])}")
    if news_top:
        lines.append("*Top news:*")
        for n in news_top[:2]:
            title = n.get("title","")[:90].replace("*","").replace("_","")
            link = n.get("link","")
            lines.append(f"• {title} [link]({link})")
    if onchain_events:
        lines.append(f"_On-chain:_ {onchain_events[0].get('title','')[:80]}")
    lines.append(f"_Time: {signal_info.get('timestamp','now')}  Next in {signal_info.get('poll_interval',30)}m_")
    if disclaimer:
        lines.append("⚠️ _Rule-based signal only. Not financial advice. DYOR._")
    return "\n".join(lines)
