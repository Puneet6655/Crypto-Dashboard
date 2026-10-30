"""Slows down password guessing: too many failed sign-ins for one email or from one address lock it for a while."""
import threading
import time
from collections import defaultdict, deque


class Throttle:
    def __init__(self, max_failures: int, window_seconds: float):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        times = self._failures[key]
        while times and times[0] <= now - self.window:
            times.popleft()
        return times

    def seconds_locked(self, *keys: str) -> float:
        """0 if every key may try again now, else how long until the first one may."""
        now = time.monotonic()
        with self._lock:
            waits = []
            for key in keys:
                times = self._trim(key, now)
                if len(times) >= self.max_failures:
                    waits.append(times[0] + self.window - now)
            return max(waits, default=0.0)

    def failed(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._trim(key, now).append(now)

    def succeeded(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


# 5 wrong passwords for one email, or 20 from one IP address, within 15 minutes.
login_by_email = Throttle(max_failures=5, window_seconds=15 * 60)
login_by_ip = Throttle(max_failures=20, window_seconds=15 * 60)
# 10 new accounts per IP address per hour.
signup_by_ip = Throttle(max_failures=10, window_seconds=60 * 60)
