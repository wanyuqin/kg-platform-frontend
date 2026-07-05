"""RocketMQ 生产者（飞书事件 topic）。"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.storage.mq.backend import MqBackend, get_mq_backend
from app.storage.mq.message import FeishuEventMessage

logger = logging.getLogger(__name__)


class FeishuEventProducer:
    def __init__(self, backend: MqBackend | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._backend = backend or get_mq_backend(self._settings)

    async def publish(self, message: FeishuEventMessage) -> None:
        topic = self._settings.rocketmq_topic_feishu_event
        await self._backend.publish(
            topic,
            message.to_bytes(),
            keys=str(message.source_doc_id),
        )
        logger.info(
            "feishu event enqueued doc=%s trigger=%s retry=%d",
            message.source_doc_id,
            message.triggered_by,
            message.retry_count,
        )

    async def publish_dlq(self, message: FeishuEventMessage) -> None:
        topic = self._settings.rocketmq_topic_feishu_event_dlq
        await self._backend.publish(topic, message.to_bytes(), keys=str(message.source_doc_id))
        logger.error(
            "feishu event moved to DLQ doc=%s token=%s retries=%d",
            message.source_doc_id,
            message.feishu_doc_token,
            message.retry_count,
        )
