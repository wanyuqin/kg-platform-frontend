"""飞书同步 + 风险矩阵集成测试。"""

import json

import httpx
from sqlalchemy import select

from app.domain.state_machine import Status
from app.feishu.client import FeishuClient
from app.feishu.sync import sync_feishu_doc
from app.storage.pg.models import Domain, Knowledge, ReviewTask
from tests.conftest import RecordingViking
from tests.test_feishu_sync import FakeOss, _feishu_mock_transport, faq_doc_blocks, seed_feishu_doc


def faq_blocks_changed_answer() -> list[dict]:
    blocks = faq_doc_blocks()
    for block in blocks:
        if block.get("block_id") == "t2":
            block["text"]["elements"][0]["text_run"]["content"] = "订单详情页申请。"
    return blocks


class TestSyncWithRiskMatrix:
    async def test_mid_risk_goes_pending_review(self, db_session):
        doc, sync = await seed_feishu_doc(db_session)
        domain = await db_session.get(Domain, "free-order")
        domain.reviewer_user_id = "ou_reviewer"
        await db_session.commit()

        # 先写入一条已有知识，使 content_hash 变化触发 mid
        client = FeishuClient(transport=_feishu_mock_transport())
        viking = RecordingViking()
        first = await sync_feishu_doc(
            db_session,
            doc.id,
            client=client,
            oss=FakeOss(),
            viking=viking.client,
            triggered_by="bind",
        )
        await db_session.commit()
        assert first.phase2.published == 1

        sync.content_hash = "stale_hash_for_mid_risk"
        await db_session.commit()

        sent: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            if "/blocks/" in request.url.path:
                return httpx.Response(
                    200, json={"code": 0, "data": {"items": faq_blocks_changed_answer()}}
                )
            if "/documents/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"document": {"title": "飞书 FAQ", "document_id": "feishu_tok_1"}},
                    },
                )
            if "/messages" in request.url.path:
                sent.append(json.loads(request.content))
                return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg_r1"}})
            raise AssertionError(str(request.url))

        client2 = FeishuClient(transport=httpx.MockTransport(handler))
        result = await sync_feishu_doc(
            db_session,
            doc.id,
            client=client2,
            oss=FakeOss(),
            viking=viking.client,
            triggered_by="poll",
        )
        await db_session.commit()

        assert result.phase2.pending_review == 1
        row = (
            await db_session.execute(
                select(Knowledge).where(
                    Knowledge.source_doc_id == doc.id,
                    Knowledge.status == Status.PENDING_REVIEW,
                )
            )
        ).scalar_one()
        assert row.risk_note

        tasks = (
            await db_session.execute(select(ReviewTask).where(ReviewTask.kid == row.kid))
        ).scalars().all()
        assert len(tasks) == 1
        assert tasks[0].feishu_card_id == "msg_r1"
        assert sent
