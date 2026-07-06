"""控制台飞书同步 API 测试。"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.console import auth as console_auth
from app.console import feishu_sync as feishu_sync_api
from app.feishu.client import FeishuClient
from app.storage.pg.models import (
    ConsoleUser,
    Domain,
    DomainMember,
    ImportBatch,
    Knowledge,
    SourceDoc,
    SyncState,
)
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_feishu_sync import FakeOss, _feishu_mock_transport


async def cookies_for(user_id: str) -> dict:
    return {console_auth.COOKIE_NAME: await console_auth.create_session(user_id)}


def _mock_client(handler=None) -> FeishuClient:
    return FeishuClient(transport=handler or _feishu_mock_transport())


@pytest.fixture
def viking():
    return RecordingViking()


@pytest.fixture
async def app_client(db_session, viking, monkeypatch):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    async def _inline_phase2(
        source_doc_id: int,
        phase1,
        actor_user_id: str,
        viking_client=None,
    ):
        await feishu_sync_api.sync_feishu_doc_phase2(
            db_session,
            source_doc_id,
            phase1,
            viking=viking_client or viking.client,
            feishu_client=_mock_client(),
            actor_user_id=actor_user_id,
        )

    def _enqueue(background_tasks, source_doc_id, phase1, actor_user_id, *, viking):
        background_tasks.add_task(_inline_phase2, source_doc_id, phase1, actor_user_id, viking)

    monkeypatch.setattr(feishu_sync_api, "enqueue_phase2", _enqueue)

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: viking.client
    app.dependency_overrides[feishu_sync_api.get_feishu_client] = lambda: _mock_client()
    app.dependency_overrides[feishu_sync_api.get_oss_client] = lambda: FakeOss()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, app


@pytest.fixture
async def seeded(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    db_session.add(ConsoleUser(user_id="ou_member", name="member"))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    await db_session.commit()


async def _seed_feishu_doc(db_session) -> tuple[SourceDoc, SyncState]:
    doc = SourceDoc(
        name="已有飞书FAQ",
        domain_code="free-order",
        type="faq",
        source="feishu",
        source_url="https://feishu.cn/docx/feishu_tok_1",
        source_title="飞书 FAQ",
        feishu_doc_token="feishu_tok_1",
        feishu_doc_type="docx",
        feishu_url="https://feishu.cn/docx/feishu_tok_1",
        sync_status="pending",
        created_by="ou_member",
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        SyncState(
            source_doc_id=doc.id,
            domain_code="free-order",
            feishu_doc_token="feishu_tok_1",
            feishu_doc_type="docx",
            feishu_title="飞书 FAQ",
            feishu_url="https://feishu.cn/docx/feishu_tok_1",
            registered_by="ou_member",
        )
    )
    await db_session.commit()
    sync = (
        await db_session.execute(select(SyncState).where(SyncState.source_doc_id == doc.id))
    ).scalar_one()
    return doc, sync


class TestResolveFeishuDoc:
    async def test_resolve_ok(self, app_client, seeded):
        client, _ = app_client
        resp = await client.post(
            "/api/source-docs/resolve",
            json={"feishu_url": "https://feishu.cn/docx/feishu_tok_1"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["permission_check"]["ok"] is True
        assert body["feishu_doc_token"] == "feishu_tok_1"

    async def test_resolve_permission_denied(self, app_client, seeded):
        client, app = app_client

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 231002, "msg": "denied"})

        app.dependency_overrides[feishu_sync_api.get_feishu_client] = lambda: FeishuClient(
            transport=httpx.MockTransport(handler)
        )
        resp = await client.post(
            "/api/source-docs/resolve",
            json={"feishu_url": "https://feishu.cn/docx/nope"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        assert resp.json()["permission_check"]["error_code"] == "feishu_app_not_in_kb"


class TestCreateFeishuSourceDoc:
    async def test_create_and_sync(self, app_client, seeded, db_session, viking):
        client, _ = app_client
        resp = await client.post(
            "/api/source-docs",
            json={
                "domain": "free-order",
                "type": "faq",
                "name": "飞书绑定FAQ",
                "feishu_url": "https://feishu.cn/docx/feishu_tok_1",
            },
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["phase1"]["ok"] is True

        doc = await db_session.get(SourceDoc, body["id"])
        assert doc.source == "feishu"
        sync = (
            await db_session.execute(select(SyncState).where(SyncState.source_doc_id == doc.id))
        ).scalar_one()
        assert sync.feishu_doc_token == "feishu_tok_1"
        assert viking.writes

    async def test_create_permission_denied_403(self, app_client, seeded):
        client, app = app_client

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 231002, "msg": "denied"})

        app.dependency_overrides[feishu_sync_api.get_feishu_client] = lambda: FeishuClient(
            transport=httpx.MockTransport(handler)
        )
        resp = await client.post(
            "/api/source-docs",
            json={
                "domain": "free-order",
                "type": "faq",
                "name": "无权限",
                "feishu_url": "https://feishu.cn/docx/x",
            },
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 403


class TestFeishuSyncOps:
    async def test_manual_sync(self, app_client, seeded, db_session):
        client, _ = app_client
        doc, _ = await _seed_feishu_doc(db_session)
        resp = await client.post(
            f"/api/source-docs/{doc.id}/sync",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        assert resp.json()["phase1"]["ok"] is True
        body = resp.json()
        assert body["sync_status"] == "syncing"
        assert body["next"] == "phase2 running"

    async def test_sync_status_and_history(self, app_client, seeded, db_session):
        client, _ = app_client
        doc, _ = await _seed_feishu_doc(db_session)
        await client.post(
            f"/api/source-docs/{doc.id}/sync",
            cookies=await cookies_for("ou_member"),
        )
        status = await client.get(
            f"/api/source-docs/{doc.id}/sync-status",
            cookies=await cookies_for("ou_member"),
        )
        assert status.status_code == 200
        assert status.json()["feishu_doc_token"] == "feishu_tok_1"

        history = await client.get(
            f"/api/source-docs/{doc.id}/sync-history",
            cookies=await cookies_for("ou_member"),
        )
        assert history.status_code == 200
        assert len(history.json()["items"]) >= 1


class TestUnbindFeishu:
    async def test_unbind_rejects_while_syncing(self, app_client, seeded, db_session, monkeypatch):
        client, _ = app_client
        monkeypatch.setattr(feishu_sync_api, "enqueue_phase2", lambda *args, **kwargs: None)

        doc, _ = await _seed_feishu_doc(db_session)
        sync_resp = await client.post(
            f"/api/source-docs/{doc.id}/sync",
            cookies=await cookies_for("ou_member"),
        )
        assert sync_resp.status_code == 200

        unbind_resp = await client.post(
            f"/api/source-docs/{doc.id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert unbind_resp.status_code == 409

    async def test_phase2_abandons_batch_after_unbind(self, seeded, db_session, viking, monkeypatch):
        from app.console.feishu_sync import run_phase2_job

        class _SessionCtx:
            def __init__(self, session):
                self._session = session

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, *args):
                return None

        monkeypatch.setattr(
            feishu_sync_api,
            "get_session_factory",
            lambda: (lambda: _SessionCtx(db_session)),
        )

        doc, sync = await _seed_feishu_doc(db_session)
        client = FeishuClient(transport=_feishu_mock_transport())
        phase1 = await feishu_sync_api.sync_feishu_doc_phase1(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            triggered_by="manual",
            actor_user_id="ou_member",
        )
        await db_session.commit()

        await db_session.delete(sync)
        doc.source = "manual"
        doc.feishu_doc_token = None
        doc.feishu_url = None
        await db_session.commit()

        await run_phase2_job(doc.id, phase1, "ou_member", viking=viking.client)

        batch = await db_session.get(ImportBatch, phase1.import_batch_id)
        assert batch is not None
        assert batch.status == "discarded"

    async def test_unbind_converts_to_manual(self, app_client, seeded, db_session):
        client, _ = app_client
        doc, _ = await _seed_feishu_doc(db_session)
        resp = await client.post(
            f"/api/source-docs/{doc.id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "manual"

        await db_session.refresh(doc)
        assert doc.source == "manual"
        assert doc.feishu_doc_token is None
        assert doc.feishu_doc_type is None
        assert doc.feishu_url is None
        sync = (
            await db_session.execute(select(SyncState).where(SyncState.source_doc_id == doc.id))
        ).scalar_one_or_none()
        assert sync is None

    async def test_unbind_discards_previewing_batch(self, app_client, seeded, db_session):
        client, _ = app_client
        doc, _ = await _seed_feishu_doc(db_session)
        batch = ImportBatch(
            domain_code=doc.domain_code,
            type=doc.type,
            file_name=doc.name,
            origin="feishu",
            source_doc_id=doc.id,
            created_by="ou_member",
            status="previewing",
        )
        db_session.add(batch)
        doc.sync_status = "failed"
        await db_session.commit()

        resp = await client.post(
            f"/api/source-docs/{doc.id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200

        await db_session.refresh(batch)
        assert batch.status == "discarded"

    async def test_unbind_updates_updated_at(self, app_client, seeded, db_session):
        from datetime import UTC, datetime, timedelta

        client, _ = app_client
        doc, _ = await _seed_feishu_doc(db_session)
        doc.updated_at = datetime.now(UTC) - timedelta(days=1)
        await db_session.commit()
        before = doc.updated_at

        resp = await client.post(
            f"/api/source-docs/{doc.id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200

        await db_session.refresh(doc)
        assert doc.updated_at > before

    async def test_unbind_rejects_non_feishu(self, app_client, seeded, db_session):
        client, _ = app_client
        doc = SourceDoc(
            name="手工文件",
            domain_code="free-order",
            type="faq",
            source="manual",
            created_by="ou_member",
        )
        db_session.add(doc)
        await db_session.commit()
        resp = await client.post(
            f"/api/source-docs/{doc.id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 400

    async def test_unbind_preserves_entries(self, app_client, seeded, db_session, viking):
        client, _ = app_client
        create = await client.post(
            "/api/source-docs",
            json={
                "domain": "free-order",
                "type": "faq",
                "name": "解绑保留条目",
                "feishu_url": "https://feishu.cn/docx/feishu_tok_1",
            },
            cookies=await cookies_for("ou_member"),
        )
        doc_id = create.json()["id"]
        kids_before = (
            (
                await db_session.execute(
                    select(Knowledge.kid).where(Knowledge.source_doc_id == doc_id)
                )
            )
            .scalars()
            .all()
        )
        assert kids_before

        resp = await client.post(
            f"/api/source-docs/{doc_id}/unbind-feishu",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        kids_after = (
            (
                await db_session.execute(
                    select(Knowledge.kid).where(Knowledge.source_doc_id == doc_id)
                )
            )
            .scalars()
            .all()
        )
        assert kids_after == kids_before


class TestFeishuEventCallback:
    async def test_url_verification(self, app_client):
        client, _ = app_client
        resp = await client.post(
            "/api/feishu/event",
            json={"type": "url_verification", "challenge": "hello-challenge"},
        )
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "hello-challenge"

    async def test_edited_event_enqueues(self, app_client, db_session, seeded):
        from app.config import get_settings
        from app.storage.mq.backend import get_memory_backend, reset_memory_backend
        from app.storage.mq.message import FeishuEventMessage

        reset_memory_backend()
        doc, _ = await _seed_feishu_doc(db_session)
        client, _ = app_client
        resp = await client.post(
            "/api/feishu/event",
            json={
                "header": {"event_type": "drive.file.edited"},
                "event": {"file_token": "feishu_tok_1"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "enqueued"

        backend = get_memory_backend()
        raw = await backend.receive(get_settings().rocketmq_topic_feishu_event, timeout=1)
        assert raw is not None
        msg = FeishuEventMessage.from_bytes(raw)
        assert msg.source_doc_id == doc.id
        assert msg.triggered_by == "event"

    async def test_deleted_event_archives(self, app_client, db_session, seeded):
        doc, _ = await _seed_feishu_doc(db_session)
        client, _ = app_client
        resp = await client.post(
            "/api/feishu/event",
            json={
                "header": {"event_type": "drive.file.deleted"},
                "event": {"file_token": "feishu_tok_1"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "archived"
        await db_session.refresh(doc)
        assert doc.status == "archived"
        sync = (
            await db_session.execute(
                select(SyncState).where(SyncState.source_doc_id == doc.id)
            )
        ).scalar_one()
        assert sync.sync_status == "archived"
        assert sync.last_error is None
