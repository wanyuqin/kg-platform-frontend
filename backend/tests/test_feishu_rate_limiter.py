"""AsyncTokenBucket 单元测试。"""

import asyncio
import time

from app.feishu.rate_limiter import AsyncTokenBucket


class TestAsyncTokenBucket:
    async def test_acquire_immediate_when_tokens_available(self):
        bucket = AsyncTokenBucket(rate=10.0, capacity=10.0)
        start = time.monotonic()
        await bucket.acquire()
        await bucket.acquire()
        assert time.monotonic() - start < 0.1

    async def test_acquire_waits_when_empty(self):
        bucket = AsyncTokenBucket(rate=100.0, capacity=1.0)
        await bucket.acquire()
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005  # 至少等到补充一个令牌

    async def test_concurrent_acquire_serializes(self):
        bucket = AsyncTokenBucket(rate=50.0, capacity=2.0)
        await asyncio.gather(*(bucket.acquire() for _ in range(4)))
