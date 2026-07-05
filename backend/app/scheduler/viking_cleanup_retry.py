"""OpenViking 删除失败重试（feishu archive purge 兜底）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.pg.models import VikingCleanupFailed
from app.storage.viking.client import VikingClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_INTERVAL = timedelta(hours=6)


async def retry_viking_cleanup_failed(
    session: AsyncSession,
    viking: VikingClient,
    *,
    now: datetime | None = None,
) -> int:
    """重试 viking_cleanup_failed 中到期的 URI，成功则删记录。"""
    now = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(VikingCleanupFailed)
                .where(
                    VikingCleanupFailed.next_retry_at <= now,
                    VikingCleanupFailed.retry_count < MAX_RETRIES,
                )
                .order_by(VikingCleanupFailed.next_retry_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )

    recovered = 0
    for row in rows:
        try:
            await viking.delete(row.uri)
        except Exception as exc:
            row.retry_count += 1
            row.last_error = str(exc)[:2000]
            row.next_retry_at = now + RETRY_INTERVAL
            row.updated_at = now
            logger.exception("viking cleanup retry failed uri=%s count=%d", row.uri, row.retry_count)
            continue
        await session.delete(row)
        recovered += 1
        logger.info("viking cleanup retry ok uri=%s", row.uri)

    return recovered
