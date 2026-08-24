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


def _fmt_price(p):
    try:
        v = float(p)
        if v >= 1000:
            return f"${v:,.2f}"
        elif v >= 1:
            return f"${v:,.2f}"
        elif v >= 0.01:
            return f"${v:,.4f}"
        elif v >= 0.0001:
            return f"${v:,.6f}"
        else:
            return f"${v:,.8f}"
    except:
        return str(p)

def _esc_html(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _human_impact(score):
    # Convert raw score to human impact for news/signal
    try:
        v = float(score)
    except:
        return "Unknown"
    a = abs(v)
    direction = "Bullish" if v > 0 else "Bearish" if v < 0 else "Neutral"
    if a >= 3:
        level = "HIGH"
    elif a >= 2:
        level = "MEDIUM"
    elif a >= 0.5:
        level = "LOW"
    else:
        level = "NEGLIGIBLE"
        direction = "Neutral"
    return f"{level} {direction}"

def format_telegram(coin, price, change_24h, signal_info, price_details, news_top, onchain_events, disclaimer=True):
    emoji = signal_info.get("emoji", "⚪")
    signal = signal_info.get("signal", "NEUTRAL")
    score = signal_info.get("composite_score", 0)
    strength = signal_info.get("strength", "")
    stars_str = signal_info.get("stars_str", "")
    # Prefer TV quality stars if present (more intuitive than internal composite stars)
    tv_qs = signal_info.get("quality_stars")
    if tv_qs is not None:
        try:
            qs = int(tv_qs)
            stars_str = "★"*qs + "☆"*(5-qs)
            # keep strength from composite but append TV quality
            strength = f"{strength} (TV {qs}★)" if strength else f"TV {qs}★"
        except:
            pass
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
    # Timeframe & winrate (user requested)
    timeframe = signal_info.get("timeframe") or f"{signal_info.get('poll_interval',30)}m"
    # Normalize timeframe display: 15 -> 15m, 60 -> 1H etc.
    tf_display = str(timeframe)
    if tf_display.isdigit():
        tf_display = tf_display + "m" if int(tf_display) < 500 else tf_display
    mode = ""
    try:
        tf_num = int(str(timeframe).replace("m","").replace("H","").strip())
        if tf_num <= 5:
            mode = "SCALPING"
        elif tf_num <= 60:
            mode = "INTRADAY"
        elif tf_num <= 240:
            mode = "SWING"
        else:
            mode = "POSITION"
    except:
        mode = ""
    winrate = signal_info.get("winrate")
    if winrate is None:
        winrate = signal_info.get("performance_winrate")
    winrate_str = f"{winrate:.1f}%" if isinstance(winrate, (int,float)) else "n/a" if winrate is None else str(winrate)
    # also try from state performance
    if winrate_str == "n/a":
        try:
            import json, pathlib
            j = json.loads(pathlib.Path("state.json").read_text(encoding="utf-8"))
            wr = j.get("performance", {}).get("winrate")
            if wr:
                winrate_str = f"{wr:.1f}%"
        except:
            pass
    # price/volume
    vol = signal_info.get("volume")
    vol_avg = signal_info.get("volume_avg")
    vol_str = ""
    if vol:
        vol_str = f"${vol:,.0f}"
        if vol_avg:
            vol_str += f" (avg {vol_avg:,.0f}, {vol/vol_avg:.1f}x)"
    # Human impact instead of raw score
    impact = _human_impact(score)
    # Confidence from stars
    conf = f"{stars_str} {strength}" if stars_str else strength
    lines = []
    # Header with strength & winrate
    header_extra = f" {conf}" if conf else ""
    lines.append(f"{emoji} <b>{_esc_html(coin.upper())} — {_esc_html(label)}{_esc_html(header_extra)}</b>")
    lines.append(f"⏱ {_esc_html(tf_display)} { _esc_html(mode) + ' • ' if mode else ''}Winrate: {_esc_html(winrate_str)} • Impact: <b>{_esc_html(impact)}</b>")
    _p = price if isinstance(price, (int,float)) else 0
    _ch = change_24h if isinstance(change_24h, (int,float)) else 0
    lines.append(f"<code>Price: {_fmt_price(_p)}  24h: {_ch:+.2f}%  Confidence: {conf or 'n/a'}</code>")
    lines.append(f"<code>RSI: {rsi_str}  MACD: {macd_str}{macd_hist}  Vol: {vol_str or 'n/a'}</code>")
    # ── Trade Levels block — bold Entry/SL/TP in order, spaced ──
    entry = signal_info.get("entry") or signal_info.get("price_entry") or price
    sl = signal_info.get("stop_loss") or signal_info.get("sl")
    tp1 = signal_info.get("tp1")
    tp2 = signal_info.get("tp2")
    tp3 = signal_info.get("tp3")
    if any([entry, sl, tp1, tp2, tp3]):
        lines.append("")
        lines.append("<b>🎯 Trade Levels:</b>")
        # helpers for distance
        def _dist(target):
            try:
                return (float(target) - float(entry)) / float(entry) * 100 if entry else 0
            except:
                return 0
        if entry:
            try:
                lines.append(f"<b>Entry:</b> <code>{_fmt_price(entry)}</code>")
            except:
                lines.append(f"<b>Entry:</b> {_esc_html(str(entry))}")
        if sl is not None:
            try:
                d = _dist(sl)
                lines.append(f"<b>Stop Loss:</b> <code>{_fmt_price(sl)}</code> ({d:+.2f}%)")
            except:
                lines.append(f"<b>Stop Loss:</b> {_esc_html(str(sl))}")
        if tp1 is not None:
            try:
                d = _dist(tp1)
                lines.append(f"<b>TP1:</b> <code>{_fmt_price(tp1)}</code> ({d:+.2f}%)")
            except:
                lines.append(f"<b>TP1:</b> {_esc_html(str(tp1))}")
        if tp2 is not None:
            try:
                d = _dist(tp2)
                lines.append(f"<b>TP2:</b> <code>{_fmt_price(tp2)}</code> ({d:+.2f}%)")
            except:
                lines.append(f"<b>TP2:</b> {_esc_html(str(tp2))}")
        if tp3 is not None:
            try:
                d = _dist(tp3)
                lines.append(f"<b>TP3:</b> <code>{_fmt_price(tp3)}</code> ({d:+.2f}%)")
            except:
                lines.append(f"<b>TP3:</b> {_esc_html(str(tp3))}")
        # RR if we have entry/sl/tp1
        try:
            if entry and sl and tp1:
                risk = abs(float(entry) - float(sl))
                reward = abs(float(tp1) - float(entry))
                if risk > 0:
                    rr = reward / risk
                    lines.append(f"<i>RR TP1: 1:{rr:.2f}</i>")
        except:
            pass
        lines.append("")  # spacing after levels
    if price_details:
        pd = ", ".join(price_details[:3])
        lines.append(f"Triggers: {_esc_html(pd)}")
    # News with impact and links
    if news_top:
        lines.append("<b>📰 Big News (impact + link):</b>")
        for n in news_top[:3]:
            title = _esc_html(n.get("title","")[:110])
            link = n.get("link","")
            src = ",".join(n.get("sources", [n.get("source","")])) if n.get("sources") else n.get("source","")
            raw_score = n.get("news_score", n.get("score",0))
            impact_n = _human_impact(raw_score)
            src_esc = _esc_html(src)
            # link as Read →
            if link:
                lines.append(f"• {title}\n  └ <a href=\"{link}\">Read → {src_esc}</a> | Impact: <b>{_esc_html(impact_n)}</b> ({raw_score:+.1f})")
            else:
                lines.append(f"• {title} | Impact: <b>{_esc_html(impact_n)}</b>")
    else:
        lines.append("<i>No big news — price-only signal</i>")
    if onchain_events:
        lines.append("<b>⛓️ On-chain:</b>")
        for e in onchain_events[:2]:
            t = _esc_html(e.get('title','')[:100])
            sc = e.get('score','')
            imp = _human_impact(sc)
            lines.append(f"• {t} | Impact: <b>{_esc_html(imp)}</b> ({sc:+})")
    # Time
    lines.append(f"<i>Time: {_esc_html(str(signal_info.get('timestamp','now'))[:16].replace('T',' '))}  Next in {signal_info.get('poll_interval',30)}m  Africa/Algiers</i>")
    if disclaimer:
        lines.append("⚠️ <i>Big signals only. Rule-based. Not financial advice. DYOR.</i>")
    return "\n".join(lines)
