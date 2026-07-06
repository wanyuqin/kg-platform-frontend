"""飞书授权轮询 scheduler 单测。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx

from app.feishu.client import FeishuClient
from app.scheduler.feishu_auth_poll import feishu_auth_poll_tick, feishu_auth_timeout_tick
from app.storage.pg.models import SourceDoc, SyncState
from tests.conftest import RecordingViking
from tests.test_feishu_sync import FakeOss, seed_feishu_doc


def _perm_denied_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if "app_access_token" in str(request.url):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
        return httpx.Response(200, json={"code": 231002, "msg": "no permission"})

    return httpx.MockTransport(handler)


def _perm_ok_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if "app_access_token" in str(request.url):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
        if "/documents/" in request.url.path:
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "T", "document_id": "feishu_tok_1"}}},
            )
        if "/blocks/" in request.url.path:
            from tests.test_feishu_sync import faq_doc_blocks

            return httpx.Response(200, json={"code": 0, "data": {"items": faq_doc_blocks()}})
        raise AssertionError(request.url)

    return httpx.MockTransport(handler)


class TestFeishuAuthTimeoutTick:
    async def test_marks_auth_timeout(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        doc.sync_status = "awaiting_auth"
        doc.awaiting_auth_since = datetime.now(UTC) - timedelta(hours=25)
        await db_session.commit()

        n = await feishu_auth_timeout_tick(db_session, timeout_hours=24)
        assert n == 1
        await db_session.refresh(doc)
        assert doc.sync_status == "auth_timeout"


class TestFeishuAuthPollTick:
    async def test_recovers_and_syncs(self, db_session, monkeypatch):
        doc, sync = await seed_feishu_doc(db_session)
        doc.sync_status = "permission_revoked"
        doc.awaiting_auth_since = datetime.now(UTC) - timedelta(minutes=10)
        sync.last_auth_check_at = datetime.now(UTC) - timedelta(minutes=5)
        await db_session.commit()

        client = FeishuClient(transport=_perm_ok_transport())
        viking = RecordingViking()
        sync_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("app.scheduler.feishu_auth_poll.sync_feishu_doc", sync_mock)

        timed_out, recovered = await feishu_auth_poll_tick(
            db_session,
            client=client,
            oss=FakeOss(),
            viking=viking.client,
            interval_sec=60,
        )
        assert recovered == 1
        sync_mock.assert_awaited_once()

    async def test_still_denied_keeps_waiting(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        doc.sync_status = "awaiting_auth"
        doc.awaiting_auth_since = datetime.now(UTC) - timedelta(minutes=10)
        sync.last_auth_check_at = datetime.now(UTC) - timedelta(minutes=5)
        await db_session.commit()

        timed_out, recovered = await feishu_auth_poll_tick(
            db_session,
            client=FeishuClient(transport=_perm_denied_transport()),
            oss=FakeOss(),
            viking=RecordingViking().client,
            interval_sec=60,
        )
        assert recovered == 0
        await db_session.refresh(doc)
        assert doc.sync_status == "awaiting_auth"

    async def test_timeout_and_recovery_in_one_query(self, db_session, monkeypatch):
        now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)

        timed_out_doc, timed_out_sync = await seed_feishu_doc(db_session)
        timed_out_doc.feishu_doc_token = "feishu_tok_timeout"
        timed_out_doc.sync_status = "awaiting_auth"
        timed_out_doc.awaiting_auth_since = now - timedelta(hours=25)
        timed_out_sync.feishu_doc_token = "feishu_tok_timeout"

        recover_doc = SourceDoc(
            name="待恢复",
            domain_code="free-order",
            type="faq",
            source="feishu",
            source_url="https://feishu.cn/docx/feishu_tok_recover",
            feishu_doc_token="feishu_tok_recover",
            feishu_doc_type="docx",
            sync_status="awaiting_auth",
            awaiting_auth_since=now - timedelta(minutes=10),
            created_by="ou_sync",
        )
        db_session.add(recover_doc)
        await db_session.flush()
        recover_sync = SyncState(
            source_doc_id=recover_doc.id,
            domain_code="free-order",
            feishu_doc_token="feishu_tok_recover",
            feishu_doc_type="docx",
            registered_by="ou_sync",
            last_auth_check_at=now - timedelta(minutes=5),
        )
        db_session.add(recover_sync)
        await db_session.commit()

        client = FeishuClient(transport=_perm_ok_transport())
        sync_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("app.scheduler.feishu_auth_poll.sync_feishu_doc", sync_mock)

        timed_out, recovered = await feishu_auth_poll_tick(
            db_session,
            now=now,
            client=client,
            oss=FakeOss(),
            viking=RecordingViking().client,
            interval_sec=60,
            timeout_hours=24,
        )
        assert timed_out == 1
        assert recovered == 1
        await db_session.refresh(timed_out_doc)
        await db_session.refresh(recover_doc)
        assert timed_out_doc.sync_status == "auth_timeout"
        sync_mock.assert_awaited_once()
        assert sync_mock.await_args.args[1] == recover_doc.id
