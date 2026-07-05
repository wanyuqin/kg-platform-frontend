"""飞书归档超期物理清理（feishu-sync §13.3 D6）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.domain.state_machine import Event, InvalidTransition, Status, transition
from app.storage.pg.models import (
    FeishuSyncReceipt,
    Knowledge,
    KnowledgeVersion,
    ReviewTask,
    SourceDoc,
    SyncState,
    VikingCleanupFailed,
)
from app.storage.viking.client import VikingClient, build_uri

logger = logging.getLogger(__name__)


async def _record_viking_cleanup_failure(
    session: AsyncSession,
    uri: str,
    exc: Exception,
    *,
    now: datetime,
) -> None:
    existing = (
        await session.execute(select(VikingCleanupFailed).where(VikingCleanupFailed.uri == uri))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            VikingCleanupFailed(
                uri=uri,
                last_error=str(exc)[:2000],
                retry_count=0,
                next_retry_at=now + timedelta(hours=1),
            )
        )
    else:
        existing.last_error = str(exc)[:2000]
        existing.next_retry_at = now + timedelta(hours=1)
        existing.updated_at = now


async def purge_expired_feishu_archives(
    session: AsyncSession,
    viking: VikingClient,
    *,
    retention_days: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """删除 archived_at 超过 retention 的飞书 source_doc 下条目，清 sync_state，保留 source_doc。"""
    settings = settings or get_settings()
    retention_days = retention_days if retention_days is not None else settings.feishu_archived_retention_days
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)

    docs = (
        (
            await session.execute(
                select(SourceDoc).where(
                    SourceDoc.source == "feishu",
                    SourceDoc.status == "archived",
                    SourceDoc.archived_at.is_not(None),
                    SourceDoc.archived_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    purged = 0
    viking_deletes: list[str] = []

    for doc in docs:
        rows = (
            (
                await session.execute(
                    select(Knowledge).where(Knowledge.source_doc_id == doc.id)
                )
            )
            .scalars()
            .all()
        )
        kids = [row.kid for row in rows]
        if kids:
            await session.execute(delete(ReviewTask).where(ReviewTask.kid.in_(kids)))
            await session.execute(delete(KnowledgeVersion).where(KnowledgeVersion.kid.in_(kids)))
            for row in rows:
                if row.status in (Status.PUBLISHED, Status.EXPIRED):
                    try:
                        row.status = transition(Status(row.status), Event.ARCHIVE)
                    except InvalidTransition:
                        pass
                    viking_deletes.append(build_uri(row.domain_code, row.type, row.kid))
            await session.execute(delete(Knowledge).where(Knowledge.source_doc_id == doc.id))

        sync = (
            await session.execute(select(SyncState).where(SyncState.source_doc_id == doc.id))
        ).scalar_one_or_none()
        if sync:
            await session.delete(sync)
        await session.execute(
            delete(FeishuSyncReceipt).where(FeishuSyncReceipt.source_doc_id == doc.id)
        )
        doc.last_sync_error = "feishu_archive_purged: 归档超过保留期，条目已物理删除"
        purged += 1
        logger.info(
            "feishu archive purged doc=%s entries=%d archived_at=%s",
            doc.id,
            len(kids),
            doc.archived_at,
        )

    for uri in viking_deletes:
        try:
            await viking.delete(uri)
        except Exception as exc:
            logger.exception("viking delete failed during archive purge uri=%s", uri)
            await _record_viking_cleanup_failure(session, uri, exc, now=now)

    return purged
