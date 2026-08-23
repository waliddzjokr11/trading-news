import time
import threading
from collections import deque

class RateLimiter:
    """
    Thread-safe rate limiter using a sliding window.
    Usage:
        limiter = RateLimiter(max_calls=25, period=60)  # 25 calls per 60s
        limiter.wait()  # call before every API request
        response = requests.get(url)
    """
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            # Remove calls outside the window
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()
            # If at limit, sleep until oldest call exits window
            if len(self.calls) >= self.max_calls:
                sleep_for = self.period - (now - self.calls[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self.calls.append(time.monotonic())

# Global instances — import these in fetcher files
coingecko_limiter = RateLimiter(max_calls=25, period=60)   # 25/min (leaves buffer vs 30)
binance_limiter   = RateLimiter(max_calls=800, period=60)  # well under 1200/min
coincap_limiter   = RateLimiter(max_calls=150, period=60)  # well under 200/min
