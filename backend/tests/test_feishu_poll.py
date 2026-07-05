"""飞书轮询 worker 单测（feishu-sync §9）。"""

from datetime import UTC, datetime, timedelta


from app.scheduler.feishu_poll import feishu_poll_tick, should_poll
from app.storage.pg.models import SourceDoc, SyncState
from tests.conftest import RecordingViking
from tests.test_feishu_sync import FakeOss, seed_feishu_doc


def _now() -> datetime:
    return datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)


class TestShouldPoll:
    def test_never_synced(self):
        doc = SourceDoc(
            name="x",
            domain_code="fo",
            type="faq",
            source="feishu",
            status="active",
            created_by="t",
        )
        sync = SyncState(
            source_doc_id=1,
            domain_code="fo",
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            registered_by="t",
        )
        assert should_poll(doc, sync, now=_now(), interval_sec=300) is True

    def test_within_interval(self):
        doc = SourceDoc(
            name="x",
            domain_code="fo",
            type="faq",
            source="feishu",
            status="active",
            created_by="t",
        )
        sync = SyncState(
            source_doc_id=1,
            domain_code="fo",
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            registered_by="t",
            last_sync_at=_now() - timedelta(seconds=60),
        )
        assert should_poll(doc, sync, now=_now(), interval_sec=300) is False

    def test_after_interval(self):
        doc = SourceDoc(
            name="x",
            domain_code="fo",
            type="faq",
            source="feishu",
            status="active",
            created_by="t",
        )
        sync = SyncState(
            source_doc_id=1,
            domain_code="fo",
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            registered_by="t",
            last_sync_at=_now() - timedelta(seconds=400),
        )
        assert should_poll(doc, sync, now=_now(), interval_sec=300) is True

    def test_skip_syncing(self):
        doc = SourceDoc(
            name="x",
            domain_code="fo",
            type="faq",
            source="feishu",
            status="active",
            created_by="t",
        )
        sync = SyncState(
            source_doc_id=1,
            domain_code="fo",
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            registered_by="t",
            sync_status="syncing",
        )
        assert should_poll(doc, sync, now=_now(), interval_sec=300) is False

    def test_respects_next_poll_at(self):
        doc = SourceDoc(
            name="x",
            domain_code="fo",
            type="faq",
            source="feishu",
            status="active",
            created_by="t",
        )
        sync = SyncState(
            source_doc_id=1,
            domain_code="fo",
            feishu_doc_token="tok",
            feishu_doc_type="docx",
            registered_by="t",
            next_poll_at=_now() + timedelta(minutes=10),
        )
        assert should_poll(doc, sync, now=_now(), interval_sec=300) is False


class TestFeishuPollTick:
    async def test_poll_invokes_sync(self, db_session, monkeypatch):
        doc, sync = await seed_feishu_doc(db_session)
        sync.last_sync_at = _now() - timedelta(hours=1)
        await db_session.commit()

        calls: list[int] = []

        async def fake_sync(session, source_doc_id, **kwargs):
            calls.append(source_doc_id)

        monkeypatch.setattr("app.scheduler.feishu_poll.sync_feishu_doc", fake_sync)

        n = await feishu_poll_tick(
            db_session,
            now=_now(),
            interval_sec=300,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )
        await db_session.commit()
        assert n == 1
        assert calls == [doc.id]

    async def test_poll_skips_recent(self, db_session, monkeypatch):
        doc, sync = await seed_feishu_doc(db_session)
        sync.last_sync_at = _now() - timedelta(seconds=30)
        await db_session.commit()

        calls: list[int] = []

        async def fake_sync(session, source_doc_id, **kwargs):
            calls.append(source_doc_id)

        monkeypatch.setattr("app.scheduler.feishu_poll.sync_feishu_doc", fake_sync)

        n = await feishu_poll_tick(
            db_session,
            now=_now(),
            interval_sec=300,
            oss=FakeOss(),
            viking=RecordingViking().client,
        )
        assert n == 0
        assert calls == []
