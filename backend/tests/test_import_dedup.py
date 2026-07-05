"""导入两阶段去重集成测试（spec §8.2）。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.console import auth as console_auth
from app.pipeline.publish import PublishInput, publish
from app.storage.pg.models import ConsoleUser, Domain, DomainMember, ImportItem, Knowledge, SourceDoc
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_publish import FAQ_SECTIONS, make_input

FIELDS_OK = {
    "标准问法": "企业版发票如何申请？",
    "相似问法": "- 怎么开发票？\n- 发票在哪里申请？",
    "标准答案": "登录管理后台申请。",
    "适用条件": "企业版付费客户",
}


def faq_entry(question: str, answer: str = "登录管理后台申请。") -> str:
    return (
        f"# {question}\n\n"
        f"## 标准问法\n{question}\n\n"
        "## 相似问法\n- 怎么开发票？\n- 发票在哪里申请？\n\n"
        f"## 标准答案\n{answer}\n\n"
        "## 适用条件\n企业版付费客户\n"
    )


def two_entry_md(same_content: bool = True) -> str:
    q = "企业版发票如何申请？"
    e1 = faq_entry(q)
    e2 = faq_entry(q) if same_content else faq_entry("退款如何办理？", answer="联系客服处理。")
    return e1 + "\n\n" + e2


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
    for uid in ("ou_member", "ou_dadmin"):
        db_session.add(ConsoleUser(user_id=uid, name=uid))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    await db_session.commit()


async def cookies_for(user_id: str) -> dict:
    return {console_auth.COOKIE_NAME: await console_auth.create_session(user_id)}


async def upload_md(app_client, md: str, name: str = "去重测试文件"):
    return await app_client.post(
        "/api/imports",
        data={"domain": "free-order", "type": "faq", "doc_name": name},
        files={"file": ("f.md", md.encode(), "text/markdown")},
        cookies=await cookies_for("ou_member"),
    )


class TestPreviewBatchDedup:
    async def test_batch_duplicate_marks_invalid(self, app_client, seeded):
        resp = await upload_md(app_client, two_entry_md(same_content=True))
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["duplicate_in_batch"] == 1
        assert body["stats"]["requires_review"] is True
        dup_item = next(
            i for i in body["items"] if any(v["rule"] == "duplicate_in_batch" for v in i["validation"])
        )
        keep_item = next(i for i in body["items"] if i["is_valid"])
        assert dup_item["is_valid"] is False
        assert dup_item["validation"][0]["message"].startswith("与本文件第")
        assert dup_item["validation"][0]["meta"]["duplicate_item_id"] == keep_item["id"]

    async def test_same_as_library_not_blocking_in_preview(self, app_client, seeded, db_session, seed_doc):
        viking = RecordingViking()
        await publish(
            db_session,
            viking.client,
            make_input(source_doc_id=seed_doc.id, doc_seq=1),
        )
        resp = await upload_md(app_client, faq_entry("企业版发票如何申请？"), name="库内重复预览")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["is_valid"] is True
        assert not any(v["rule"] == "duplicate_in_batch" for v in item["validation"])


class TestConfirmDedup:
    async def test_confirm_skips_invalid_batch_duplicates(self, app_client, seeded):
        resp = await upload_md(app_client, two_entry_md(same_content=True))
        body = resp.json()
        dup_id = next(
            i["id"] for i in body["items"] if any(v["rule"] == "duplicate_in_batch" for v in i["validation"])
        )
        valid_ids = [i["id"] for i in body["items"] if i["is_valid"]]
        confirm = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": valid_ids + [dup_id]},
            cookies=await cookies_for("ou_member"),
        )
        results = {r["item_id"]: r for r in confirm.json()["results"]}
        assert results[dup_id]["error"] == "blocking 校验未通过"
        assert results[valid_ids[0]]["kid"] is not None

    async def test_confirm_with_batch_dup_goes_pending_review_no_viking(
        self, app_client, seeded, viking
    ):
        resp = await upload_md(app_client, two_entry_md(same_content=True))
        body = resp.json()
        valid_ids = [i["id"] for i in body["items"] if i["is_valid"]]
        confirm = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": valid_ids},
            cookies=await cookies_for("ou_member"),
        )
        cbody = confirm.json()
        assert cbody["requires_review"] is True
        assert cbody["summary"]["pending_review"] == 1
        assert cbody["summary"]["succeeded"] == 1
        assert cbody["results"][0]["status"] == "pending_review"
        assert viking.writes == []

        kid = cbody["results"][0]["kid"]
        row = (
            await app_client.get(f"/api/knowledge/{kid}", cookies=await cookies_for("ou_member"))
        ).json()
        assert row["status"] == "pending_review"

    async def test_confirm_without_batch_dup_publishes_and_writes_viking(
        self, app_client, seeded, viking
    ):
        resp = await upload_md(app_client, two_entry_md(same_content=False), name="无重复文件")
        body = resp.json()
        assert body["stats"]["duplicate_in_batch"] == 0
        valid_ids = [i["id"] for i in body["items"] if i["is_valid"]]
        confirm = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": valid_ids},
            cookies=await cookies_for("ou_member"),
        )
        cbody = confirm.json()
        assert cbody["requires_review"] is False
        assert cbody["summary"]["pending_review"] == 0
        assert cbody["results"][0]["status"] == "published"
        assert len(viking.writes) == len(valid_ids)

    async def test_confirm_global_duplicate_in_results(self, app_client, seeded, db_session, seed_doc):
        viking = RecordingViking()
        existing = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )
        resp = await upload_md(app_client, faq_entry("企业版发票如何申请？"), name="库内冲突确认")
        body = resp.json()
        item_id = body["items"][0]["id"]
        confirm = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": [item_id]},
            cookies=await cookies_for("ou_member"),
        )
        cbody = confirm.json()
        assert cbody["summary"]["failed_duplicate"] == 1
        assert existing.kid in cbody["results"][0]["error"]

    async def test_confirm_idempotent_on_result_kid(self, app_client, seeded, viking):
        resp = await upload_md(app_client, faq_entry("幂等测试问题"), name="幂等文件")
        body = resp.json()
        item_id = body["items"][0]["id"]
        first = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": [item_id]},
            cookies=await cookies_for("ou_member"),
        )
        kid = first.json()["results"][0]["kid"]
        second = await app_client.post(
            f"/api/imports/{body['id']}/confirm",
            json={"item_ids": [item_id]},
            cookies=await cookies_for("ou_member"),
        )
        assert second.json()["results"][0]["kid"] == kid
        assert second.json()["results"][0]["error"] is None
        assert len(viking.writes) == 1
