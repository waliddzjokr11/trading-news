"""
generate_dashboard.py — static dark-theme HTML, mobile-friendly, auto-refresh 5m
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Crypto Alert Dashboard</title>
<style>
:root{--bg:#0f1115;--card:#1a1d24;--muted:#9aa0a6;--line:#2a2e39}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:var(--bg);color:#e6e6e6}
.header{padding:18px 14px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
.badge{padding:4px 8px;border-radius:6px;font-size:12px;font-weight:700;color:#111}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;padding:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--muted);border-bottom:1px solid var(--line);padding:6px}td{padding:6px;border-bottom:1px solid #222}
.small{color:var(--muted);font-size:12px}
.search{width:100%;padding:10px;border-radius:8px;border:1px solid var(--line);background:#0f1115;color:#fff;margin:10px 0}
.pill{padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700}
.kpi{display:flex;gap:10px;flex-wrap:wrap;padding:14px}
.kpi div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
a{color:#8ab4f8}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style>
</head><body>
<div class="header">
<h2 style="margin:0">📊 Crypto Alert Dashboard <span class="small">auto-refresh 5m</span></h2>
<div class="small">Last updated: {{last_updated}} &nbsp;|&nbsp; Next run in ~{{poll_interval}}m &nbsp;|&nbsp; Source: {{last_source}} &nbsp;|&nbsp; Runs: {{run_count}} &nbsp;|&nbsp; Alerts: {{total_alerts}} &nbsp;|&nbsp; Uptime: {{uptime}}%</div>
<input id="q" class="search" placeholder="Search coin (btc, sol, atom...)" oninput="filter()">
</div>

<div class="kpi">
<div><b>{{watchlist_count}} coins</b> watched</div>
<div><b>{{last_alerts_24h}} alerts</b> last 24h</div>
<div><b>{{news_count}} news</b> deduped</div>
<div><b>{{onchain_count}} on-chain</b> events</div>
</div>

<h3 style="padding:0 14px">Watchlist — 100 coins</h3>
<div class="grid" id="grid">
{{cards}}
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:14px" id="bottom">
<div class="card">
<h3>Alert History (last 50)</h3>
<table><tr><th>Time</th><th>Coin</th><th>Signal</th><th>Score</th></tr>
{{alert_rows}}
</table>
</div>
<div class="card">
<h3>System Health (last 10 runs)</h3>
<table><tr><th>Time</th><th>Status</th><th>Source</th><th>Coins</th><th>Alerts</th></tr>
{{health_rows}}
</table>
<div class="small" style="margin-top:8px">Logs: truncated — see GitHub Actions</div>
</div>
</div>

<div class="card" style="margin:14px">
<h3>News Feed (last 20)</h3>
<table><tr><th>Title</th><th>Source</th><th>Score</th></tr>
{{news_rows}}
</table>
</div>

<div class="small" style="padding:14px;text-align:center">⚠️ Rule-based signals only. Not financial advice. DYOR. — Africa/Algiers</div>

<script>
function filter(){
  let q=document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('#grid .card').forEach(c=>{
    let t=c.getAttribute('data-coin');
    c.style.display = t.includes(q) ? '' : 'none';
  });
}
</script>
</body></html>
"""


def generate_dashboard(config, state, prices, signals, news_items, onchain_events, out_path="dashboard/index.html"):
    state_path = Path(state.path) if hasattr(state, "path") else Path("state.json")
    # compute metrics
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    poll_interval = config.get("poll_interval_minutes", 30)
    run_history = state.state.get("run_history", [])
    last_source = run_history[-1].get("source", "n/a") if run_history else "n/a"
    run_count = state.state.get("run_count", 0)
    total_alerts = len(state.state.get("alert_history", []))
    # uptime = % success in last 20
    recent = run_history[-20:] if run_history else []
    uptime = round(100 * sum(1 for r in recent if r.get("status")=="success") / len(recent), 1) if recent else 100.0
    watchlist_count = len(config.get("watchlist", []))
    # alerts last 24h
    from datetime import datetime as dt, timezone as tz, timedelta
    cutoff = dt.now(tz.utc) - timedelta(hours=24)
    last_alerts_24h = 0
    for a in state.state.get("alert_history", []):
        try:
            ts = dt.fromisoformat(a["timestamp"])
            if ts.tzinfo is None: ts = ts.replace(tzinfo=tz.utc)
            if ts > cutoff: last_alerts_24h+=1
        except: pass
    news_count = len(news_items) if news_items else 0
    onchain_count = len(onchain_events) if onchain_events else 0

    # cards per coin
    cards_html = ""
    # signals is dict coin -> signal dict
    if not signals:
        cards_html = '<div class="card small">No signals this run (all sources failed or dry-run mock pending)</div>'
    else:
        for coin, sig in sorted(signals.items()):
            price = prices.get(coin, {})
            p = price.get("price", 0) or 0
            ch = price.get("change_24h", 0) or 0
            sig_label = sig.get("signal","NEUTRAL")
            color = sig.get("color","#888")
            emoji = sig.get("emoji","⚪")
            score = sig.get("composite_score",0)
            rsi = sig.get("rsi")
            macd = sig.get("macd") or {}
            rsi_s = f"{rsi:.1f}" if rsi is not None else "n/a"
            macd_s = macd.get("direction","n/a")
            last_alert = state.state.get("last_alert", {}).get(coin, {})
            last_a = last_alert.get("signal","—") if last_alert else "—"
            ch_color = "#2ecc71" if ch>=0 else "#ff4d4d"
            det = sig.get("details") or {}
            price_det = ", ".join(det.get("price", [])[:1]) or "—"
            cards_html += f"""
<div class="card" data-coin="{coin}">
<div style="display:flex;justify-content:space-between;align-items:center">
<b>{coin}</b> <span class="badge" style="background:{color}">{emoji} {sig_label}</span>
</div>
<div style="margin:8px 0;font-size:14px">${p:,.2f} <span style="color:{ch_color}">{ch:+.2f}% 24h</span></div>
<div class="small">Score {score:+.2f} &nbsp; RSI {rsi_s} &nbsp; MACD {macd_s}</div>
<div class="small" style="margin-top:6px">Last alert: {last_a}</div>
<div class="small">Price: {price_det}</div>
</div>
"""

    # alert rows
    alert_rows = ""
    for a in reversed(state.state.get("alert_history", [])[-50:]):
        ts = a.get("timestamp","")[:16].replace("T"," ")
        coin = a.get("coin","")
        sig = a.get("signal","")
        sc = a.get("score",0)
        col = {"DUMP_WARNING":"#ff2a2a","BEARISH":"#ff8c00","NEUTRAL":"#f5c518","BULLISH":"#2ecc71","STRONG_BUY":"#00ff88"}.get(sig,"#888")
        alert_rows += f"<tr><td>{ts}</td><td>{coin}</td><td><span style='color:{col}'>{sig}</span></td><td>{sc:+.2f}</td></tr>"
    if not alert_rows:
        alert_rows = "<tr><td colspan='4' class='small'>No alerts yet</td></tr>"

    # health rows
    health_rows = ""
    for r in reversed(run_history[-10:]):
        ts = r.get("timestamp","")[:16].replace("T"," ")
        st = r.get("status","")
        src = r.get("source","")
        coins = r.get("coins","")
        alerts = r.get("alerts","")
        clr = "#2ecc71" if st=="success" else "#ff4d4d" if st=="fail" else "#f5c518"
        health_rows += f"<tr><td>{ts}</td><td style='color:{clr}'>{st}</td><td>{src}</td><td>{coins}</td><td>{alerts}</td></tr>"
    if not health_rows:
        health_rows = "<tr><td colspan='5' class='small'>No runs yet</td></tr>"

    # news rows
    news_rows = ""
    for n in (news_items or [])[:20]:
        title = (n.get("title","")[:90]).replace("<","&lt;")
        src = ",".join(n.get("sources", [n.get("source","")])) if n.get("sources") else n.get("source","")
        link = n.get("link","#")
        score = n.get("news_score", n.get("score",""))
        if score == "": score = "—"
        else: 
            try: score = f"{float(score):+.1f}"
            except: pass
        news_rows += f"<tr><td><a href='{link}' target='_blank'>{title}</a></td><td>{src}</td><td>{score}</td></tr>"
    if not news_rows:
        news_rows = "<tr><td colspan='3' class='small'>No news this run</td></tr>"

    html = TEMPLATE.replace("{{last_updated}}", last_updated)\
        .replace("{{poll_interval}}", str(poll_interval))\
        .replace("{{last_source}}", last_source)\
        .replace("{{run_count}}", str(run_count))\
        .replace("{{total_alerts}}", str(total_alerts))\
        .replace("{{uptime}}", str(uptime))\
        .replace("{{watchlist_count}}", str(watchlist_count))\
        .replace("{{last_alerts_24h}}", str(last_alerts_24h))\
        .replace("{{news_count}}", str(news_count))\
        .replace("{{onchain_count}}", str(onchain_count))\
        .replace("{{cards}}", cards_html)\
        .replace("{{alert_rows}}", alert_rows)\
        .replace("{{health_rows}}", health_rows)\
        .replace("{{news_rows}}", news_rows)

    # ensure dashboard dir
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    # nojekyll for GitHub Pages
    try:
        (out.parent / ".nojekyll").touch(exist_ok=True)
    except: pass
    return str(out)
