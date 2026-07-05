"""审核待办 REST API 测试。"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.console import auth as console_auth
from app.domain.state_machine import Status
from app.pipeline.publish import PublishInput, publish
from app.storage.pg.models import (
    ConsoleUser,
    Domain,
    DomainMember,
    Knowledge,
    ReviewTask,
    SourceDoc,
)
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_console_knowledge import FIELDS_OK, cookies_for


@pytest.fixture
def viking():
    return RecordingViking()


@pytest.fixture
async def app_client(db_session, viking):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: viking.client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def seeded(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    db_session.add(
        Domain(
            code="other",
            short_code="ot",
            name="其他域",
            created_by="t",
            reviewer_user_id="ou_reviewer",
        )
    )
    for uid, admin in (
        ("ou_member", False),
        ("ou_admin", True),
        ("ou_reviewer", False),
        ("ou_out", False),
    ):
        db_session.add(ConsoleUser(user_id=uid, name=uid))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_admin", role="admin"))
    db_session.add(DomainMember(domain_code="other", user_id="ou_reviewer", role="member"))
    await db_session.commit()


async def _seed_pending_review(
    db_session,
    viking,
    *,
    domain="free-order",
    reviewer_id=None,
    task_type="risk",
    doc_name="审核测试文件",
    title="待审核条目",
    fields=None,
):
    sections = fields or dict(FIELDS_OK)
    doc = SourceDoc(
        name=doc_name,
        domain_code=domain,
        type="faq",
        source="manual",
        created_by="ou_member",
    )
    db_session.add(doc)
    await db_session.flush()
    inp = PublishInput(
        domain_code=domain,
        type_="faq",
        title=title,
        sections=sections,
        tags=[],
        owner_user_id="ou_member",
        source_type="manual",
        source_ref="test",
        source_url=None,
        effective_date=date.today(),
        expire_date=None,
        actor_user_id="ou_member",
        source_doc_id=doc.id,
        doc_seq=1,
    )
    result = await publish(db_session, viking, inp, mode="review")
    task = ReviewTask(
        kid=result.kid,
        domain_code=domain,
        task_type=task_type,
        status="pending",
        risk_note="中风险变更",
        submitter_id="ou_member",
        reviewer_id=reviewer_id,
    )
    db_session.add(task)
    await db_session.commit()
    return task, result.kid


class TestReviewTasksApi:
    async def test_list_pending_by_domain(self, app_client, seeded, db_session, viking):
        task, _ = await _seed_pending_review(db_session, viking.client)
        resp = await app_client.get(
            "/api/review-tasks",
            params={"domain": "free-order", "task_type": "risk"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == task.id
        assert body["items"][0]["knowledge"]["title"] == "待审核条目"

    async def test_approve_by_domain_admin(self, app_client, seeded, db_session, viking):
        task, kid = await _seed_pending_review(db_session, viking.client)
        resp = await app_client.post(
            f"/api/review-tasks/{task.id}/approve",
            cookies=await cookies_for("ou_admin"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["kid"] == kid
        assert body["status"] == Status.PUBLISHED
        assert body["index_state"] == "indexing"
        assert len(viking.writes) == 1

        row = await db_session.get(Knowledge, kid)
        assert row.status == Status.PUBLISHED
        updated_task = await db_session.get(ReviewTask, task.id)
        assert updated_task.status == "approved"

    async def test_approve_by_reviewer(self, app_client, seeded, db_session, viking):
        task, kid = await _seed_pending_review(
            db_session, viking.client, domain="other", reviewer_id="ou_reviewer"
        )
        resp = await app_client.post(
            f"/api/review-tasks/{task.id}/approve",
            cookies=await cookies_for("ou_reviewer"),
        )
        assert resp.status_code == 200
        assert resp.json()["kid"] == kid

    async def test_member_cannot_approve(self, app_client, seeded, db_session, viking):
        task, _ = await _seed_pending_review(db_session, viking.client)
        resp = await app_client.post(
            f"/api/review-tasks/{task.id}/approve",
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 403

    async def test_reject_requires_reason(self, app_client, seeded, db_session, viking):
        task, kid = await _seed_pending_review(db_session, viking.client)
        resp = await app_client.post(
            f"/api/review-tasks/{task.id}/reject",
            json={"reason": "答案不准确"},
            cookies=await cookies_for("ou_admin"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == Status.DRAFT
        assert body["reject_reason"] == "答案不准确"

        row = await db_session.get(Knowledge, kid)
        assert row.status == Status.DRAFT
        updated_task = await db_session.get(ReviewTask, task.id)
        assert updated_task.status == "rejected"

    async def test_outsider_cannot_list(self, app_client, seeded, db_session, viking):
        await _seed_pending_review(db_session, viking.client)
        resp = await app_client.get(
            "/api/review-tasks",
            cookies=await cookies_for("ou_out"),
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_filter_task_type(self, app_client, seeded, db_session, viking):
        risk_task, _ = await _seed_pending_review(db_session, viking.client, task_type="risk")
        conflict_task, _ = await _seed_pending_review(
            db_session,
            viking.client,
            task_type="conflict",
            doc_name="审核测试文件B",
            title="另一条待审核",
            fields={
                "标准问法": "如何修改收货地址？",
                "相似问法": "- 改地址",
                "标准答案": "订单未发货前可改。",
                "适用条件": "未发货订单",
            },
        )

        resp = await app_client.get(
            "/api/review-tasks",
            params={"domain": "free-order", "task_type": "conflict"},
            cookies=await cookies_for("ou_admin"),
        )
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["items"]]
        assert conflict_task.id in ids
        assert risk_task.id not in ids
