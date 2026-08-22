# Crypto News & Signal Alert System — 100 Coins, Halal-Focused

Fully automated, **$0 forever**, zero-server crypto monitor. Fetches price/volume + news + whale on-chain, computes rule-based signals, alerts via **Telegram + Email**, runs 24/7 on **GitHub Actions** (or local cron), with a live dashboard via GitHub Pages. Single `config.yaml` tuning — no code changes.

> ⚠️ Rule-based signals only. Not financial advice. DYOR. — Spec: 100-coin expanded watchlist (mostly halal, Binance top-vol, includes ATOM/TRX/OP/XRP/BNB/NEAR/TAO). Private repo defaults to **30m interval** to stay under 2,000 min/month free limit. Use public repo for 15m.

## Quick Start (20–30 min)

### 1. Create / Fork Repo
- Put this folder on GitHub as `trading-news` (private OK, but keep 30m interval). `git init` + push if greenfield.

### 2. Gmail App Password
- Google Account → Security → 2-Step Verification → **App Passwords** → create 16-char password for `GMAIL_APP_PASSWORD`. Use your Gmail as `GMAIL_USER`.

### 3. Telegram Bot
- Message **@BotFather** → `/newbot` → save `TELEGRAM_BOT_TOKEN`.
- Start chat with your bot, send any message, then get chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates` → find `chat.id` → `TELEGRAM_CHAT_ID`.

### 4. CryptoPanic Key (optional, free)
- https://cryptopanic.com/developers/api/ → free tier improves news.

### 5. GitHub Secrets
- Repo Settings → Secrets → Actions → New secret: `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL_TO`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CRYPTOPANIC_API_KEY` (optional).

### 6. Edit `config.yaml`
- Watchlist is 100 halal-filtered IDs — comment out any coin to disable, add `HOME` when you confirm CoinGecko ID. Tune `price.short_change_pct`, `weights` (must sum 1.0), `alerts.cooldown_minutes`, `alerts.min_signal_to_alert`, timezone `Africa/Algiers` already set for Oran.

### 7. Enable Actions
- Actions tab → enable workflows → **Run workflow** (manual trigger) to verify first run.

### 8. GitHub Pages (Dashboard)
- Settings → Pages → Source: **Deploy from branch** `main` → folder `/dashboard` (or `main` + `/ (root)` if you move `dashboard/index.html` — spec uses `/dashboard`). Wait 1–2 min → dashboard URL appears. Refresh every 5m.

### 9. Local Fallback (optional)
```bash
cp .env.example .env  # fill secrets
pip install -r requirements.txt
python src/main.py --dry-run   # must work with no secrets (mock data)
python src/main.py             # live local
# crontab 30m
# */30 * * * * cd "/path/to/trading news" && python src/main.py >> logs/cron.log 2>&1
# Windows Task Scheduler: point to python.exe + src/main.py
```

### 10. Verify
- Check Actions log, `state.json` updated, `dashboard/index.html` generated, Telegram/Email received on signal.

## Architecture

```
src/fetchers/price_fetcher.py   CoinGecko (chunk 50) → Binance → CoinCap, 429-aware
            news_fetcher.py     CryptoPanic + CoinDesk/CoinTelegraph/Decrypt RSS, dedup hash + multi-source boost
            onchain_fetcher.py  Whale Alert RSS, inflow/outflow scoring
src/signals/price_signals.py    % change 15m/60m, volume spike 2.5x, RSI(14) Wilder, MACD(12,26,9) manual via pandas EWM
            news_signals.py     Keyword tiers (-3/-2/-1, +3/+2/+1, 1.5x multi-source)
            onchain_signals.py  Exchange flows ±2
            signal_engine.py    composite = price*0.4 + news*0.35 + onchain*0.25 → DUMP/BEARISH/NEUTRAL/BULLISH/STRONG_BUY
src/alerts/telegram_sender.py + email_sender.py  retry 30s, HTML + Markdown, cooldown 60m, escalation bypass
src/state/state_manager.py      price_history 200, last_alert, seen_news_hash 24h TTL, alert_history 200, run_history 100
src/dashboard/generate_dashboard.py  dark mobile static HTML, search, kpis, health
src/main.py                     orchestrator, --dry-run mock, daily digest 08:00 Africa/Algiers
backtest.py                     replay CoinGecko market_chart, CSV + accuracy metrics
```

## Configuration

All in `config.yaml` — watchlist 100, `poll_interval_minutes: 30` (private) / 15 if public, price thresholds, weights, news TTL, keywords extras, alerts, sources. Change interval also update `.github/workflows/monitor.yml` cron.

## Cost & Limits

- All APIs free, no credit card. CoinGecko 30/min, Binance 1200/min, CoinCap 200/min — 100 coins = 1–2 calls per run, safe. GitHub Actions private 2,000 min/mo → 30m avoids overage; public unlimited. State persists via `state.json` commit `[skip ci]` + `git pull --rebase`.

## Backtesting

```bash
python backtest.py --coin bitcoin --days 90
# outputs backtest_bitcoin_90d.csv + % dump/buy accuracy
```

Tune `config.yaml` thresholds and re-run.

## KDRX v2 — TradingView Integration (Add-On)

### Pine Script Upgrade
File: `pine_script/kdrx_indicator_v2.pine` — paste into TradingView Pine Editor v5 → Add to chart. Preserves all original (Supertrend, MACD, EMA150/250, HMA55, pullback/contrarian, trailing SL, 3 TP, bar coloring) and adds:

- **Live Winrate Counter** `var` tracked: Signals / Wins / Losses / TP1-3 / SL / Winrate % / Avg RR — shown in Smart Panel. Color: green >55%, yellow 45–55%, red <45%. *Resets on timeframe change / history reload — Pine var limitation.*
- **MTF Confluence Panel** second table (top-left, toggle `showMTFPanel`): 1m,3m,5m,15m,30m,1H,4H,1D EMA200 vs price → `SCORE: 6/8 BULLISH 🟢` (≥6 bright green, 4–5 yellow, ≤3 red)
- **Quality Score** 0–5★ per label: +1 MACD aligned, +1 Trend, +1 HMA momentum, +1 Vol above avg, +1 MTF ≥5/8. Label shows `BUY ★★★★☆` + detail `✓ Trend ✓ MACD ✓ Vol ✗ MTF ✓ Mom`
- **Timeframe Mode Label** top-center: `⏱ 15m — SCALPING MODE` (1m–5m SCALPING, 15m–1H INTRADAY, 4H–1D SWING, 1W+ POSITION)
- **Enhanced Alerts** JSON for webhook (see below) — 12 alertconditions: `Buy/Sell`, `Strong Buy/Sell`, `High Quality Buy/Sell (≥4★)`, `TP1-3`, `SL`, `MTF Bull/Bear (≥6/8)`. All toggleable.

Apply indicator to chart, set timeframe 15m (INTRADAY) for scalps.

### Webhook Bridge — TradingView → Python

Two layers watch market: TV technicals + Python news/on-chain → same Telegram pipeline.

```
TradingView (KDRX v2 alert JSON) → Webhook https://YOUR_RENDER.onrender.com/webhook/tradingview 
→ src/webhook_server.py (Flask) → enrich news (news_fetcher.py) + onchain (onchain_fetcher.py) 
→ compute_tv_combined_score()  tv*0.50 + news*0.30 + onchain*0.20 → Telegram if ≥ threshold
```

**Webhook server:**
- `src/webhook_server.py` — Flask `POST /webhook/tradingview` header `X-TV-Secret: WEBHOOK_SECRET`, `GET /health`, `GET /webhook/status`
- `src/alerts/tradingview_webhook.py` — `normalize_coin()`, `compute_tv_combined_score()` mapping `quality_stars 0–5 → -3..+3`, `should_alert_tv()` (min stars + cooldown)
- `config.yaml` add `tradingview:` section: `webhook_enabled`, `min_quality_stars: 3`, `tv_signal_weight: 0.50` etc.
- `state.json` new keys: `tv_signals[]` (last 200) + `performance {total_signals, wins, by_coin}`

**JSON alert format (set in TV):**
```json
{
  "signal": "BUY", "strength": "STRONG", "quality_stars": 4, "coin": "BTCUSDT",
  "timeframe": "15", "price": 67450.20, "stop_loss": 66100.00, "tp1": 68800.40,
  "tp2": 70150.60, "tp3": 71500.80, "rsi": 58.3, "macd_direction": "bullish",
  "mtf_score": "6/8", "winrate": 62.5, "total_signals": 48, "timestamp": "{{timenow}}"
}
```
In TV: Create Alert → Condition: `KDRX v2 - High Quality Buy (≥4★)` → Options: Webhook URL → `https://YOUR_URL/webhook/tradingview` → Message: paste the JSON template from indicator (uses `{{ticker}}`, `{{interval}}`, `{{timenow}}`). Add header `X-TV-Secret` if you set `WEBHOOK_SECRET`.

### Deploy Webhook (Free)

**Option A — Laptop + ngrok (quick test):**
```bash
pip install flask gunicorn
WEBHOOK_SECRET=gBFKnf1yyHgcPIi5mU-cZNiv9SIWinhL python src/webhook_server.py  # :5000
# other terminal:
ngrok http 5000  # free tier → https://abc123.ngrok.io
# TV Webhook URL = https://abc123.ngrok.io/webhook/tradingview + header X-TV-Secret
```

**Option B — Render.com 24/7 (recommended):**
- Push `render.yaml` is in repo → Render Dashboard → New → Blueprint → connect `trading-news` → Apply
- Add env vars in Render: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GMAIL_*`, `WEBHOOK_SECRET` (= same as `.env` `gBFKnf1yyHgcPIi5mU-cZNiv9SIWinhL`)
- Deploy → URL `https://crypto-alert-webhook.onrender.com` → health `GET /health` 200
- TV Webhook URL = `https://crypto-alert-webhook.onrender.com/webhook/tradingview`
- Free tier spins down after 15m; `monitor.yml` keep-alive pings `/health` if `WEBHOOK_URL` secret set

**Dashboard upgrade:** `generate_dashboard.py` now shows TradingView Signal Feed (last 20: Time Coin Signal Quality TV/News/Combined Action) + Winrate Summary panel `performance` from `state.json`.

Add to GitHub Secrets: `WEBHOOK_SECRET=gBFKnf1yyHgcPIi5mU-cZNiv9SIWinhL` and optionally `WEBHOOK_URL=https://YOUR_RENDER.onrender.com` for keep-alive.

## Troubleshooting

- `429` rate-limit: auto waits `Retry-After` then fallback — check Actions log source used.
- No alerts: check `alerts.min_signal_to_alert` (BEARISH hides NEUTRAL) + `cooldown_minutes` + `max_alerts_per_run: 20`.
- State not persisting: ensure workflow has `contents: write` + repo not fork without secrets.
- TV webhook 401: check `X-TV-Secret` header matches `WEBHOOK_SECRET` in Render/.env.
- Pine winrate resets: expected on timeframe change — document limitation.
- Render cold start: first TV alert after idle may take 30s to wake; keep-alive curl from Actions every 30m mitigates.

## Disclaimer

> ⚠️ This alert is generated by an automated rule-based system for informational purposes only. It is NOT financial advice. Signals are based on configurable heuristics and may be wrong. Always do your own research before making any trading decisions. Past signal accuracy does not guarantee future results.
