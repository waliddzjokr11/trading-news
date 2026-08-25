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
    "open_trades": {},  # coin -> {entry, sl, tp1, tp2, tp3, signal, opened_at, entry_price}
    "trade_history": [],  # closed trades with result
    "performance": {"total_signals": 0, "wins": 0, "losses": 0, "by_coin": {}, "winrate": 0, "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0, "suppressed": 0},
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

    # ---- trade tracking (TP/SL hits + winrate) ----
    def open_trade(self, coin, entry, sl, tp1, tp2, tp3, signal, timestamp=None):
        """Open a new trade for winrate tracking. Overwrites existing open trade for that coin."""
        self.state.setdefault("open_trades", {})[coin] = {
            "coin": coin,
            "entry": float(entry) if entry else 0,
            "sl": float(sl) if sl else 0,
            "tp1": float(tp1) if tp1 else 0,
            "tp2": float(tp2) if tp2 else 0,
            "tp3": float(tp3) if tp3 else 0,
            "signal": signal,
            "opened_at": timestamp or _now_iso(),
            "highest_tp_hit": 0,  # 0=none, 1=TP1, 2=TP2, 3=TP3
        }

    def check_tp_hits(self, current_prices: dict):
        """
        Check all open trades against current price.
        current_prices: dict coin -> {price, ...} or coin -> price
        Returns list of hits: [{coin, hit: 'TP1'|'TP2'|'TP3'|'SL', price, trade}]
        Updates trade_history + performance winrate, sends TP signal via history.
        """
        hits = []
        open_trades = self.state.get("open_trades", {})
        to_close = []
        for coin, trade in list(open_trades.items()):
            pdata = current_prices.get(coin)
            if not pdata:
                continue
            cur = pdata.get("price") if isinstance(pdata, dict) else pdata
            if cur is None:
                continue
            try:
                cur_f = float(cur)
            except:
                continue
            entry = trade.get("entry", 0)
            sl = trade.get("sl", 0)
            tp1 = trade.get("tp1", 0)
            tp2 = trade.get("tp2", 0)
            tp3 = trade.get("tp3", 0)
            signal = trade.get("signal", "")
            is_long = signal in ("BULLISH", "STRONG_BUY", "BUY", "STRONG BUY")
            hit = None
            # SL has priority if both hit in same bar (use low/high but we only have close; approximate with close)
            # For long: TP hit if cur >= tp, SL if cur <= sl
            # For short: inverted
            if is_long:
                if tp3 and cur >= tp3 and trade.get("highest_tp_hit", 0) < 3:
                    hit = "TP3"
                elif tp2 and cur >= tp2 and trade.get("highest_tp_hit", 0) < 2:
                    hit = "TP2"
                elif tp1 and cur >= tp1 and trade.get("highest_tp_hit", 0) < 1:
                    hit = "TP1"
                elif sl and cur <= sl:
                    hit = "SL"
            else:
                # short / bearish
                if tp3 and cur <= tp3 and trade.get("highest_tp_hit", 0) < 3:
                    hit = "TP3"
                elif tp2 and cur <= tp2 and trade.get("highest_tp_hit", 0) < 2:
                    hit = "TP2"
                elif tp1 and cur <= tp1 and trade.get("highest_tp_hit", 0) < 1:
                    hit = "TP1"
                elif sl and cur >= sl:
                    hit = "SL"
            if hit:
                # update highest
                level = {"TP1": 1, "TP2": 2, "TP3": 3, "SL": -1}.get(hit, 0)
                if hit.startswith("TP"):
                    trade["highest_tp_hit"] = max(trade.get("highest_tp_hit", 0), level)
                hits.append({"coin": coin, "hit": hit, "price": cur_f, "trade": dict(trade)})
                # update performance immediately for every TP touch (as per user: every single trade result)
                self._record_trade_result(coin, hit, cur_f, trade)
                # if SL or TP3, close trade
                if hit in ("SL", "TP3"):
                    to_close.append(coin)
                else:
                    # TP1/TP2 keep open for higher TP
                    self.state["open_trades"][coin] = trade
        for c in to_close:
            self.state["open_trades"].pop(c, None)
        if hits:
            self.save()
        return hits

    def _record_trade_result(self, coin, hit, price, trade):
        """Add to trade_history and update overall winrate (every TP touch counts)."""
        perf = self.state.setdefault("performance", {"total_signals": 0, "wins": 0, "losses": 0, "by_coin": {}, "winrate": 0, "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0, "suppressed": 0})
        # trade_history entry
        entry = {
            "timestamp": _now_iso(),
            "coin": coin,
            "signal": trade.get("signal"),
            "hit": hit,
            "entry": trade.get("entry"),
            "exit_price": float(price),
            "sl": trade.get("sl"),
            "tp1": trade.get("tp1"),
            "tp2": trade.get("tp2"),
            "tp3": trade.get("tp3"),
        }
        self.state.setdefault("trade_history", []).append(entry)
        if len(self.state["trade_history"]) > 500:
            self.state["trade_history"] = self.state["trade_history"][-500:]
        # update counts: TP = win, SL = loss (every single result)
        if hit.startswith("TP"):
            perf["wins"] = perf.get("wins", 0) + 1
            if hit == "TP1":
                perf["tp1"] = perf.get("tp1", 0) + 1
            elif hit == "TP2":
                perf["tp2"] = perf.get("tp2", 0) + 1
            elif hit == "TP3":
                perf["tp3"] = perf.get("tp3", 0) + 1
        elif hit == "SL":
            perf["losses"] = perf.get("losses", 0) + 1
            perf["sl"] = perf.get("sl", 0) + 1
        # total = wins+losses (every TP/SL)
        total = perf.get("wins", 0) + perf.get("losses", 0)
        perf["total_signals"] = total
        perf["winrate"] = round(perf["wins"] / total * 100, 1) if total else 0
        # per coin winrate
        by_coin = perf.setdefault("by_coin", {})
        # store per coin wins/losses separately if needed
        # keep coin count for best/worst (already used)
        # also track per coin winrate via trade_history filter later
        self.state["performance"] = perf
        # also add to alert_history as TP signal for dashboard
        self.state.setdefault("alert_history", []).append({
            "timestamp": _now_iso(),
            "coin": coin,
            "signal": hit,
            "score": 0,
            "action": hit,
            "hit": hit,
        })
        if len(self.state["alert_history"]) > 200:
            self.state["alert_history"] = self.state["alert_history"][-200:]
