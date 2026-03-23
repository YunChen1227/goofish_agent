import asyncio
import time


class RateLimiter:
    def __init__(self, max_per_hour: int) -> None:
        self.max_tokens: float = float(max_per_hour)
        self.tokens: float = self.max_tokens
        self.refill_rate: float = max_per_hour / 3600.0
        self._last_refill: float = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self._last_refill = now

    async def acquire(self) -> None:
        self._refill()
        if self.tokens < 1.0:
            wait = (1.0 - self.tokens) / self.refill_rate
            await asyncio.sleep(wait)
            self._refill()
        self.tokens -= 1.0


class RateLimiterRegistry:
    """Pre-configured rate limiters per DESIGN.md 10.3."""

    def __init__(self, settings: object) -> None:
        self.search = RateLimiter(settings.search_per_hour)  # type: ignore[attr-defined]
        self.detail = RateLimiter(settings.detail_per_hour)  # type: ignore[attr-defined]
        self.favorite = RateLimiter(settings.favorite_per_hour)  # type: ignore[attr-defined]
        self.chat_new = RateLimiter(settings.chat_new_per_hour)  # type: ignore[attr-defined]
        self.message = RateLimiter(settings.message_per_hour)  # type: ignore[attr-defined]
