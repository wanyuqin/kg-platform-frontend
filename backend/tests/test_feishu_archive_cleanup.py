"""飞书归档 30 天物理清理单测。"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.domain.state_machine import Status
from app.scheduler.feishu_archive_cleanup import purge_expired_feishu_archives
from app.storage.pg.models import Knowledge, SourceDoc, SyncState, VikingCleanupFailed
from tests.conftest import RecordingViking
from tests.test_feishu_sync import seed_feishu_doc


class TestPurgeExpiredFeishuArchives:
    async def test_purges_old_archived_doc(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        doc.status = "archived"
        doc.archived_at = datetime.now(UTC) - timedelta(days=31)
        doc.sync_status = "archived"
        db_session.add(
            Knowledge(
                kid="faq-fo-purge1",
                domain_code="free-order",
                type="faq",
                title="Q",
                owner_user_id="ou_sync",
                status=Status.PUBLISHED,
                source_doc_id=doc.id,
                doc_seq=1,
                content_hash="abc1234567890123456789012345678901234567890123456789012345678",
                effective_date=date.today(),
                expire_date=date(2099, 1, 1),
                source_type="feishu_doc",
                source_ref="feishu:tok:1",
            )
        )
        await db_session.commit()

        viking = RecordingViking()
        n = await purge_expired_feishu_archives(
            db_session, viking.client, retention_days=30, now=datetime.now(UTC)
        )
        await db_session.commit()

        assert n == 1
        assert (
            await db_session.execute(select(Knowledge).where(Knowledge.source_doc_id == doc.id))
        ).scalar_one_or_none() is None
        assert (
            await db_session.execute(select(SyncState).where(SyncState.source_doc_id == doc.id))
        ).scalar_one_or_none() is None
        shell = await db_session.get(SourceDoc, doc.id)
        assert shell is not None
        assert shell.status == "archived"
        assert len(viking.deletes) == 1

    async def test_skips_recent_archive(self, db_session):
        doc, _ = await seed_feishu_doc(db_session)
        doc.status = "archived"
        doc.archived_at = datetime.now(UTC) - timedelta(days=5)
        await db_session.commit()

        n = await purge_expired_feishu_archives(
            db_session, RecordingViking().client, retention_days=30
        )
        assert n == 0

    async def test_viking_delete_failure_recorded(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        doc.status = "archived"
        doc.archived_at = datetime.now(UTC) - timedelta(days=31)
        doc.sync_status = "archived"
        db_session.add(
            Knowledge(
                kid="faq-fo-purge2",
                domain_code="free-order",
                type="faq",
                title="Q2",
                owner_user_id="ou_sync",
                status=Status.PUBLISHED,
                source_doc_id=doc.id,
                doc_seq=1,
                content_hash="abc1234567890123456789012345678901234567890123456789012345678",
                effective_date=date.today(),
                expire_date=date(2099, 1, 1),
                source_type="feishu_doc",
                source_ref="feishu:tok:1",
            )
        )
        await db_session.commit()

        class FailDeleteViking:
            def __init__(self):
                from app.storage.viking.client import VikingClient
                import httpx

                def handler(request: httpx.Request) -> httpx.Response:
                    if request.method == "DELETE":
                        raise RuntimeError("viking down")
                    return httpx.Response(200, json={"status": "ok"})

                self.client = VikingClient(transport=httpx.MockTransport(handler))

        await purge_expired_feishu_archives(
            db_session, FailDeleteViking().client, retention_days=30, now=datetime.now(UTC)
        )
        await db_session.commit()

        failed = (
            await db_session.execute(select(VikingCleanupFailed))
        ).scalars().all()
        assert len(failed) == 1
        assert failed[0].uri.startswith("viking://")
