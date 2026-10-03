import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """At most `limit` events per key in a sliding `window_s`-second window (in memory)."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._events: defaultdict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= self._window:
            events.popleft()
        if len(events) >= self._limit:
            return False
        events.append(now)
        return True
