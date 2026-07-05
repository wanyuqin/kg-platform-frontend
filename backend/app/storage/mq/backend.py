"""MQ 后端抽象：memory（测试/本地）与 rocketmq（P2 联调）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class MqBackend(Protocol):
    async def publish(self, topic: str, body: bytes, *, keys: str = "") -> None: ...

    async def receive(self, topic: str, *, timeout: float | None = None) -> bytes | None: ...


class MemoryMqBackend:
    """进程内 asyncio 队列；单测与无 RocketMQ 环境使用。"""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[bytes]] = {}

    def _queue(self, topic: str) -> asyncio.Queue[bytes]:
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue()
        return self._queues[topic]

    async def publish(self, topic: str, body: bytes, *, keys: str = "") -> None:
        await self._queue(topic).put(body)

    async def receive(self, topic: str, *, timeout: float | None = None) -> bytes | None:
        try:
            if timeout is None:
                return await self._queue(topic).get()
            return await asyncio.wait_for(self._queue(topic).get(), timeout=timeout)
        except TimeoutError:
            return None

    def pending(self, topic: str) -> int:
        return self._queue(topic).qsize()


_memory_singleton: MemoryMqBackend | None = None


def get_memory_backend() -> MemoryMqBackend:
    global _memory_singleton
    if _memory_singleton is None:
        _memory_singleton = MemoryMqBackend()
    return _memory_singleton


def reset_memory_backend() -> None:
    global _memory_singleton
    _memory_singleton = None


class RocketMqBackend:
    """rocketmq-client-python 薄封装；仅在 KG_FEISHU_MQ_BACKEND=rocketmq 时使用。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._producer = None

    def _ensure_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            from rocketmq.client import Producer
        except ImportError as exc:
            raise RuntimeError(
                "rocketmq-client-python 未安装，请 uv add rocketmq-client-python "
                "或设置 KG_FEISHU_MQ_BACKEND=memory"
            ) from exc
        producer = Producer("kg-feishu-producer")
        producer.set_name_server_address(self._settings.rocketmq_namesrv)
        producer.start()
        self._producer = producer
        return producer

    async def publish(self, topic: str, body: bytes, *, keys: str = "") -> None:
        from rocketmq.client import Message

        def _send() -> None:
            producer = self._ensure_producer()
            msg = Message(topic)
            msg.set_body(body)
            if keys:
                msg.set_keys(keys)
            producer.send_sync(msg)

        await asyncio.to_thread(_send)

    async def receive(self, topic: str, *, timeout: float | None = None) -> bytes | None:
        raise NotImplementedError("RocketMQ 消费请使用 FeishuEventConsumer.run_forever")


def get_mq_backend(settings: Settings | None = None) -> MqBackend:
    settings = settings or get_settings()
    if settings.feishu_mq_backend == "rocketmq":
        return RocketMqBackend(settings)
    return get_memory_backend()
