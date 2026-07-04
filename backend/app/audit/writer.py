"""审计批量写入（技术设计文档 十一）。

进程内有界队列（maxsize 10000）+ 后台协程每 1s / 满 500 条批量 INSERT；
队列满丢弃并计数告警；批量写失败整批丢弃并记日志（毒丸不阻塞队列）。
P1 接受崩溃丢失秒级尾部数据——审计驱动治理统计而非计费；P2 若需强保证
写入口切 RocketMQ，表结构不变。消费协程由 app lifespan 起停（main.py）。
"""

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.storage.pg.models import AuditLog

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 10000
FLUSH_INTERVAL_S = 1.0
FLUSH_BATCH = 500

# executemany 要求各行字段同构：search / read 记录按此列集补齐 None
_COLUMNS = (
    "ts",
    "key_id",
    "action",
    "query",
    "filter_type",
    "filter_tag",
    "hits",
    "excluded_expired",
    "kid",
    "version",
    "latency_ms",
)

_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)


def enqueue(record: dict[str, Any]) -> bool:
    """非阻塞入队；队列满返回 False（调用方仅计数，不影响主请求）。"""
    try:
        _queue.put_nowait(record)
        return True
    except asyncio.QueueFull:
        return False


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    return {col: record.get(col) for col in _COLUMNS}


async def _write_batch(batch: list[dict], session: AsyncSession) -> int:
    try:
        await session.execute(insert(AuditLog), [_normalize(r) for r in batch])
        await session.commit()
        return len(batch)
    except Exception:
        await session.rollback()
        logger.exception("audit batch write failed, %d records dropped", len(batch))
        return 0


async def flush_once(session: AsyncSession) -> int:
    """非阻塞 drain 当前队列（至多 FLUSH_BATCH 条）并落库；停机清尾与测试复用。"""
    batch: list[dict] = []
    while len(batch) < FLUSH_BATCH and not _queue.empty():
        batch.append(_queue.get_nowait())
    if not batch:
        return 0
    return await _write_batch(batch, session)


async def _collect_batch(queue: asyncio.Queue, max_batch: int, window_s: float) -> list[dict]:
    """阻塞等首条记录，随后在窗口内攒批：满 max_batch 或到时即返回。

    攒批中被取消（进程 shutdown）时，把已出队未落库的记录放回队列再传播取消，
    交由 lifespan 的 flush_once 清尾——否则这批记录会静默丢失。
    """
    batch = [await queue.get()]  # 此处取消无持有记录，直接传播
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window_s
    try:
        while len(batch) < max_batch:
            timeout = deadline - loop.time()
            if timeout <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(queue.get(), timeout))
            except TimeoutError:
                break
    except asyncio.CancelledError:
        for record in batch:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(record)
        raise
    return batch


async def run_consumer(
    session_factory: async_sessionmaker[AsyncSession],
    window_s: float = FLUSH_INTERVAL_S,
) -> None:
    """消费主循环；由 lifespan 以 task 运行，shutdown 时 cancel + flush_once 清尾。"""
    while True:
        batch = await _collect_batch(_queue, FLUSH_BATCH, window_s)
        async with session_factory() as session:
            await _write_batch(batch, session)
