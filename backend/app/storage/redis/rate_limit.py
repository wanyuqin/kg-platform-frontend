"""按 Key 固定窗口限流（技术设计文档 10.2）。

Redis 故障时放行并告警——检索是读操作，可用性优先于限流精度。
"""

import logging
import time

from redis.exceptions import RedisError

from app.storage.redis.client import get_redis

logger = logging.getLogger(__name__)


async def check_rate_limit(key_id: str, qps_limit: int) -> bool:
    """在限额内返回 True；超限返回 False；Redis 故障放行。"""
    window = int(time.time())
    redis_key = f"rl:{key_id}:{window}"
    try:
        redis = get_redis()
        count = await redis.incr(redis_key)
        if count == 1:
            await redis.expire(redis_key, 2)
        return count <= qps_limit
    except RedisError:
        logger.warning("rate limiter degraded: redis unavailable, allowing request")
        return True
