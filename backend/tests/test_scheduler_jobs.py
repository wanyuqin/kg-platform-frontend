"""scheduler 任务体（技术设计文档 二 / 8.4 / 十一；草稿清理为 ADR-0021）。"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.pipeline.publish import publish
from app.scheduler import jobs
from app.storage.pg.models import Domain, Knowledge
from tests.conftest import RecordingViking
from tests.test_publish import FAQ_SECTIONS, make_input


class ProbeViking:
    """is_indexed / write 可编程桩。"""

    def __init__(self):
        self.indexed_uris: set[str] = set()
        self.write_fail = False
        self.writes: list[str] = []

    async def write(self, uri: str, content: str) -> None:
        from app.storage.viking.client import VikingError

        if self.write_fail:
            raise VikingError("still down")
        self.writes.append(uri)

    async def is_indexed(self, uri: str, probe_query: str) -> bool:
        return uri in self.indexed_uris


@pytest.fixture
async def domain(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    await db_session.commit()
    return "free-order"


@pytest.fixture(autouse=True)
def _reset_retry_state():
    jobs._retry_state.clear()
    yield
    jobs._retry_state.clear()


async def seed_published(db_session, index_state: str) -> str:
    rv = RecordingViking()
    result = await publish(db_session, rv.client, make_input())
    row = (
        await db_session.execute(select(Knowledge).where(Knowledge.kid == result.kid))
    ).scalar_one()
    row.index_state = index_state
    await db_session.commit()
    return result.kid


class TestRetryFailedIndex:
    async def test_retry_success_sets_indexing(self, db_session, domain):
        kid = await seed_published(db_session, "failed")
        viking = ProbeViking()
        retried = await jobs.retry_failed_index(db_session, viking)
        assert retried == 1
        assert viking.writes == [f"viking://resources/free-order/faq/{kid}.md"]
        row = await db_session.get(Knowledge, kid)
        assert row.index_state == "indexing"  # ready 由就绪轮询置位

    async def test_retry_failure_backs_off(self, db_session, domain):
        kid = await seed_published(db_session, "failed")
        viking = ProbeViking()
        viking.write_fail = True
        await jobs.retry_failed_index(db_session, viking)
        row = await db_session.get(Knowledge, kid)
        assert row.index_state == "failed"
        count, next_at = jobs._retry_state[kid]
        assert count == 1
        assert next_at > datetime.now(UTC)  # 退避期内
        # 退避期内再扫：不重试
        await jobs.retry_failed_index(db_session, viking)
        assert jobs._retry_state[kid][0] == 1

    async def test_gives_up_after_max_retries(self, db_session, domain, caplog):
        kid = await seed_published(db_session, "failed")
        viking = ProbeViking()
        viking.write_fail = True
        jobs._retry_state[kid] = (jobs.MAX_RETRIES, datetime.now(UTC) - timedelta(seconds=1))
        with caplog.at_level("ERROR"):
            retried = await jobs.retry_failed_index(db_session, viking)
        assert retried == 0
        assert any(kid in r.getMessage() for r in caplog.records)  # 告警平台管理员（8.4）


class TestPollIndexingReady:
    async def test_ready_when_probe_hits(self, db_session, domain):
        kid = await seed_published(db_session, "indexing")
        viking = ProbeViking()
        viking.indexed_uris.add(f"viking://resources/free-order/faq/{kid}.md")
        assert await jobs.poll_indexing_ready(db_session, viking) == 1
        row = await db_session.get(Knowledge, kid)
        assert row.index_state == "ready"

    async def test_stays_indexing_when_not_ready(self, db_session, domain):
        kid = await seed_published(db_session, "indexing")
        assert await jobs.poll_indexing_ready(db_session, ProbeViking()) == 0
        row = await db_session.get(Knowledge, kid)
        assert row.index_state == "indexing"


async def partition_names(db_session) -> set[str]:
    rows = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'audit_log_%'")
    )
    return {r[0] for r in rows}


class TestAuditPartitions:
    async def test_precreate_next_month(self, db_session):
        created = await jobs.precreate_audit_partition(db_session, today=date(2026, 8, 25))
        assert created == "audit_log_2026_09"
        assert "audit_log_2026_09" in await partition_names(db_session)

    async def test_precreate_idempotent(self, db_session):
        # 2026_08 分区迁移已建，重复预建不报错
        created = await jobs.precreate_audit_partition(db_session, today=date(2026, 7, 25))
        assert created == "audit_log_2026_08"

    async def test_drop_expired_partitions(self, db_session):
        dropped = await jobs.drop_expired_audit_partitions(
            db_session, today=date(2027, 4, 1), retention_days=180
        )
        assert set(dropped) == {"audit_log_2026_07", "audit_log_2026_08"}
        assert not await partition_names(db_session) & set(dropped)

    async def test_drop_keeps_partitions_within_retention(self, db_session):
        dropped = await jobs.drop_expired_audit_partitions(
            db_session, today=date(2026, 9, 1), retention_days=180
        )
        assert dropped == []


class TestCleanupStaleDrafts:
    async def test_deletes_drafts_older_than_30_days(self, db_session, domain):
        from app.pipeline.publish import save_draft

        kid_old = await save_draft(db_session, make_input())
        kid_new = await save_draft(
            db_session,
            make_input(sections={**FAQ_SECTIONS, "标准问法": "另一条？"}, title="另一条？"),
        )
        await db_session.execute(
            text("UPDATE knowledge SET updated_at = now() - interval '31 days' WHERE kid = :k"),
            {"k": kid_old},
        )
        await db_session.commit()

        deleted = await jobs.cleanup_stale_drafts(db_session)
        assert deleted == 1
        assert await db_session.get(Knowledge, kid_old) is None
        assert (await db_session.get(Knowledge, kid_new)).status == "draft"

    async def test_does_not_touch_published(self, db_session, domain):
        kid = await seed_published(db_session, "ready")
        await db_session.execute(
            text("UPDATE knowledge SET updated_at = now() - interval '99 days' WHERE kid = :k"),
            {"k": kid},
        )
        await db_session.commit()
        assert await jobs.cleanup_stale_drafts(db_session) == 0
        assert (await db_session.get(Knowledge, kid)).status == "published"
