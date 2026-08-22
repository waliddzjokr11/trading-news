#!/usr/bin/env python3
"""
webhook_server.py — Flask receiver for TradingView alerts → signal_engine + Telegram
Run: python src/webhook_server.py  (local, ngrok) or via Render gunicorn
Endpoint: POST /webhook/tradingview  Header X-TV-Secret == WEBHOOK_SECRET
Health: GET /health  (for Render + GitHub keep-alive)
"""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

# ensure src on path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import yaml

# Load env from project root
try:
    _root = Path(__file__).resolve().parent.parent
    load_dotenv(dotenv_path=_root / ".env", override=True)
    load_dotenv(override=True)
except Exception:
    pass

from state.state_manager import StateManager
from alerts.telegram_sender import send_telegram, format_telegram
from alerts.email_sender import send_email, build_html
from alerts.tradingview_webhook import normalize_coin, compute_tv_combined_score, should_alert_tv, should_send_tv_alert, update_performance

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("webhook")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")

def load_config():
    p = Path(CONFIG_PATH)
    if not p.exists():
        p = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok", "time": datetime.now(timezone.utc).isoformat(), "service":"crypto-alert-webhook"}), 200

@app.route("/", methods=["GET"])
def index():
    return jsonify({"service":"crypto-alert-webhook","endpoints":["POST /webhook/tradingview","GET /health","GET /webhook/status"],"docs":"See pine_script/kdrx_indicator_v2.pine"}), 200

@app.route("/webhook/status", methods=["GET"])
def status():
    try:
        st = StateManager("state.json")
        tv = st.state.get("tv_signals", [])[-5:]
        perf = st.state.get("performance", {})
        return jsonify({"tv_last_5": tv, "performance": perf, "run_count": st.state.get("run_count")}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    # Auth
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        got = request.headers.get("X-TV-Secret") or request.headers.get("X-Tv-Secret") or request.args.get("secret", "")
        if got != secret:
            logger.warning(f"Webhook auth fail from {request.remote_addr}")
            return jsonify({"error":"unauthorized"}), 401

    # Parse JSON (TradingView sends JSON string, sometimes as text)
    try:
        data = request.get_json(force=True, silent=False)
        if data is None:
            # try raw
            data = json.loads(request.data.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Webhook bad JSON: {e} raw={request.data[:300]}")
        return jsonify({"error":"bad json","detail":str(e)}), 400

    # TradingView placeholders: {{ticker}} etc. may be literal if not replaced; handle
    # Expected keys: signal, strength, quality_stars, coin, timeframe, price, stop_loss, tp1, tp2, tp3, rsi, macd_direction, mtf_score
    logger.info(f"Webhook received: {data}")

    # Minimal validation
    if not isinstance(data, dict) or "signal" not in data:
        return jsonify({"error":"missing signal"}), 400

    cfg = load_config()
    if not cfg.get("tradingview", {}).get("webhook_enabled", True):
        return jsonify({"error":"webhook disabled in config"}), 403

    # Normalize coin
    tv_coin = str(data.get("coin") or data.get("ticker") or data.get("symbol") or "unknown")
    coin_id = normalize_coin(tv_coin, cfg.get("watchlist", []))
    logger.info(f"TV coin {tv_coin} -> {coin_id}")

    # Enrich + combined score
    st = StateManager("state.json")
    combined = compute_tv_combined_score(data, coin_id, cfg, state_manager=st)
    logger.info(f"Combined {coin_id}: tv {combined['tv_score']} news {combined['news_score']} onchain {combined['onchain_score']} => {combined['composite_score']} {combined['signal_label']}")

    # Should alert? Check Python confirmation gate first (winrate fix)
    ok_py, reason_py = should_send_tv_alert(data, coin_id, cfg, st.state)
    if not ok_py:
        logger.info(f"TV alert suppressed by Python confirmation: {reason_py}")
        should, reason = False, reason_py
    else:
        should, reason = should_alert_tv(data, combined, cfg, st, coin_id)
    action_taken = "pending"
    tg_ok = False
    email_ok = False

    if should:
        # Build alert payloads
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        gmail_user = os.environ.get("GMAIL_USER")
        gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
        email_to = os.environ.get("ALERT_EMAIL_TO")

        # Use telegram_sender format but adapt for TV: create signal_info with full strength/news + trade levels
        signal_info = {
            "emoji": {"DUMP_WARNING":"🔴","BEARISH":"🟠","NEUTRAL":"🟡","BULLISH":"🟢","STRONG_BUY":"🚀"}.get(combined["signal_label"],"⚪"),
            "signal": combined["signal_label"],
            "composite_score": combined["composite_score"],
            "color": {"DUMP_WARNING":"#ff2a2a","BEARISH":"#ff8c00","NEUTRAL":"#f5c518","BULLISH":"#2ecc71","STRONG_BUY":"#00ff88"}.get(combined["signal_label"],"#888"),
            "rsi": data.get("rsi"),
            "macd": {"direction": data.get("macd_direction")},
            "weights": combined["weights"],
            "timestamp": data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "poll_interval": cfg.get("poll_interval_minutes",30),
            "timeframe": data.get("timeframe"),
            "winrate": data.get("winrate"),
            "details": {"price": [f"TV {data.get('signal')} {data.get('quality_stars')}★ MTF {data.get('mtf_score','')} WR {data.get('winrate','')}%"]},
            "top_news": combined.get("news_top", []),
            "onchain_events": combined.get("onchain_events", []),
            "strength": combined.get("c_strength", ""),
            "stars": combined.get("c_stars", 0),
            "stars_str": combined.get("c_stars_str", ""),
            "quality_stars": data.get("quality_stars"),
            "price_score": combined.get("tv_score", 0),
            "news_score": combined.get("news_score", 0),
            "onchain_score": combined.get("onchain_score", 0),
            "entry": data.get("price"),
            "stop_loss": data.get("stop_loss"),
            "tp1": data.get("tp1"),
            "tp2": data.get("tp2"),
            "tp3": data.get("tp3"),
            "sl": data.get("stop_loss"),
        }
        price = float(data.get("price") or 0)
        # change_24h not in TV payload, use 0
        tg_msg = format_telegram(coin_id, price, 0, signal_info, signal_info["details"]["price"], signal_info["top_news"], signal_info["onchain_events"])
        # prepend TV context
        header = f"📡 <b>TradingView</b> {data.get('signal')} {data.get('quality_stars',0)}★ {tv_coin} {data.get('timeframe','')}  MTF {data.get('mtf_score','')}  WR {data.get('winrate','')}%\n"
        tg_msg = header + tg_msg

        # Telegram primary
        if cfg.get("alerts", {}).get("telegram_enabled", True):
            tg_ok = send_telegram(tg_token, tg_chat, tg_msg, parse_mode="HTML")
        # Email secondary
        if cfg.get("alerts", {}).get("email_enabled", True):
            # Build HTML email via existing builder (reuse)
            html = build_html(coin_id, price, 0, None, None, signal_info, signal_info["details"]["price"], signal_info["top_news"], signal_info["onchain_events"])
            # Inject TV JSON details into email top
            tv_details = f"<div style='background:#1a1d24;padding:10px;border-radius:8px;margin:10px 0'><b>TradingView Raw:</b><pre style='font-size:11px;overflow:auto'>{json.dumps(data, indent=2)}</pre><b>Combined:</b> {combined}</div>"
            html = html.replace("<div style=\"background:#242831", tv_details + "<div style=\"background:#242831", 1)
            subject = f"[TV] {signal_info['emoji']} {coin_id.upper()} — {combined['signal_label']} ({data.get('quality_stars')}★)"
            email_ok = send_email(gmail_user, gmail_pass, email_to, subject, html)
        else:
            email_ok = True  # skip but count as not needed

        if tg_ok or email_ok:
            st.record_alert(coin_id, combined["signal_label"], combined["composite_score"])
            action_taken = "Alert Sent"
        else:
            action_taken = "Alert Failed"
    else:
        action_taken = f"Below threshold: {reason}"
        logger.info(f"TV alert suppressed: {reason}")

    # Log to state anyway
    update_performance(st, coin_id, data, combined, action_taken)
    # also record run-like health?
    # Keep-alive: update last_run
    st.save()

    return jsonify({
        "ok": True,
        "coin_id": coin_id,
        "tv_signal": data.get("signal"),
        "quality_stars": data.get("quality_stars"),
        "combined_score": combined["composite_score"],
        "signal_label": combined["signal_label"],
        "action_taken": action_taken,
        "telegram_sent": tg_ok,
        "email_sent": email_ok,
        "reason": reason if not should else "sent",
    }), 200

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error":"not found","endpoints":["POST /webhook/tradingview","GET /health"]}), 404

if __name__ == "__main__":
    cfg = load_config()
    port = int(os.environ.get("PORT") or cfg.get("tradingview", {}).get("webhook_port", 5000))
    host = "0.0.0.0"
    logger.info(f"Starting webhook server on {host}:{port}  secret={'set' if os.environ.get('WEBHOOK_SECRET') else 'NOT SET (open)'}  config webhook_enabled={cfg.get('tradingview',{}).get('webhook_enabled')}")
    app.run(host=host, port=port, debug=False)
