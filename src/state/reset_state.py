#!/usr/bin/env python3
"""
Resets state.json to a clean empty template.
Usage: python src/state/reset_state.py
WARNING: This clears all price history, alert history, and run logs.
"""
import json

EMPTY_STATE = {
    "price_history": {},
    "last_alert": {},
    "seen_news_hashes": [],
    "last_run": None,
    "run_count": 0,
    "alert_history": [],
    "run_history": [],
    "tv_signals": [],
    "performance": {
        "total_signals": 0,
        "wins": 0,
        "losses": 0,
        "by_coin": {}
    }
}

with open("state.json", "w") as f:
    json.dump(EMPTY_STATE, f, indent=2)

print("✅ state.json reset to empty template")
print("Run 'python src/main.py --dry-run' to verify setup")
