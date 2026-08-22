#!/usr/bin/env python3
"""
backtest.py — replay historical CoinGecko market_chart, apply signal engine.
Usage: python backtest.py --coin bitcoin --days 90 --config config.yaml
"""
import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import requests
import yaml

from signals.price_signals import evaluate_price

HEADERS = {"User-Agent": "crypto-alert-system/1.0 backtest"}


def fetch_history(coin_id, days, vs="usd"):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": vs, "days": days}
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    if r.status_code == 429:
        print("Rate limited 429 — wait 60s")
        time.sleep(60)
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    prices = data.get("prices", [])  # [[ts_ms, price]]
    volumes = data.get("total_volumes", [])
    # align
    vol_map = {int(ts): v for ts, v in volumes}
    combined = []
    for ts_ms, price in prices:
        vol = vol_map.get(int(ts_ms), 0)
        dt = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc)
        combined.append({"timestamp": dt, "price": float(price), "volume": float(vol)})
    return combined


def compute_rsi_macd_signals(combined, config):
    # Use evaluate_price iteratively
    results = []
    price_history = []
    for idx, pt in enumerate(combined):
        hist = price_history[-200:]  # window
        res = evaluate_price("backtest", pt["price"], pt["volume"], hist, config)
        # also need composite with news/onchain neutral for backtest price-only
        # map price score directly to label via thresholds (simplified)
        from signals.signal_engine import composite_label
        label = composite_label(res["score"])  # treat price score as composite for backtest
        # price 24h later for accuracy check
        price_now = pt["price"]
        # find point 24h later (approx 24h = 1 day, data hourly if days>1)
        later = None
        target_ts = pt["timestamp"].timestamp() + 24*3600
        for j in range(idx+1, len(combined)):
            if combined[j]["timestamp"].timestamp() >= target_ts:
                later = combined[j]["price"]
                break
        change_24h = ((later - price_now)/price_now*100) if later else None
        correct = None
        if later is not None:
            if label == "DUMP_WARNING" and change_24h is not None:
                correct = change_24h < -2
            elif label == "STRONG_BUY" and change_24h is not None:
                correct = change_24h > 2
            elif label in ("BULLISH", "BEARISH"):
                correct = (change_24h > 0) == (label == "BULLISH")
        results.append({
            "timestamp": pt["timestamp"].isoformat(),
            "price": price_now,
            "volume": pt["volume"],
            "rsi": res.get("rsi"),
            "macd_dir": (res.get("macd") or {}).get("direction"),
            "price_score": res["score"],
            "signal": label,
            "price_24h_later": later,
            "change_24h_later": change_24h,
            "correct": correct,
        })
        price_history.append({"timestamp": pt["timestamp"].isoformat(), "price": pt["price"], "volume": pt["volume"]})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", default="bitcoin", help="CoinGecko ID")
    parser.add_argument("--days", type=int, default=90, help="days back (max 365 free)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out", default=None, help="csv out path")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    print(f"Fetching {args.coin} {args.days}d history...")
    combined = fetch_history(args.coin, args.days)
    print(f"Got {len(combined)} points from {combined[0]['timestamp']} to {combined[-1]['timestamp']}")

    results = compute_rsi_macd_signals(combined, cfg)

    out = args.out or f"backtest_{args.coin}_{args.days}d.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp","price","volume","rsi","macd_dir","price_score","signal","price_24h_later","change_24h_later","correct"])
        w.writeheader()
        w.writerows(results)
    print(f"Wrote {out}")

    # accuracy metrics
    dumps = [r for r in results if r["signal"]=="DUMP_WARNING" and r["correct"] is not None]
    buys = [r for r in results if r["signal"]=="STRONG_BUY" and r["correct"] is not None]
    def acc(lst):
        return sum(1 for x in lst if x["correct"])/len(lst)*100 if lst else 0
    print(f"DUMP_WARNING: {len(dumps)} signals, accuracy {acc(dumps):.1f}% (drop >2% in 24h)")
    print(f"STRONG_BUY: {len(buys)} signals, accuracy {acc(buys):.1f}% (rise >2% in 24h)")
    bulls = [r for r in results if r["signal"]=="BULLISH" and r["correct"] is not None]
    bears = [r for r in results if r["signal"]=="BEARISH" and r["correct"] is not None]
    print(f"BULLISH: {len(bulls)} acc {acc(bulls):.1f}%  BEARISH: {len(bears)} acc {acc(bears):.1f}%")
    print("Tune thresholds in config.yaml price.* and rerun to improve.")


if __name__ == "__main__":
    main()
