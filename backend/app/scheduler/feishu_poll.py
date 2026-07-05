"""飞书文档轮询兜底（feishu-sync §9）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.feishu.auth_state import AUTH_WAIT_STATUSES
from app.feishu.client import FeishuClient
from app.feishu.sync import sync_feishu_doc
from app.storage.oss.client import OssClient
from app.storage.pg.models import SourceDoc, SyncState
from app.storage.viking.client import VikingClient, get_viking

logger = logging.getLogger(__name__)


def should_poll(
    doc: SourceDoc,
    sync: SyncState,
    *,
    now: datetime,
    interval_sec: int,
) -> bool:
    """判断 doc 是否到达轮询窗口（§9.1）。"""
    if doc.source != "feishu" or doc.status != "active":
        return False
    if sync.sync_status == "syncing" or doc.sync_status == "syncing":
        return False
    if doc.sync_status in AUTH_WAIT_STATUSES or doc.sync_status == "auth_timeout":
        return False
    if sync.sync_status == "quarantine":
        return False
    if sync.next_poll_at is not None and now < sync.next_poll_at:
        return False
    ref = sync.last_sync_at or sync.last_poll_at
    if ref is None:
        return True
    return (now - ref).total_seconds() >= interval_sec


async def feishu_poll_tick(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    interval_sec: int | None = None,
    settings: Settings | None = None,
    client: FeishuClient | None = None,
    oss: OssClient | None = None,
    viking: VikingClient | None = None,
) -> int:
    """扫描 active 飞书 source_doc 并触发 sync；返回成功启动同步的数量。"""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    interval = interval_sec if interval_sec is not None else settings.feishu_poll_interval_sec
    feishu = client or FeishuClient()
    oss_client = oss or OssClient()
    viking_client = viking or get_viking()

    rows = (
        await session.execute(
            select(SourceDoc, SyncState)
            .join(SyncState, SyncState.source_doc_id == SourceDoc.id)
            .where(SourceDoc.source == "feishu", SourceDoc.status == "active")
        )
    ).all()

    started = 0
    for doc, sync in rows:
        doc_interval = doc.sync_interval_sec or interval
        if not should_poll(doc, sync, now=now, interval_sec=doc_interval):
            continue
        sync.last_poll_at = now
        sync.next_poll_at = now + timedelta(seconds=doc_interval)
        try:
            await sync_feishu_doc(
                session,
                doc.id,
                client=feishu,
                oss=oss_client,
                viking=viking_client,
                triggered_by="poll",
                run_phase2=True,
            )
            started += 1
        except Exception:
            logger.exception("feishu poll sync failed doc=%s", doc.id)
    return started
