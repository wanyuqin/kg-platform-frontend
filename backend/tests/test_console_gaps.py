"""线稿对照补缺的八项后端能力（设计 7.2 九页线框逐一对照后的差距）。"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text

from app.console import auth as console_auth
from app.storage.pg.models import (
    AuditLog,
    ConsoleUser,
    Domain,
    DomainMember,
)
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_console_knowledge import FIELDS_OK, create_body


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
    db_session.add(Domain(code="other", short_code="ot", name="他域", created_by="t"))
    for uid, admin in (("ou_member", False), ("ou_plat", True)):
        db_session.add(ConsoleUser(user_id=uid, name=uid, is_platform_admin=admin))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    await db_session.commit()


async def cookies_for(user_id: str) -> dict:
    return {console_auth.COOKIE_NAME: await console_auth.create_session(user_id)}


class TestMyDomains:
    async def test_member_sees_member_domains_plus_common(self, app_client, seeded):
        resp = await app_client.get("/api/domains/mine", cookies=await cookies_for("ou_member"))
        assert resp.status_code == 200
        codes = {d["code"] for d in resp.json()["items"]}
        assert codes == {"free-order", "common"}  # 仅显示有权限的 domain（线稿⑤）+ 通用域

    async def test_platform_admin_sees_all(self, app_client, seeded):
        resp = await app_client.get("/api/domains/mine", cookies=await cookies_for("ou_plat"))
        codes = {d["code"] for d in resp.json()["items"]}
        assert {"free-order", "other", "common"} <= codes


class TestKnowledgeStats:
    async def test_counts_by_type(self, app_client, seeded):
        cookies = await cookies_for("ou_member")
        await app_client.post("/api/knowledge", json=create_body(), cookies=cookies)
        await app_client.post(
            "/api/knowledge",
            json=create_body(
                title="免单资格？",
                fields={**FIELDS_OK, "标准问法": "免单资格？"},
                new_doc_name="免单资格文件",
            ),
            cookies=cookies,
        )
        resp = await app_client.get(
            "/api/knowledge/stats", params={"domain": "free-order"}, cookies=cookies
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["by_type"]["faq"] == 2


class TestListSearchQ:
    async def test_q_matches_title_or_kid(self, app_client, seeded):
        cookies = await cookies_for("ou_member")
        created = await app_client.post("/api/knowledge", json=create_body(), cookies=cookies)
        kid = created.json()["kid"]

        by_title = await app_client.get("/api/knowledge", params={"q": "发票"}, cookies=cookies)
        assert [i["kid"] for i in by_title.json()["items"]] == [kid]
        by_kid = await app_client.get("/api/knowledge", params={"q": kid}, cookies=cookies)
        assert [i["kid"] for i in by_kid.json()["items"]] == [kid]
        miss = await app_client.get("/api/knowledge", params={"q": "不存在"}, cookies=cookies)
        assert miss.json()["items"] == []


class TestHits30d:
    async def test_list_and_detail_include_hits(self, app_client, seeded, db_session):
        cookies = await cookies_for("ou_member")
        created = await app_client.post("/api/knowledge", json=create_body(), cookies=cookies)
        kid = created.json()["kid"]
        now = datetime.now(UTC)
        # 31 天前的行落在 6 月分区（迁移只建了 7/8 月），测试内补建（savepoint 回滚）
        await db_session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS audit_log_2026_06 PARTITION OF audit_log "
                "FOR VALUES FROM ('2026-06-01+08') TO ('2026-07-01+08')"
            )
        )
        await db_session.execute(
            insert(AuditLog),
            [
                # 近 30 天：search 命中一次 + read 一次 = 2
                dict(
                    ts=now - timedelta(days=1),
                    key_id="k1",
                    action="search",
                    query="发票",
                    hits=[{"kid": kid, "version": 1, "score": 0.9}],
                    latency_ms=10,
                ),
                dict(
                    ts=now - timedelta(days=2),
                    key_id="k1",
                    action="read",
                    kid=kid,
                    version=1,
                    latency_ms=5,
                ),
                # 超 30 天：不计（31 天前，落在补建的 6 月分区）
                dict(
                    ts=now - timedelta(days=31),
                    key_id="k1",
                    action="read",
                    kid=kid,
                    version=1,
                    latency_ms=5,
                ),
            ],
        )
        await db_session.commit()

        listed = await app_client.get("/api/knowledge", cookies=cookies)
        assert listed.json()["items"][0]["hits_30d"] == 2
        detail = await app_client.get(f"/api/knowledge/{kid}", cookies=cookies)
        assert detail.json()["hits_30d"] == 2
        assert detail.json()["source_ref"].startswith("form:")  # 线稿①溯源卡字段


class TestValidateEndpoint:
    async def test_returns_findings_without_persisting(self, app_client, seeded):
        cookies = await cookies_for("ou_member")
        resp = await app_client.post(
            "/api/knowledge/validate",
            json={"type": "faq", "fields": {"标准问法": "缺答案？"}},
            cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert any(v["rule"] == "missing_required_section" for v in body["validation"])
        # 纯校验不落库
        listed = await app_client.get("/api/knowledge", cookies=cookies)
        assert listed.json()["items"] == []

    async def test_ok_with_valid_fields(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge/validate",
            json={"type": "faq", "fields": dict(FIELDS_OK)},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.json()["ok"] is True


class TestImportItemsFields:
    async def test_items_include_parsed_fields(self, app_client, seeded):
        md = "# 发票如何申请？\n\n## 标准问法\n发票如何申请？\n\n## 相似问法\n- 甲\n- 乙\n\n## 标准答案\n后台申请。\n\n## 适用条件\n无限制\n"
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq"},
            files={"file": ("f.md", md.encode(), "text/markdown")},
            cookies=await cookies_for("ou_member"),
        )
        item = resp.json()["items"][0]
        assert item["fields"]["标准问法"] == "发票如何申请？"  # 线稿⑦：展开查看解析后字段


class TestDomainKeysList:
    async def test_masked_key_list(self, app_client, seeded, db_session):
        cookies = await cookies_for("ou_plat")
        issued = await app_client.post(
            "/api/domains/free-order/keys",
            json={"agent_name": "客服 Agent", "qps_limit": 10},
            cookies=cookies,
        )
        key_id = issued.json()["key_id"]
        resp = await app_client.get("/api/domains/free-order/keys", cookies=cookies)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["key_id"] == key_id
        assert items[0]["agent_name"] == "客服 Agent"
        assert items[0]["status"] == "active"
        assert "plaintext" not in items[0] and "key_hash" not in items[0]  # 只回掩码信息


class TestDomainsStats:
    async def test_domains_list_includes_stats(self, app_client, seeded, db_session):
        member = await cookies_for("ou_member")
        await app_client.post("/api/knowledge", json=create_body(), cookies=member)
        plat = await cookies_for("ou_plat")
        await app_client.post(
            "/api/domains/free-order/keys", json={"agent_name": "a1"}, cookies=plat
        )
        resp = await app_client.get("/api/domains", cookies=plat)
        by_code = {d["code"]: d for d in resp.json()["items"]}
        fo = by_code["free-order"]
        assert fo["stats"]["total"] == 1  # 线稿⑥：知识 N 条
        assert fo["stats"]["by_type"]["faq"] == 1  # 类型分布条
        assert fo["stats"]["agents"] == 1  # Agent N 个
