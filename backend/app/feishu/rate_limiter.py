"""飞书 OpenAPI 全局限流（令牌桶，feishu-sync §14）。"""

import asyncio
import time


class AsyncTokenBucket:
    """异步令牌桶；rate 为每秒补充令牌数。"""

    def __init__(self, rate: float, *, capacity: float | None = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = self.capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            await asyncio.sleep(wait)
