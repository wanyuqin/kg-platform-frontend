"""控制台业务面：知识 CRUD / 导入 / 模板（技术设计文档 七、八）。

契约：submit 校验拒收返回 200 + status="rejected" + validation[]（7.2 响应设计），
hash 重复返回 409；draft 仅本人可见（ADR-0021）。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.console import auth as console_auth
from app.storage.pg.models import ConsoleUser, Domain, DomainMember, Knowledge
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking

FIELDS_OK = {
    "标准问法": "企业版发票如何申请？",
    "相似问法": "- 怎么开发票？\n- 发票在哪里申请？",
    "标准答案": "登录管理后台申请。",
    "适用条件": "企业版付费客户",
}


def create_body(**overrides) -> dict:
    body = {
        "domain": "free-order",
        "type": "faq",
        "title": "企业版发票如何申请？",
        "fields": dict(FIELDS_OK),
        "tags": ["发票"],
        "owner": "ou_member",
        "effective_date": "2026-07-01",
        "save_mode": "submit",
        "new_doc_name": "默认测试文件",
    }
    body.update(overrides)
    return body


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
    """免单域 + 三个用户：域成员（ou_member）、域管理员（ou_dadmin）、外人（ou_out）。"""
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    for uid in ("ou_member", "ou_dadmin", "ou_out"):
        db_session.add(ConsoleUser(user_id=uid, name=uid))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_dadmin", role="admin"))
    await db_session.commit()


async def cookies_for(user_id: str) -> dict:
    return {console_auth.COOKIE_NAME: await console_auth.create_session(user_id)}


class TestCreateKnowledge:
    async def test_submit_publishes(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge", json=create_body(), cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "published"
        assert body["kid"] == "faq-fo-0001"
        assert body["validation"] == []

    async def test_submit_blocking_rejected_with_validation(self, app_client, seeded):
        bad = create_body(fields={"标准问法": "缺答案？", "相似问法": "- 甲\n- 乙"})
        resp = await app_client.post(
            "/api/knowledge", json=bad, cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected" and body["kid"] is None
        rules = {v["rule"] for v in body["validation"]}
        assert "missing_required_section" in rules

    async def test_submit_sensitive_rejected(self, app_client, seeded):
        bad = create_body(fields={**FIELDS_OK, "标准答案": "联系 13800138000 处理。"})
        resp = await app_client.post(
            "/api/knowledge", json=bad, cookies=await cookies_for("ou_member")
        )
        body = resp.json()
        assert body["status"] == "rejected"
        assert any(v["rule"] == "sensitive" for v in body["validation"])

    async def test_submit_warning_publishes_with_validation(self, app_client, seeded):
        one_similar = create_body(fields={**FIELDS_OK, "相似问法": "- 只有一条"})
        resp = await app_client.post(
            "/api/knowledge", json=one_similar, cookies=await cookies_for("ou_member")
        )
        body = resp.json()
        assert body["status"] == "published"  # warning 降级不阻塞（8.2）
        assert any(v["rule"] == "faq_similar_questions" for v in body["validation"])

    async def test_save_draft(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge",
            json=create_body(save_mode="draft"),
            cookies=await cookies_for("ou_member"),
        )
        assert resp.json()["status"] == "draft"

    async def test_duplicate_content_409(self, app_client, seeded):
        cookies = await cookies_for("ou_member")
        await app_client.post("/api/knowledge", json=create_body(), cookies=cookies)
        resp = await app_client.post(
            "/api/knowledge", json=create_body(title="换标题正文相同"), cookies=cookies
        )
        assert resp.status_code == 409

    async def test_outsider_403(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge", json=create_body(), cookies=await cookies_for("ou_out")
        )
        assert resp.status_code == 403


class TestListAndDetail:
    @pytest.fixture
    async def published_kid(self, app_client, seeded) -> str:
        resp = await app_client.post(
            "/api/knowledge", json=create_body(), cookies=await cookies_for("ou_member")
        )
        return resp.json()["kid"]

    async def test_list_filters(self, app_client, seeded, published_kid):
        resp = await app_client.get(
            "/api/knowledge",
            params={"domain": "free-order", "type": "faq", "status": "published"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [i["kid"] for i in items] == [published_kid]

    async def test_draft_visible_only_to_owner(self, app_client, seeded):
        member = await cookies_for("ou_member")
        await app_client.post("/api/knowledge", json=create_body(save_mode="draft"), cookies=member)
        mine = await app_client.get("/api/knowledge", params={"status": "draft"}, cookies=member)
        assert len(mine.json()["items"]) == 1
        # 域管理员也看不到他人草稿（ADR-0021：仅本人可见）
        others = await app_client.get(
            "/api/knowledge", params={"status": "draft"}, cookies=await cookies_for("ou_dadmin")
        )
        assert others.json()["items"] == []

    async def test_detail_with_content_and_versions(self, app_client, seeded, published_kid):
        resp = await app_client.get(
            f"/api/knowledge/{published_kid}", cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "## 标准答案" in body["content"]
        assert [v["version"] for v in body["versions"]] == [1]

    async def test_detail_outsider_403(self, app_client, seeded, published_kid):
        resp = await app_client.get(
            f"/api/knowledge/{published_kid}", cookies=await cookies_for("ou_out")
        )
        assert resp.status_code == 403


class TestUpdateAndLifecycle:
    @pytest.fixture
    async def published_kid(self, app_client, seeded) -> str:
        resp = await app_client.post(
            "/api/knowledge", json=create_body(), cookies=await cookies_for("ou_member")
        )
        return resp.json()["kid"]

    async def test_put_republishes_version_2(self, app_client, seeded, published_kid):
        resp = await app_client.put(
            f"/api/knowledge/{published_kid}",
            json=create_body(fields={**FIELDS_OK, "标准答案": "改为自助申请。"}),
            cookies=await cookies_for("ou_member"),  # owner 本人可改
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

    async def test_put_by_non_owner_member_403(self, app_client, seeded, published_kid, db_session):
        db_session.add(DomainMember(domain_code="free-order", user_id="ou_out", role="member"))
        await db_session.commit()
        resp = await app_client.put(
            f"/api/knowledge/{published_kid}",
            json=create_body(fields={**FIELDS_OK, "标准答案": "篡改"}),
            cookies=await cookies_for("ou_out"),  # 同域成员但非 owner
        )
        assert resp.status_code == 403

    async def test_patch_meta(self, app_client, seeded, published_kid, db_session):
        resp = await app_client.patch(
            f"/api/knowledge/{published_kid}/meta",
            json={"tags": ["发票", "企业版"], "expire_date": "2027-12-31"},
            cookies=await cookies_for("ou_dadmin"),  # domain 管理员可改
        )
        assert resp.status_code == 200
        row = await db_session.get(Knowledge, published_kid)
        assert row.tags == ["发票", "企业版"]
        assert row.expire_date.isoformat() == "2027-12-31"

    async def test_archive_deletes_viking_file(
        self, app_client, seeded, published_kid, viking, db_session
    ):
        resp = await app_client.post(
            f"/api/knowledge/{published_kid}/archive", cookies=await cookies_for("ou_dadmin")
        )
        assert resp.status_code == 200
        row = await db_session.get(Knowledge, published_kid)
        assert row.status == "archived"
        assert viking.deletes == [f"viking://resources/free-order/faq/{published_kid}.md"]

    async def test_renew_updates_expire_date(self, app_client, seeded, published_kid, db_session):
        resp = await app_client.post(
            f"/api/knowledge/{published_kid}/renew",
            json={"expire_date": "2028-01-01"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        row = await db_session.get(Knowledge, published_kid)
        assert row.expire_date.isoformat() == "2028-01-01"


MULTI_FAQ_MD = """# 企业版发票如何申请？

## 标准问法
企业版发票如何申请？

## 相似问法
- 怎么开发票？
- 发票在哪里申请？

## 标准答案
登录管理后台申请。

## 适用条件
企业版付费客户

# 坏条目缺必填段

## 标准问法
只有问法没有答案？

## 相似问法
- 甲
- 乙
"""


class TestImports:
    async def test_upload_preview_confirm_flow(self, app_client, seeded, db_session):
        cookies = await cookies_for("ou_member")
        upload = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq"},
            files={"file": ("faq.md", MULTI_FAQ_MD.encode(), "text/markdown")},
            cookies=cookies,
        )
        assert upload.status_code == 200
        batch = upload.json()
        assert len(batch["items"]) == 2
        good, bad = batch["items"]
        assert good["is_valid"] is True
        assert bad["is_valid"] is False  # blocking 条目预览页不可勾选（8.1）

        preview = await app_client.get(f"/api/imports/{batch['id']}", cookies=cookies)
        assert preview.status_code == 200

        confirm = await app_client.post(
            f"/api/imports/{batch['id']}/confirm",
            json={"item_ids": [good["id"], bad["id"]]},
            cookies=cookies,
        )
        assert confirm.status_code == 200
        results = confirm.json()["results"]
        assert results[0]["kid"] == "faq-fo-0001"
        assert results[1]["kid"] is None and results[1]["error"]  # blocking 条目拒绝入库

    async def test_upload_rejects_oversize(self, app_client, seeded):
        cookies = await cookies_for("ou_member")
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq"},
            files={"file": ("big.md", b"x" * (2 * 1024 * 1024 + 1), "text/markdown")},
            cookies=cookies,
        )
        assert resp.status_code == 400


class TestTemplates:
    async def test_download_template(self, app_client, seeded):
        resp = await app_client.get(
            "/api/templates/faq.md",
            cookies=await cookies_for("ou_out"),  # 登录即可
        )
        assert resp.status_code == 200
        assert "## 标准问法" in resp.text

    async def test_unknown_type_404(self, app_client, seeded):
        resp = await app_client.get("/api/templates/wiki.md", cookies=await cookies_for("ou_out"))
        assert resp.status_code == 404


class TestSourceDocAttachment:
    async def test_create_with_new_doc(self, app_client, seeded):
        body = create_body(new_doc_name="客服FAQ")
        resp = await app_client.post(
            "/api/knowledge", json=body, cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 200
        kid = resp.json()["kid"]
        detail = await app_client.get(f"/api/knowledge/{kid}", cookies=await cookies_for("ou_member"))
        assert detail.json()["source_doc"]["name"] == "客服FAQ"

    async def test_create_with_existing_doc(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc = SourceDoc(name="已有文件", domain_code="free-order", type="faq",
                        source="manual", created_by="ou_member")
        db_session.add(doc)
        await db_session.commit()
        body = create_body(source_doc_id=doc.id)
        resp = await app_client.post(
            "/api/knowledge", json=body, cookies=await cookies_for("ou_member")
        )
        assert resp.json()["kid"] is not None

    async def test_create_without_doc_rejected(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge", json=create_body(new_doc_name=None), cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 400  # 必须归属文件（spec §4.1）

    async def test_doc_type_mismatch_rejected(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc = SourceDoc(name="SOP文件", domain_code="free-order", type="sop",
                        source="manual", created_by="ou_member")
        db_session.add(doc)
        await db_session.commit()
        resp = await app_client.post(
            "/api/knowledge", json=create_body(source_doc_id=doc.id),
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 400
