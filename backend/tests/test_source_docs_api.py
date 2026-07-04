"""知识文件查询接口（spec §4.2、§6）。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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
def viking_rec():
    """共享的 RecordingViking：测试可断言 write/delete 调用记录。"""
    return RecordingViking()


@pytest.fixture
async def app_client(db_session, viking_rec):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: viking_rec.client
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


class TestConfirmUpdateBatch:
    async def test_full_update_cycle(self, app_client, seeded, db_session):
        from app.storage.pg.models import Knowledge as K

        doc_id = await import_doc(app_client, "文件丙")
        new_md = FAQ_MD_OK.replace("订单页申请。", "订单详情页申请。") + (
            "\n# 新问题？\n\n## 标准问法\n新问题？\n\n## 相似问法\n- 新1\n- 新2\n\n"
            "## 标准答案\n新答案。\n\n## 适用条件\n无\n"
        )
        preview = (
            await app_client.post(
                f"/api/source-docs/{doc_id}/update",
                data={"text": new_md},
                cookies=await cookies_for("ou_member"),
            )
        ).json()
        selectable = [i["id"] for i in preview["items"] if i["align_action"] != "unchanged"]
        resp = await app_client.post(
            f"/api/imports/{preview['id']}/confirm",
            json={"item_ids": selectable},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200

        rows = (
            (await db_session.execute(
                select(K).where(K.source_doc_id == doc_id).order_by(K.doc_seq)
            )).scalars().all()
        )
        by_title = {r.title: r for r in rows}
        assert by_title["如何退款？"].version == 2          # changed → 版本+1
        assert by_title["新问题？"].status == "published"     # new → 入库
        assert by_title["发货时间？"].status == "archived"    # disappeared → 下架
        # doc_seq 重写：新文本序（如何退款？=1，新问题？=2）
        assert by_title["如何退款？"].doc_seq == 1
        assert by_title["新问题？"].doc_seq == 2

    async def test_disappeared_unselected_survives(self, app_client, seeded, db_session):
        from app.storage.pg.models import Knowledge as K

        doc_id = await import_doc(app_client, "文件丁")
        preview = (
            await app_client.post(
                f"/api/source-docs/{doc_id}/update",
                data={"text": FAQ_MD_OK},  # 只剩第一条 →「发货时间？」标 disappeared
                cookies=await cookies_for("ou_member"),
            )
        ).json()
        keep = [i["id"] for i in preview["items"] if i["align_action"] == "new"]  # 全 unchanged/disappeared → 空
        resp = await app_client.post(
            f"/api/imports/{preview['id']}/confirm",
            json={"item_ids": keep},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        rows = (
            (await db_session.execute(
                select(K).where(K.source_doc_id == doc_id)
            )).scalars().all()
        )
        assert all(r.status == "published" for r in rows)  # 未勾选的 disappeared 不下架

    async def test_confirm_twice_idempotent(self, app_client, seeded, db_session, viking_rec):
        """重复 confirm（双击/重试）：第二次 200，disappeared 条目幂等成功不 500，
        viking.delete 恰好一次（不重复删已下架条目的索引）。"""
        from app.storage.pg.models import Knowledge as K

        doc_id = await import_doc(app_client, "文件戊")
        preview = (
            await app_client.post(
                f"/api/source-docs/{doc_id}/update",
                data={"text": FAQ_MD_OK},  # 只剩第一条 →「发货时间？」标 disappeared
                cookies=await cookies_for("ou_member"),
            )
        ).json()
        disappeared = [i["id"] for i in preview["items"] if i["align_action"] == "disappeared"]
        assert disappeared
        for round_ in (1, 2):  # 第二轮：条目已 archived，需幂等成功
            resp = await app_client.post(
                f"/api/imports/{preview['id']}/confirm",
                json={"item_ids": disappeared},
                cookies=await cookies_for("ou_member"),
            )
            assert resp.status_code == 200, f"第 {round_} 次 confirm 失败"
            for r in resp.json()["results"]:
                assert r["error"] is None and r["kid"]
        row = (
            (await db_session.execute(
                select(K).where(K.source_doc_id == doc_id, K.title == "发货时间？")
            )).scalars().one()
        )
        assert row.status == "archived"
        # 索引删除恰好一次：第二次 confirm 不重复 delete
        assert viking_rec.deletes.count(f"viking://resources/free-order/faq/{row.kid}.md") == 1


class TestSourceDocOps:
    async def test_renew_all(self, app_client, seeded):
        doc_id = await import_doc(app_client, "续期文件")
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/renew", json={"days": 30},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.json()["renewed"] == 2
        detail = (await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )).json()
        from datetime import date, timedelta

        want = (date.today() + timedelta(days=30)).isoformat()
        assert all(e["expire_date"] == want for e in detail["entries"])

    async def test_offline_archives_all(self, app_client, seeded):
        doc_id = await import_doc(app_client, "下架文件")
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/offline", cookies=await cookies_for("ou_member")
        )
        assert resp.json()["archived_entries"] == 2
        detail = (await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )).json()
        assert detail["status"] == "archived"
        assert all(e["status"] == "archived" for e in detail["entries"])

    async def test_rename_conflict(self, app_client, seeded):
        a = await import_doc(app_client, "甲名")
        await import_doc(app_client, "乙名")
        resp = await app_client.patch(
            f"/api/source-docs/{a}", json={"name": "乙名"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409

    async def test_renew_archived_doc_conflict(self, app_client, seeded):
        """归档后只读：续期 → 409。"""
        doc_id = await import_doc(app_client, "已归档续期")
        await app_client.post(
            f"/api/source-docs/{doc_id}/offline", cookies=await cookies_for("ou_member")
        )
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/renew", json={"days": 30},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409

    async def test_rename_archived_doc_conflict(self, app_client, seeded):
        """归档后只读：重命名 → 409。"""
        doc_id = await import_doc(app_client, "已归档改名")
        await app_client.post(
            f"/api/source-docs/{doc_id}/offline", cookies=await cookies_for("ou_member")
        )
        resp = await app_client.patch(
            f"/api/source-docs/{doc_id}", json={"name": "新名字"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409
