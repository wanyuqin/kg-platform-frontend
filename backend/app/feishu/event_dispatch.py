"""飞书事件 → MQ / 归档分发（feishu-sync §8）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.feishu.sync import archive_source_doc
from app.storage.mq.message import FeishuEventMessage
from app.storage.mq.producer import FeishuEventProducer
from app.storage.pg.models import SourceDoc, SyncState

logger = logging.getLogger(__name__)

SYNC_TRIGGER_EVENTS = frozenset(
    {
        "drive.file.bitable_field_changed",
        "drive.file.title_updated",
        "drive.file.edited",
    }
)
DELETE_EVENT = "drive.file.deleted"


async def find_sync_by_file_token(
    session: AsyncSession, file_token: str
) -> tuple[SourceDoc, SyncState] | None:
    row = (
        await session.execute(
            select(SourceDoc, SyncState)
            .join(SyncState, SyncState.source_doc_id == SourceDoc.id)
            .where(SyncState.feishu_doc_token == file_token)
        )
    ).one_or_none()
    return row if row is None else (row[0], row[1])


async def enqueue_feishu_sync(
    session: AsyncSession,
    sync: SyncState,
    *,
    triggered_by: str,
    producer: FeishuEventProducer | None = None,
) -> None:
    producer = producer or FeishuEventProducer()
    await producer.publish(
        FeishuEventMessage(
            source_doc_id=sync.source_doc_id,
            feishu_doc_token=sync.feishu_doc_token,
            feishu_doc_type=sync.feishu_doc_type,
            triggered_by=triggered_by,  # type: ignore[arg-type]
        )
    )
    sync.last_event_at = datetime.now(UTC)


async def dispatch_feishu_event(
    session: AsyncSession,
    *,
    event_type: str | None,
    file_token: str | None,
    producer: FeishuEventProducer | None = None,
) -> str:
    """处理飞书 drive 事件，返回 action 标签（logged / enqueued / archived / ignored）。"""
    if not event_type or not file_token:
        return "ignored"

    if event_type == DELETE_EVENT:
        row = await find_sync_by_file_token(session, file_token)
        if row is None:
            logger.info("feishu delete ignored unknown token=%s", file_token)
            return "ignored"
        doc, _sync = row
        try:
            await archive_source_doc(session, doc.id)
            return "archived"
        except Exception:
            await session.rollback()
            logger.exception("archive_source_doc failed for token=%s", file_token)
            return "error"

    if event_type not in SYNC_TRIGGER_EVENTS:
        return "ignored"

    row = await find_sync_by_file_token(session, file_token)
    if row is None:
        logger.info("feishu sync event ignored unknown token=%s type=%s", file_token, event_type)
        return "ignored"
    doc, sync = row
    if doc.status == "archived":
        return "ignored"

    await enqueue_feishu_sync(session, sync, triggered_by="event", producer=producer)
    return "enqueued"
