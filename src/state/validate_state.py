#!/usr/bin/env python3
"""
Run this to check the health of state.json.
Usage: python src/state/validate_state.py
"""
import json
import sys
from datetime import datetime, timezone

# fix Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except:
    pass

STATE_FILE = "state.json"
REQUIRED_KEYS = [
    "price_history", "last_alert", "seen_news_hashes",
    "last_run", "run_count", "alert_history", "run_history"
]

def validate():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        print(" [ERR] state.json not found")
        return False
    except json.JSONDecodeError as e:
        print(f" [ERR] state.json is corrupted JSON: {e}")
        return False

    issues = []

    # Check required keys
    for key in REQUIRED_KEYS:
        if key not in state:
            issues.append(f"Missing key: {key}")

    # Check run_count
    run_count = state.get("run_count", 0)
    print(f"  run_count     : {run_count}")
    if run_count == 0:
        issues.append("run_count is 0 — system has never completed a run")

    # Check last_run
    last_run = state.get("last_run")
    print(f"  last_run      : {last_run}")
    if last_run:
        try:
            lr = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - lr).total_seconds() / 3600
            print(f"  last_run age  : {age_hours:.1f} hours ago")
            if age_hours > 1:
                issues.append(f"Last run was {age_hours:.1f}h ago — may not be running on schedule")
        except Exception:
            issues.append("last_run timestamp is malformed")

    # Check price_history
    ph = state.get("price_history", {})
    print(f"  coins tracked : {len(ph)}")
    if len(ph) == 0:
        issues.append("price_history is empty — no coins have been fetched")

    # Check alert_history
    ah = state.get("alert_history", [])
    print(f"  alerts sent   : {len(ah)}")

    if issues:
        print("\n[WARN] ISSUES FOUND:")
        for i in issues:
            print(f"  - {i}")
        return False
    else:
        print("\n[OK] state.json looks healthy")
        return True

if __name__ == "__main__":
    print("=== state.json Health Check ===")
    ok = validate()
    sys.exit(0 if ok else 1)
