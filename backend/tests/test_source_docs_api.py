"""知识文件查询接口（spec §4.2、§6）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.storage.pg.models import ConsoleUser, Domain, DomainMember
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_console_knowledge import FAQ_MD_OK, cookies_for

FAQ_MD_TWO = FAQ_MD_OK + (
    "\n# 发货时间？\n\n## 标准问法\n发货时间？\n\n## 相似问法\n- 几天发货\n- 何时发货\n\n"
    "## 标准答案\n当天发货。\n\n## 适用条件\n现货\n"
)


@pytest.fixture
async def app_client(db_session):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: RecordingViking().client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def seeded(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    for uid in ("ou_member", "ou_out"):
        db_session.add(ConsoleUser(user_id=uid, name=uid))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    await db_session.commit()


async def import_doc(app_client, name: str, md: str = FAQ_MD_TWO) -> int:
    resp = await app_client.post(
        "/api/imports",
        data={"domain": "free-order", "type": "faq", "doc_name": name, "text": md},
        cookies=await cookies_for("ou_member"),
    )
    batch = resp.json()
    resp = await app_client.post(
        f"/api/imports/{batch['id']}/confirm",
        json={"item_ids": [i["id"] for i in batch["items"]]},
        cookies=await cookies_for("ou_member"),
    )
    return resp.json()["source_doc_id"]


class TestSourceDocList:
    async def test_list_with_counts(self, app_client, seeded):
        await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            "/api/source-docs", params={"domain": "free-order"},
            cookies=await cookies_for("ou_member"),
        )
        item = resp.json()["items"][0]
        assert item["name"] == "文件甲"
        assert item["entry_total"] == 2 and item["entry_published"] == 2

    async def test_outsider_sees_nothing(self, app_client, seeded):
        await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            "/api/source-docs", params={"domain": "free-order"},
            cookies=await cookies_for("ou_out"),
        )
        assert resp.json()["items"] == []


class TestSourceDocDetail:
    async def test_detail_entries_ordered(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )
        entries = resp.json()["entries"]
        assert [e["doc_seq"] for e in entries] == [1, 2]
        assert resp.json()["batches"][0]["stats"]["new"] == 2

    async def test_content_concatenates(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}/content", cookies=await cookies_for("ou_member")
        )
        md = resp.json()["markdown"]
        assert md.index("# 如何退款？") < md.index("# 发货时间？")

    async def test_outsider_404(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_out")
        )
        assert resp.status_code == 404


class TestSourceDocUpdate:
    async def test_update_preview_four_actions(self, app_client, seeded, db_session):
        doc_id = await import_doc(app_client, "文件甲")  # 两条：如何退款？/ 发货时间？
        new_md = FAQ_MD_OK.replace("订单页申请。", "订单详情页申请。") + (
            "\n# 新问题？\n\n## 标准问法\n新问题？\n\n## 相似问法\n- 新1\n- 新2\n\n"
            "## 标准答案\n新答案。\n\n## 适用条件\n无\n"
        )  # 变更 1 条 + 新增 1 条；「发货时间？」消失
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/update",
            data={"text": new_md},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        actions = {i["align_action"] for i in resp.json()["items"]}
        assert actions == {"changed", "new", "disappeared"}

    async def test_reload_preview_keeps_form_flag(self, app_client, seeded, db_session):
        """更新批次经 GET /imports/{id} 重载后，表单条目 disappeared 行仍带 is_form=True。"""
        from tests.test_console_knowledge import create_body

        doc_id = await import_doc(app_client, "文件丙")
        # 表单往该文件加一条（source_ref=form:ou_member）
        resp = await app_client.post(
            "/api/knowledge",
            json=create_body(source_doc_id=doc_id, new_doc_name=None),
            cookies=await cookies_for("ou_member"),
        )
        assert resp.json()["kid"]
        # 更新文本不含表单条目 → 该条 disappeared
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/update",
            data={"text": FAQ_MD_TWO},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        batch_id = resp.json()["id"]
        # 重载预览页（前端在线编辑流程按 batchId 重新拉取）
        resp = await app_client.get(
            f"/api/imports/{batch_id}", cookies=await cookies_for("ou_member")
        )
        disappeared = [i for i in resp.json()["items"] if i["align_action"] == "disappeared"]
        assert disappeared and all(i["is_form"] for i in disappeared)

    async def test_update_archived_doc_conflict(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc_id = await import_doc(app_client, "文件乙")
        (await db_session.get(SourceDoc, doc_id)).status = "archived"
        await db_session.commit()
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/update",
            data={"text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409
