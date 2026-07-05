"""飞书授权等待轮询 + 24h 超时（feishu-sync §4.5 / §4.8）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.feishu.auth_state import (
    AUTH_WAIT_STATUSES,
    apply_permission_failure,
    clear_auth_wait,
    is_auth_timed_out,
    mark_auth_timeout,
    should_auth_poll,
)
from app.feishu.client import FeishuClient
from app.feishu.doc_resolver import check_permission
from app.feishu.sync import sync_feishu_doc
from app.storage.oss.client import OssClient
from app.storage.pg.models import SourceDoc, SyncState
from app.storage.viking.client import VikingClient, get_viking

logger = logging.getLogger(__name__)


async def feishu_auth_timeout_tick(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    timeout_hours: int | None = None,
    settings: Settings | None = None,
) -> int:
    """将等待授权超过 timeout 的文档标为 auth_timeout。"""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    timeout_hours = timeout_hours if timeout_hours is not None else settings.feishu_auth_timeout_hours

    rows = (
        await session.execute(
            select(SourceDoc, SyncState)
            .join(SyncState, SyncState.source_doc_id == SourceDoc.id)
            .where(
                SourceDoc.source == "feishu",
                SourceDoc.status == "active",
                SourceDoc.sync_status.in_(tuple(AUTH_WAIT_STATUSES)),
            )
        )
    ).all()

    timed_out = 0
    for doc, sync in rows:
        if not is_auth_timed_out(doc, now=now, timeout_hours=timeout_hours):
            continue
        mark_auth_timeout(doc, sync)
        timed_out += 1
        logger.info("feishu auth timeout doc=%s since=%s", doc.id, doc.awaiting_auth_since)
    if timed_out:
        await session.flush()
    return timed_out


async def feishu_auth_poll_tick(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    interval_sec: int | None = None,
    timeout_hours: int | None = None,
    settings: Settings | None = None,
    client: FeishuClient | None = None,
    oss: OssClient | None = None,
    viking: VikingClient | None = None,
) -> int:
    """轮询 awaiting_auth / permission_revoked 文档；权限恢复则触发同步。"""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    interval = interval_sec if interval_sec is not None else settings.feishu_auth_poll_interval_sec
    timeout_hours = timeout_hours if timeout_hours is not None else settings.feishu_auth_timeout_hours

    await feishu_auth_timeout_tick(session, now=now, timeout_hours=timeout_hours, settings=settings)

    feishu = client or FeishuClient()
    oss_client = oss or OssClient()
    viking_client = viking or get_viking()

    rows = (
        await session.execute(
            select(SourceDoc, SyncState)
            .join(SyncState, SyncState.source_doc_id == SourceDoc.id)
            .where(
                SourceDoc.source == "feishu",
                SourceDoc.status == "active",
                SourceDoc.sync_status.in_(tuple(AUTH_WAIT_STATUSES)),
            )
        )
    ).all()

    recovered = 0
    for doc, sync in rows:
        if not should_auth_poll(
            doc, sync, now=now, interval_sec=interval, timeout_hours=timeout_hours
        ):
            continue
        sync.last_auth_check_at = now
        perm = await check_permission(feishu, sync.feishu_doc_token)
        if not perm.ok:
            apply_permission_failure(doc, sync, perm.error_code or "feishu_api_error", now=now)
            continue
        clear_auth_wait(doc)
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
            recovered += 1
        except Exception:
            logger.exception("feishu auth poll sync failed doc=%s", doc.id)
    return recovered
