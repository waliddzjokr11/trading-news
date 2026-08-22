"""
state_manager.py — persistent state for GitHub Actions + local runs.
Stores rolling price history, alert cooldowns, dedup hashes, run history.
"""
import json
import os
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path


DEFAULT_STATE = {
    "price_history": {},
    "last_alert": {},
    "seen_news_hashes": [],
    "seen_news_meta": {},  # hash -> {timestamp, count}
    "alert_history": [],
    "run_history": [],
    "last_run": None,
    "last_digest_date": None,
    "run_count": 0,
}

# Signal severity rank (lower = more bearish / urgent dump). For escalation check we use absolute severity distance.
SIGNAL_RANK = {
    "DUMP_WARNING": 0,
    "BEARISH": 1,
    "NEUTRAL": 2,
    "BULLISH": 3,
    "STRONG_BUY": 4,
}
# Urgency rank for escalation: dump and strong buy are both extremes; escalation if moves away from neutral.
# Simpler: any move to a more extreme level in either direction, or rank change >=1 when previous was non-neutral and new is more extreme.
ESCALATION_ORDER = ["DUMP_WARNING", "BEARISH", "NEUTRAL", "BULLISH", "STRONG_BUY"]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    def __init__(self, state_path="state.json"):
        self.path = Path(state_path)
        self.state = self.load()

    def load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # merge defaults for missing keys (forward compat)
                for k, v in DEFAULT_STATE.items():
                    if k not in data:
                        data[k] = v
                return data
            except Exception:
                return json.loads(json.dumps(DEFAULT_STATE))
        return json.loads(json.dumps(DEFAULT_STATE))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    # ---- price history ----
    def append_price(self, coin_id, price, volume):
        """Append price point, trim to 200."""
        hist = self.state["price_history"].setdefault(coin_id, [])
        hist.append({"timestamp": _now_iso(), "price": float(price), "volume": float(volume) if volume else 0})
        # keep last 200
        if len(hist) > 200:
            hist[:] = hist[-200:]

    def get_history(self, coin_id):
        return self.state["price_history"].get(coin_id, [])

    # ---- news dedup ----
    @staticmethod
    def hash_news(title, link):
        h = hashlib.md5(f"{title.strip().lower()}|{link.strip().lower()}".encode()).hexdigest()
        return h[:16]

    def is_seen_news(self, h, ttl_hours=24):
        meta = self.state.get("seen_news_meta", {}).get(h)
        if not meta:
            return False
        try:
            ts = datetime.fromisoformat(meta["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts
            if age > timedelta(hours=ttl_hours):
                # expire
                self.state["seen_news_meta"].pop(h, None)
                if h in self.state.get("seen_news_hashes", []):
                    self.state["seen_news_hashes"].remove(h)
                return False
            return True
        except Exception:
            return False

    def mark_seen_news(self, h):
        if h not in self.state.setdefault("seen_news_hashes", []):
            self.state["seen_news_hashes"].append(h)
        self.state.setdefault("seen_news_meta", {})[h] = {"timestamp": _now_iso(), "count": self.state["seen_news_meta"].get(h, {}).get("count", 0) + 1}
        # keep list bounded (500)
        if len(self.state["seen_news_hashes"]) > 800:
            self.state["seen_news_hashes"] = self.state["seen_news_hashes"][-500:]

    def prune_seen(self, ttl_hours=24):
        """Remove expired hashes."""
        now = datetime.now(timezone.utc)
        for h in list(self.state.get("seen_news_meta", {}).keys()):
            try:
                ts = datetime.fromisoformat(self.state["seen_news_meta"][h]["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if now - ts > timedelta(hours=ttl_hours):
                    self.state["seen_news_meta"].pop(h, None)
                    if h in self.state["seen_news_hashes"]:
                        self.state["seen_news_hashes"].remove(h)
            except Exception:
                continue

    # ---- alert cooldown & escalation ----
    def should_alert(self, coin_id, new_signal, cooldown_minutes=60):
        """
        Returns (should_send: bool, reason: str)
        Escalation bypasses cooldown: if new signal is more extreme than last.
        """
        last = self.state.get("last_alert", {}).get(coin_id)
        if not last:
            return True, "first_alert"
        try:
            last_ts = datetime.fromisoformat(last["timestamp"])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
        except Exception:
            return True, "bad_timestamp"
        elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
        last_sig = last.get("signal", "NEUTRAL")
        if last_sig not in SIGNAL_RANK or new_signal not in SIGNAL_RANK:
            return True, "unknown_signal"
        is_escalation = self._is_escalation(last_sig, new_signal)
        if is_escalation:
            return True, f"escalation {last_sig}->{new_signal}"
        if elapsed >= cooldown_minutes:
            return True, f"cooldown_passed {elapsed:.0f}m"
        return False, f"cooldown {elapsed:.0f}/{cooldown_minutes}m last={last_sig}"

    @staticmethod
    def _is_escalation(old, new):
        """Escalation if new is more extreme (further from NEUTRAL) than old."""
        order = ESCALATION_ORDER
        oi = order.index(old) if old in order else 2
        ni = order.index(new) if new in order else 2
        # distance from neutral (index 2)
        old_dist = abs(oi - 2)
        new_dist = abs(ni - 2)
        # if new is further from neutral AND on same side or crossing to opposite extreme, treat as escalation
        # also any move to DUMP_WARNING or STRONG_BUY from non-extreme is escalation
        if new in ("DUMP_WARNING", "STRONG_BUY") and old not in ("DUMP_WARNING", "STRONG_BUY"):
            return True
        if new_dist > old_dist:
            # same side escalation (more bearish or more bullish)
            if (oi < 2 and ni < 2) or (oi > 2 and ni > 2):
                return True
            # crossing from neutral to any side is not escalation unless extreme (handled above)
        return False

    def record_alert(self, coin_id, signal, score):
        self.state.setdefault("last_alert", {})[coin_id] = {
            "timestamp": _now_iso(),
            "signal": signal,
            "score": float(score),
        }
        self.state.setdefault("alert_history", []).append({
            "coin": coin_id,
            "signal": signal,
            "score": float(score),
            "timestamp": _now_iso(),
        })
        # keep last 200 alerts
        if len(self.state["alert_history"]) > 200:
            self.state["alert_history"] = self.state["alert_history"][-200:]

    # ---- run tracking ----
    def record_run(self, status="success", source="unknown", coins=0, alerts=0, error=""):
        self.state["run_count"] = self.state.get("run_count", 0) + 1
        self.state["last_run"] = _now_iso()
        entry = {
            "timestamp": _now_iso(),
            "status": status,
            "source": source,
            "coins": coins,
            "alerts": alerts,
        }
        if error:
            entry["error"] = error[:500]
        self.state.setdefault("run_history", []).append(entry)
        if len(self.state["run_history"]) > 100:
            self.state["run_history"] = self.state["run_history"][-100:]

    def should_send_digest(self, digest_time="08:00", tz_name="Africa/Algiers"):
        """Check if daily digest is due (local time matches digest_time and not yet sent today)."""
        try:
            import pytz
            tz = pytz.timezone(tz_name)
            now_local = datetime.now(tz)
            today_str = now_local.strftime("%Y-%m-%d")
            if self.state.get("last_digest_date") == today_str:
                return False
            cur_hm = now_local.strftime("%H:%M")
            # allow 30-min window (since runs are every 30m)
            h, m = map(int, digest_time.split(":"))
            now_mins = now_local.hour * 60 + now_local.minute
            target_mins = h * 60 + m
            # if within 30m after target
            if 0 <= now_mins - target_mins < 30:
                return True
            return False
        except Exception:
            return False

    def mark_digest_sent(self):
        try:
            import pytz
            from datetime import datetime as dt
            # use config tz if available, else UTC
            tz_name = "Africa/Algiers"
            try:
                import yaml
                cfg_path = os.environ.get("CONFIG_PATH", "config.yaml")
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f) or {}
                    tz_name = cfg.get("alerts", {}).get("timezone", tz_name)
            except Exception:
                pass
            tz = pytz.timezone(tz_name)
            self.state["last_digest_date"] = dt.now(tz).strftime("%Y-%m-%d")
        except Exception:
            self.state["last_digest_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
