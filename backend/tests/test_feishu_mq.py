"""飞书 MQ 消息与 consumer 单测。"""

import asyncio

import pytest

from app.config import get_settings
from app.storage.mq.backend import get_memory_backend, reset_memory_backend
from app.storage.mq.consumer import FeishuEventConsumer
from app.storage.mq.message import FeishuEventMessage
from app.storage.mq.producer import FeishuEventProducer
from tests.conftest import RecordingViking
from tests.test_feishu_sync import FakeOss, seed_feishu_doc


@pytest.fixture(autouse=True)
def _memory_mq():
    reset_memory_backend()
    yield
    reset_memory_backend()


@pytest.fixture
def mq_session_factory(db_session, monkeypatch):
    """Consumer 与用例共用 db_session 连接，避免 savepoint 隔离导致「unknown doc」。"""

    class _SessionCtx:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *args):
            return None

    def factory():
        return _SessionCtx(db_session)

    monkeypatch.setattr("app.storage.mq.consumer.get_session_factory", lambda: factory)
    return db_session


class TestFeishuEventMessage:
    def test_roundtrip(self):
        msg = FeishuEventMessage(
            source_doc_id=1,
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            triggered_by="event",
            retry_count=2,
        )
        restored = FeishuEventMessage.from_bytes(msg.to_bytes())
        assert restored.source_doc_id == 1
        assert restored.feishu_doc_token == "tok"
        assert restored.triggered_by == "event"
        assert restored.retry_count == 2
        assert restored.enqueued_at

    def test_with_retry_increments(self):
        msg = FeishuEventMessage(
            source_doc_id=1,
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            triggered_by="poll",
            retry_count=1,
        )
        assert msg.with_retry().retry_count == 2


class TestFeishuMqConsumer:
    async def test_publish_and_consume_ok(self, mq_session_factory, monkeypatch):
        doc, _ = await seed_feishu_doc(mq_session_factory)
        backend = get_memory_backend()
        producer = FeishuEventProducer(backend)
        consumer = FeishuEventConsumer(
            backend=backend,
            producer=producer,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )

        calls: list[int] = []

        async def fake_sync(session, source_doc_id, **kwargs):
            calls.append(source_doc_id)

        monkeypatch.setattr("app.storage.mq.consumer.sync_feishu_doc", fake_sync)

        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
        )
        await producer.publish(msg)
        raw = await backend.receive(get_settings().rocketmq_topic_feishu_event, timeout=1)
        assert raw is not None
        result = await consumer.handle_raw(raw)
        assert result == "ok"
        assert calls == [doc.id]

    async def test_skip_when_syncing(self, mq_session_factory):
        doc, sync = await seed_feishu_doc(mq_session_factory)
        sync.sync_status = "syncing"
        await mq_session_factory.commit()

        backend = get_memory_backend()
        consumer = FeishuEventConsumer(backend=backend, oss=FakeOss(), viking=RecordingViking().client)
        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
        )
        assert await consumer.handle_message(msg) == "skip"

    async def test_does_not_skip_idle_doc_for_new_edit_event(self, mq_session_factory, monkeypatch):
        """idle 不代表内容未变；去重由 sync_feishu_doc 内 has_sync_receipt 负责。"""
        doc, sync = await seed_feishu_doc(mq_session_factory)
        sync.sync_status = "idle"
        sync.content_hash = "a" * 64
        sync.last_error = None
        await mq_session_factory.commit()

        calls: list[int] = []

        async def fake_sync(session, source_doc_id, **kwargs):
            calls.append(source_doc_id)

        monkeypatch.setattr("app.storage.mq.consumer.sync_feishu_doc", fake_sync)

        consumer = FeishuEventConsumer(
            backend=get_memory_backend(), oss=FakeOss(), viking=RecordingViking().client
        )
        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
        )
        assert await consumer.handle_message(msg) == "ok"
        assert calls == [doc.id]

    async def test_retry_non_memory_republishes_immediately(self, mq_session_factory, monkeypatch):
        doc, _ = await seed_feishu_doc(mq_session_factory)
        backend = get_memory_backend()
        producer = FeishuEventProducer(backend)
        consumer = FeishuEventConsumer(
            backend=backend,
            producer=producer,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )
        settings = get_settings()
        monkeypatch.setattr(settings, "feishu_mq_backend", "rocketmq")

        async def fail_sync(session, source_doc_id, **kwargs):
            from app.feishu.sync import FeishuSyncError

            raise FeishuSyncError("phase1_failed", "boom")

        monkeypatch.setattr("app.storage.mq.consumer.sync_feishu_doc", fail_sync)

        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
            retry_count=0,
        )
        assert await consumer.handle_message(msg) == "retry"
        retry_raw = await backend.receive(settings.rocketmq_topic_feishu_event, timeout=1)
        assert retry_raw is not None
        retry_msg = FeishuEventMessage.from_bytes(retry_raw)
        assert retry_msg.retry_count == 1
        dlq_raw = await backend.receive(settings.rocketmq_topic_feishu_event_dlq, timeout=0.05)
        assert dlq_raw is None

    async def test_retry_then_dlq(self, mq_session_factory, monkeypatch, caplog):
        doc, _ = await seed_feishu_doc(mq_session_factory)
        backend = get_memory_backend()
        producer = FeishuEventProducer(backend)
        consumer = FeishuEventConsumer(
            backend=backend,
            producer=producer,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )
        settings = get_settings()

        async def fail_sync(session, source_doc_id, **kwargs):
            from app.feishu.sync import FeishuSyncError

            raise FeishuSyncError("phase1_failed", "boom")

        monkeypatch.setattr("app.storage.mq.consumer.sync_feishu_doc", fail_sync)

        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
            retry_count=3,
        )
        with caplog.at_level("ERROR"):
            assert await consumer.handle_message(msg) == "dlq"
        assert any("FEISHU_DLQ_PUSH" in record.message for record in caplog.records)
        dlq_raw = await backend.receive(settings.rocketmq_topic_feishu_event_dlq, timeout=1)
        assert dlq_raw is not None
        dlq_msg = FeishuEventMessage.from_bytes(dlq_raw)
        assert dlq_msg.retry_count == 3

    async def test_schedule_retry_republishes(self, mq_session_factory, monkeypatch):
        doc, _ = await seed_feishu_doc(mq_session_factory)
        backend = get_memory_backend()
        producer = FeishuEventProducer(backend)
        consumer = FeishuEventConsumer(
            backend=backend,
            producer=producer,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )
        settings = get_settings()

        async def fail_sync(session, source_doc_id, **kwargs):
            from app.feishu.sync import FeishuSyncError

            raise FeishuSyncError("phase1_failed", "boom")

        monkeypatch.setattr("app.storage.mq.consumer.sync_feishu_doc", fail_sync)

        msg = FeishuEventMessage(
            source_doc_id=doc.id,
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            triggered_by="event",
            retry_count=0,
        )
        assert await consumer.handle_message(msg) == "retry"
        await asyncio.sleep(0.05)
        retry_raw = await backend.receive(settings.rocketmq_topic_feishu_event, timeout=1)
        assert retry_raw is not None
        retry_msg = FeishuEventMessage.from_bytes(retry_raw)
        assert retry_msg.retry_count == 1
