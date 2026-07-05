"""RocketMQ 消费者：飞书同步主流程 + 重试 + DLQ（feishu-sync §11）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.feishu.client import FeishuClient
from app.feishu.exceptions import FeishuPermissionError
from app.feishu.auth_state import apply_permission_failure
from app.feishu.sync import FeishuSyncError, mark_sync_technical_error, sync_feishu_doc
from app.storage.mq.backend import MqBackend, get_mq_backend
from app.storage.mq.message import FeishuEventMessage
from app.storage.mq.producer import FeishuEventProducer
from app.storage.oss.client import OssClient
from app.storage.pg.models import SourceDoc, SyncState
from app.storage.pg.session import get_session_factory
from app.storage.viking.client import VikingClient, get_viking

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS_SEC = (60, 300, 900)


class FeishuEventConsumer:
    def __init__(
        self,
        *,
        backend: MqBackend | None = None,
        producer: FeishuEventProducer | None = None,
        settings: Settings | None = None,
        feishu_client: FeishuClient | None = None,
        oss: OssClient | None = None,
        viking: VikingClient | None = None,
    ):
        self._settings = settings or get_settings()
        self._backend = backend or get_mq_backend(self._settings)
        self._producer = producer or FeishuEventProducer(self._backend, self._settings)
        self._feishu = feishu_client or FeishuClient()
        self._oss = oss or OssClient()
        self._viking = viking or get_viking()
        self._topic = self._settings.rocketmq_topic_feishu_event

    async def run_forever(self, *, poll_timeout: float = 1.0) -> None:
        logger.info("feishu mq consumer started topic=%s", self._topic)
        while True:
            raw = await self._backend.receive(self._topic, timeout=poll_timeout)
            if raw is None:
                await asyncio.sleep(0)
                continue
            await self.handle_raw(raw)

    async def handle_raw(self, raw: bytes) -> Literal["ok", "retry", "dlq", "skip"]:
        message = FeishuEventMessage.from_bytes(raw)
        return await self.handle_message(message)

    async def handle_message(self, message: FeishuEventMessage) -> Literal["ok", "retry", "dlq", "skip"]:
        async with get_session_factory()() as session:
            if await _should_skip_message(session, message):
                await session.commit()
                return "skip"
            try:
                await sync_feishu_doc(
                    session,
                    message.source_doc_id,
                    client=self._feishu,
                    oss=self._oss,
                    viking=self._viking,
                    triggered_by=message.triggered_by,
                    run_phase2=True,
                )
                await session.commit()
                return "ok"
            except FeishuPermissionError as exc:
                if exc.platform_code == "feishu_app_not_in_kb":
                    sync = (
                        await session.execute(
                            select(SyncState).where(
                                SyncState.source_doc_id == message.source_doc_id
                            )
                        )
                    ).scalar_one_or_none()
                    doc = await session.get(SourceDoc, message.source_doc_id)
                    if sync and doc:
                        apply_permission_failure(
                            doc, sync, exc.platform_code or "feishu_app_not_in_kb"
                        )
                else:
                    await mark_sync_technical_error(
                        session, message.source_doc_id, exc.platform_code
                    )
                await session.commit()
                logger.warning(
                    "feishu sync permission error doc=%s code=%s",
                    message.source_doc_id,
                    exc.platform_code,
                )
                return "skip"
            except FeishuSyncError as exc:
                await mark_sync_technical_error(session, message.source_doc_id, exc.code)
                await session.commit()
                return await self._schedule_retry(message)
            except Exception:
                logger.exception("feishu sync failed doc=%s", message.source_doc_id)
                await session.rollback()
                return await self._schedule_retry(message)

    async def _schedule_retry(self, message: FeishuEventMessage) -> Literal["retry", "dlq"]:
        if message.retry_count >= MAX_RETRIES:
            await self._producer.publish_dlq(message)
            return "dlq"
        delay = RETRY_DELAYS_SEC[min(message.retry_count, len(RETRY_DELAYS_SEC) - 1)]
        retry_msg = message.with_retry()
        if self._settings.feishu_mq_backend == "memory" or delay == 0:
            await self._producer.publish(retry_msg)
            return "retry"
        logger.warning(
            "feishu mq retry would sleep %ds; routing to DLQ instead doc=%s",
            delay,
            message.source_doc_id,
        )
        await self._producer.publish_dlq(retry_msg)
        return "dlq"


async def _should_skip_message(session: AsyncSession, message: FeishuEventMessage) -> bool:
    """同 doc 正在 syncing 时跳过重放消息（§11.2 幂等）。"""
    sync = (
        await session.execute(
            select(SyncState).where(SyncState.source_doc_id == message.source_doc_id)
        )
    ).scalar_one_or_none()
    if sync is None:
        logger.warning("feishu mq skip unknown doc=%s", message.source_doc_id)
        return True
    if sync.feishu_doc_token != message.feishu_doc_token:
        logger.warning(
            "feishu mq token mismatch doc=%s msg=%s state=%s",
            message.source_doc_id,
            message.feishu_doc_token,
            sync.feishu_doc_token,
        )
    doc = await session.get(SourceDoc, message.source_doc_id)
    if sync.sync_status == "syncing" or (doc is not None and doc.sync_status == "syncing"):
        return True
    return False
