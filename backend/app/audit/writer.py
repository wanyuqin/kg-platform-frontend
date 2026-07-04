"""审计批量写入（技术设计文档 十一）。

进程内有界队列（maxsize 10000）+ 后台协程每 1s / 满 500 条批量 INSERT；
队列满丢弃并计数告警。P1 接受崩溃丢失秒级尾部数据。

TODO(P1)：实现队列消费协程并挂到 app lifespan。
"""

import asyncio
from typing import Any

QUEUE_MAXSIZE = 10000
FLUSH_INTERVAL_S = 1.0
FLUSH_BATCH = 500

_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)


def enqueue(record: dict[str, Any]) -> bool:
    """非阻塞入队；队列满返回 False（调用方仅计数，不影响主请求）。"""
    try:
        _queue.put_nowait(record)
        return True
    except asyncio.QueueFull:
        return False
