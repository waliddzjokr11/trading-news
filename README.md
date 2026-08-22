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

## Troubleshooting

- `429` rate-limit: auto waits `Retry-After` then fallback — check Actions log source used.
- No alerts: check `alerts.min_signal_to_alert` (BEARISH hides NEUTRAL) + `cooldown_minutes` + `max_alerts_per_run: 20`.
- State not persisting: ensure workflow has `contents: write` + repo not fork without secrets.

## Disclaimer

> ⚠️ This alert is generated by an automated rule-based system for informational purposes only. It is NOT financial advice. Signals are based on configurable heuristics and may be wrong. Always do your own research before making any trading decisions. Past signal accuracy does not guarantee future results.
