"""飞书同步主流程单元测试（feishu-sync §7）。"""

from datetime import date

import httpx
import pytest
from sqlalchemy import select

from app.domain.state_machine import Status
from app.feishu.client import FeishuClient
from app.feishu.exceptions import FeishuPermissionError
from app.feishu.sync import sync_feishu_doc, sync_feishu_doc_phase1, sync_feishu_doc_phase2
from app.pipeline.publish import PublishInput, publish
from app.storage.pg.models import Domain, ImportBatch, ImportItem, Knowledge, SourceDoc, SyncState
from tests.conftest import RecordingViking


class FakeOss:
    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        return f"http://oss.test/{key}"


def faq_doc_blocks() -> list[dict]:
    """渲染结果对齐 FAQ_MD_OK 的最小 Block 树。"""
    return [
        {"block_id": "page", "block_type": 1, "children": ["h1"]},
        {
            "block_id": "h1",
            "block_type": 3,
            "heading1": {"elements": [{"text_run": {"content": "如何退款？"}}]},
            "children": ["s1", "s2", "s3", "s4"],
        },
        {
            "block_id": "s1",
            "block_type": 4,
            "heading2": {"elements": [{"text_run": {"content": "标准问法"}}]},
            "children": ["t1"],
        },
        {
            "block_id": "t1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "如何退款？"}}]},
            "children": [],
        },
        {
            "block_id": "s2",
            "block_type": 4,
            "heading2": {"elements": [{"text_run": {"content": "相似问法"}}]},
            "children": ["b1", "b2"],
        },
        {
            "block_id": "b1",
            "block_type": 12,
            "bullet": {"elements": [{"text_run": {"content": "退款流程"}}]},
            "children": [],
        },
        {
            "block_id": "b2",
            "block_type": 12,
            "bullet": {"elements": [{"text_run": {"content": "退钱"}}]},
            "children": [],
        },
        {
            "block_id": "s3",
            "block_type": 4,
            "heading2": {"elements": [{"text_run": {"content": "标准答案"}}]},
            "children": ["t2"],
        },
        {
            "block_id": "t2",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "订单页申请。"}}]},
            "children": [],
        },
        {
            "block_id": "s4",
            "block_type": 4,
            "heading2": {"elements": [{"text_run": {"content": "适用条件"}}]},
            "children": ["t3"],
        },
        {
            "block_id": "t3",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "7 天内"}}]},
            "children": [],
        },
    ]


def _feishu_mock_transport(blocks: list[dict] | None = None):
    blocks = blocks if blocks is not None else faq_doc_blocks()

    def handler(request: httpx.Request) -> httpx.Response:
        if "app_access_token" in str(request.url):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
        if "/blocks/" in request.url.path:
            return httpx.Response(200, json={"code": 0, "data": {"items": blocks}})
        if "/documents/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"document": {"title": "飞书 FAQ", "document_id": "feishu_tok_1"}},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _empty_doc_blocks() -> list[dict]:
    return [{"block_id": "page", "block_type": 1, "children": []}]


class FailDeleteViking:
    """viking.delete 抛异常，write 正常。"""

    def __init__(self):
        from app.storage.viking.client import VikingClient

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "DELETE":
                raise RuntimeError("viking down")
            return httpx.Response(200, json={"status": "ok", "result": {}})

        self.client = VikingClient(transport=httpx.MockTransport(handler))


async def seed_feishu_doc(db_session) -> tuple[SourceDoc, SyncState]:
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    doc = SourceDoc(
        name="飞书FAQ",
        domain_code="free-order",
        type="faq",
        source="feishu",
        source_url="https://feishu.cn/docx/feishu_tok_1",
        source_title="飞书 FAQ",
        feishu_doc_token="feishu_tok_1",
        feishu_doc_type="docx",
        feishu_url="https://feishu.cn/docx/feishu_tok_1",
        sync_status="pending",
        created_by="ou_sync",
    )
    db_session.add(doc)
    await db_session.flush()
    sync = SyncState(
        source_doc_id=doc.id,
        domain_code="free-order",
        feishu_doc_token="feishu_tok_1",
        feishu_doc_type="docx",
        feishu_title="飞书 FAQ",
        feishu_url="https://feishu.cn/docx/feishu_tok_1",
        registered_by="ou_sync",
    )
    db_session.add(sync)
    await db_session.commit()
    return doc, sync


class TestSyncPhase1:
    async def test_phase1_creates_import_batch(self, db_session):
        doc, _ = await seed_feishu_doc(db_session)
        client = FeishuClient(transport=_feishu_mock_transport())
        phase1 = await sync_feishu_doc_phase1(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            triggered_by="manual",
        )
        await db_session.commit()

        assert phase1.ok is True
        assert phase1.parsed_items == 1
        assert phase1.blocking_count == 0

        batch = await db_session.get(ImportBatch, phase1.import_batch_id)
        assert batch is not None and batch.origin == "feishu"
        items = (
            await db_session.execute(select(ImportItem).where(ImportItem.batch_id == batch.id))
        ).scalars().all()
        assert len(items) == 1 and items[0].is_valid

    async def test_phase1_permission_denied(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 231002, "msg": "no permission"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        with pytest.raises(FeishuPermissionError) as exc_info:
            await sync_feishu_doc_phase1(
                db_session,
                doc.id,
                client=client,
                oss=FakeOss(),
                triggered_by="bind",
            )
        await db_session.commit()
        assert exc_info.value.platform_code == "feishu_app_not_in_kb"
        await db_session.refresh(sync)
        assert sync.sync_status == "error"


class TestSyncFull:
    async def test_full_sync_publishes_knowledge(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        client = FeishuClient(transport=_feishu_mock_transport())
        viking = RecordingViking()

        result = await sync_feishu_doc(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            viking=viking.client,
            triggered_by="poll",
        )
        await db_session.commit()

        assert result.phase1.ok
        assert result.phase2 is not None
        assert result.phase2.published == 1
        assert result.phase2.sync_status == "idle"

        rows = (
            await db_session.execute(select(Knowledge).where(Knowledge.source_doc_id == doc.id))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "published"
        assert viking.writes

        await db_session.refresh(sync)
        assert sync.content_hash == result.phase1.content_hash
        assert sync.last_sync_at is not None

    async def test_phase1_only_keeps_syncing(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        client = FeishuClient(transport=_feishu_mock_transport())

        result = await sync_feishu_doc(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            viking=RecordingViking().client,
            triggered_by="bind",
            run_phase2=False,
        )
        await db_session.commit()

        assert result.phase1.ok
        assert result.phase2 is None
        await db_session.refresh(sync)
        assert sync.sync_status == "syncing"


class TestPhase2Disappeared:
    async def test_disappeared_viking_delete_failure_rolls_back(self, db_session):
        doc, _ = await seed_feishu_doc(db_session)
        client = FeishuClient(transport=_feishu_mock_transport())
        viking = RecordingViking()

        await sync_feishu_doc(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            viking=viking.client,
            triggered_by="poll",
        )
        await db_session.commit()

        row = (
            await db_session.execute(select(Knowledge).where(Knowledge.source_doc_id == doc.id))
        ).scalar_one()
        assert row.status == Status.PUBLISHED

        empty_client = FeishuClient(transport=_feishu_mock_transport(_empty_doc_blocks()))
        phase1 = await sync_feishu_doc_phase1(
            db_session,
            doc.id,
            client=empty_client,
            oss=FakeOss(),
            triggered_by="manual",
        )
        assert phase1.ok

        result = await sync_feishu_doc_phase2(
            db_session,
            doc.id,
            phase1,
            viking=FailDeleteViking().client,
        )
        await db_session.commit()

        await db_session.refresh(row)
        assert row.status == Status.PUBLISHED
        assert result.archived == 0
        assert result.failed == 1


class TestPhase2ErrorDetail:
    async def test_duplicate_content_records_structured_error(self, db_session):
        feishu_faq_sections = {
            "标准问法": "如何退款？",
            "相似问法": "- 退款流程\n- 退钱",
            "标准答案": "订单页申请。",
            "适用条件": "7 天内",
        }
        doc, sync = await seed_feishu_doc(db_session)
        other_doc = SourceDoc(
            name="测试文件",
            domain_code="free-order",
            type="faq",
            source="manual",
            created_by="t",
        )
        db_session.add(other_doc)
        await db_session.flush()
        viking = RecordingViking()
        await publish(
            db_session,
            viking.client,
            PublishInput(
                domain_code="free-order",
                type_="faq",
                title="库内已有标题",
                sections=feishu_faq_sections,
                tags=[],
                owner_user_id="ou_sync",
                source_type="manual",
                source_ref="form:dup",
                source_url=None,
                effective_date=date.today(),
                expire_date=None,
                actor_user_id="ou_sync",
                source_doc_id=other_doc.id,
                doc_seq=1,
            ),
        )

        client = FeishuClient(transport=_feishu_mock_transport())
        phase1 = await sync_feishu_doc_phase1(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            triggered_by="manual",
            actor_user_id="ou_sync",
        )
        assert phase1.ok

        result = await sync_feishu_doc_phase2(
            db_session,
            doc.id,
            phase1,
            viking=viking.client,
            actor_user_id="ou_sync",
        )
        await db_session.commit()

        assert result.failed == 1
        assert result.sync_status == "error"
        await db_session.refresh(sync)
        await db_session.refresh(doc)
        detail = sync.last_error_detail
        assert detail is not None
        assert detail["code"] == "phase2_partial_failure"
        assert detail["breakdown"]["duplicate_content"] == 1
        assert len(detail["failures"]) == 1
        failure = detail["failures"][0]
        assert failure["reason"] == "duplicate_content"
        assert failure["duplicate"]["kid"] == "faq-fo-0001"
        assert failure["duplicate"]["source_doc_id"] == other_doc.id
        assert "同步失败" in sync.last_error
        assert doc.last_sync_error_detail == detail
