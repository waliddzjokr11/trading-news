#!/usr/bin/env python3
"""
Runs before the main loop. Checks all secrets and API connectivity.
Prints a clear pass/fail checklist. Exits with error if critical
items fail.
"""
import os
import sys
import requests

# fix Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except:
    pass

REQUIRED_SECRETS = {
    "TELEGRAM_BOT_TOKEN": "Telegram bot token",
    "TELEGRAM_CHAT_ID": "Telegram chat ID",
    "GMAIL_USER": "Gmail address",
    "GMAIL_APP_PASSWORD": "Gmail app password",
    "ALERT_EMAIL_TO": "Alert destination email",
}

OPTIONAL_SECRETS = {
    "CRYPTOPANIC_API_KEY": "CryptoPanic API key (optional)",
    "WEBHOOK_SECRET": "Webhook secret (only needed if webhook server deployed)",
}

FREE_API_CHECKS = [
    {
        "name": "CoinGecko",
        "url": "https://api.coingecko.com/api/v3/ping",
        "critical": True,
    },
    {
        "name": "Binance (fallback)",
        "url": "https://api.binance.com/api/v3/ping",
        "critical": False,
    },
    {
        "name": "CoinCap (fallback 2)",
        "url": "https://api.coincap.io/v2/assets?limit=1",
        "critical": False,
    },
]

def run_startup_check(dry_run=False):
    print("\n" + "="*50)
    print("  STARTUP VALIDATION CHECK")
    print("="*50)

    all_critical_ok = True

    # 1. Check secrets
    print("\n[1] Secrets / Environment Variables")
    for key, label in REQUIRED_SECRETS.items():
        val = os.getenv(key)
        if val and len(val) > 3:
            print(f"  [OK] {label}")
        else:
            print(f"  [MISSING] {label} ({key})")
            if not dry_run:
                all_critical_ok = False

    for key, label in OPTIONAL_SECRETS.items():
        val = os.getenv(key)
        status = "[OK]" if val else "[ ] not set"
        print(f"  {status} {label}")

    # 2. Check APIs
    print("\n[2] Free API Connectivity")
    for api in FREE_API_CHECKS:
        try:
            r = requests.get(api["url"], timeout=8, headers={"User-Agent": "crypto-alert-system/1.0"})
            if r.status_code == 200:
                print(f"  [OK] {api['name']} reachable")
            else:
                print(f"  [WARN] {api['name']} returned {r.status_code}")
                if api["critical"] and not dry_run:
                    all_critical_ok = False
        except Exception as e:
            print(f"  [ERR] {api['name']} UNREACHABLE: {e}")
            if api["critical"] and not dry_run:
                all_critical_ok = False

    # 3. Check Telegram connectivity (if token set)
    print("\n[3] Alert Channel Test")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=8
            )
            if r.status_code == 200:
                bot_name = r.json().get("result", {}).get("username", "unknown")
                print(f"  [OK] Telegram bot reachable (@{bot_name})")
            else:
                print(f"  [ERR] Telegram bot token invalid (status {r.status_code})")
                if not dry_run:
                    all_critical_ok = False
        except Exception as e:
            print(f"  [ERR] Telegram unreachable: {e}")
    else:
        print("  [WARN] Telegram credentials not set — skipping test")

    # 4. Summary
    print("\n" + "="*50)
    if all_critical_ok:
        print("  [OK] ALL CRITICAL CHECKS PASSED — starting monitor")
    else:
        print("  [ERR] CRITICAL CHECKS FAILED — fix above issues first")
        print("  Tip: run with --dry-run to bypass secret checks for testing")
    print("="*50 + "\n")

    if not all_critical_ok and not dry_run:
        sys.exit(1)
