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
