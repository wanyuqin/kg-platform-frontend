"""审计批量写入（技术设计文档 十一）。

flush 路径注入测试 session（savepoint 隔离）；消费协程集成测试用独立
factory 写真 kg_test 并自行 TRUNCATE 清理。
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit import writer
from app.storage.pg.models import AuditLog
from tests.conftest import TEST_DB_URL


def search_record(**overrides) -> dict:
    record = {
        "ts": datetime.now(UTC),
        "key_id": "k1234567",
        "action": "search",
        "query": "发票怎么开",
        "filter_type": ["faq"],
        "filter_tag": None,
        "hits": [{"kid": "faq-fo-0001", "version": 1, "score": 0.9}],
        "excluded_expired": 1,
        "latency_ms": 120,
    }
    record.update(overrides)
    return record


def read_record(**overrides) -> dict:
    record = {
        "ts": datetime.now(UTC),
        "key_id": "k1234567",
        "action": "read",
        "kid": "faq-fo-0001",
        "version": 2,
        "latency_ms": 15,
    }
    record.update(overrides)
    return record


class TestFlushOnce:
    async def test_empty_queue_returns_zero(self, db_session):
        assert await writer.flush_once(db_session) == 0

    async def test_writes_search_and_read_records(self, db_session):
        writer.enqueue(search_record())
        writer.enqueue(read_record())
        written = await writer.flush_once(db_session)
        assert written == 2

        rows = (await db_session.execute(select(AuditLog))).scalars().all()
        by_action = {r.action: r for r in rows}
        s = by_action["search"]
        assert s.query == "发票怎么开"
        assert s.filter_type == ["faq"]
        assert s.hits == [{"kid": "faq-fo-0001", "version": 1, "score": 0.9}]
        assert s.excluded_expired == 1
        assert s.kid is None  # 异构字段补齐 None
        r = by_action["read"]
        assert r.kid == "faq-fo-0001" and r.version == 2
        assert r.query is None

    async def test_bad_batch_dropped_queue_not_blocked(self, db_session):
        # 毒丸防护：非法记录导致整批丢弃并记日志，后续批次不受影响
        writer.enqueue(search_record(action="invalid-action-too-long"))
        assert await writer.flush_once(db_session) == 0
        writer.enqueue(read_record())
        assert await writer.flush_once(db_session) == 1


class TestCollectBatch:
    async def test_respects_batch_cap(self):
        queue: asyncio.Queue = asyncio.Queue()
        for i in range(3):
            queue.put_nowait(read_record(version=i))
        batch = await writer._collect_batch(queue, max_batch=2, window_s=0.05)
        assert len(batch) == 2
        assert queue.qsize() == 1

    async def test_window_closes_batch(self):
        queue: asyncio.Queue = asyncio.Queue()
        queue.put_nowait(read_record())
        batch = await writer._collect_batch(queue, max_batch=500, window_s=0.05)
        assert len(batch) == 1  # 1s 窗口语义：不足 500 条到时即冲


class TestLifespanWiring:
    def test_app_lifespan_flushes_on_shutdown(self, migrated_db):
        # TestClient with 块触发 startup/shutdown；shutdown 清尾保证记录落库
        import psycopg
        from fastapi.testclient import TestClient

        from app.main import create_app

        with TestClient(create_app()) as client:
            assert client.get("/healthz").status_code == 200
            writer.enqueue(read_record(kid="faq-fo-lifespan"))

        with psycopg.connect("postgresql://kg:kg@localhost:5433/kg_test", autocommit=True) as conn:
            count = conn.execute(
                "SELECT count(*) FROM audit_log WHERE kid = 'faq-fo-lifespan'"
            ).fetchone()[0]
            conn.execute("TRUNCATE audit_log")
        assert count == 1


class TestConsumerLoop:
    async def test_consumer_writes_enqueued_records(self, migrated_db):
        engine = create_async_engine(TEST_DB_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        task = asyncio.create_task(writer.run_consumer(factory, window_s=0.05))
        try:
            writer.enqueue(read_record(kid="faq-fo-loop"))
            for _ in range(40):  # 最多等 2s
                await asyncio.sleep(0.05)
                async with factory() as s:
                    found = (
                        await s.execute(select(AuditLog).where(AuditLog.kid == "faq-fo-loop"))
                    ).scalar_one_or_none()
                if found:
                    break
            assert found is not None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with factory() as s:
                await s.execute(text("TRUNCATE audit_log"))
                await s.commit()
            await engine.dispose()
