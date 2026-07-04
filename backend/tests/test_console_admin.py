"""控制台管理面：domain / 成员 / API Key / 审计查询（技术设计文档 七 7.2）。"""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert

from app.console import auth as console_auth
from app.gateway.auth import authenticate
from app.storage.pg.models import AuditLog, ConsoleUser, Domain, DomainMember
from app.storage.pg.session import get_session


@pytest.fixture
async def app_client(db_session):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def login_as(db_session, user_id: str, *, platform_admin=False) -> dict:
    """建用户 + session，返回请求 cookies。"""
    if await db_session.get(ConsoleUser, user_id) is None:
        db_session.add(ConsoleUser(user_id=user_id, name=user_id, is_platform_admin=platform_admin))
        await db_session.commit()
    sid = await console_auth.create_session(user_id)
    return {console_auth.COOKIE_NAME: sid}


@pytest.fixture
async def admin_cookies(db_session):
    return await login_as(db_session, "ou_platform", platform_admin=True)


@pytest.fixture
async def member_cookies(db_session):
    return await login_as(db_session, "ou_plain")


class TestDomains:
    async def test_create_and_list(self, app_client, admin_cookies):
        resp = await app_client.post(
            "/api/domains",
            json={"code": "free-order", "short_code": "fo", "name": "免单域"},
            cookies=admin_cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "free-order"

        listed = await app_client.get("/api/domains", cookies=admin_cookies)
        codes = {d["code"] for d in listed.json()["items"]}
        assert {"free-order", "common"} <= codes  # common 为迁移种子

    async def test_create_requires_platform_admin(self, app_client, member_cookies):
        resp = await app_client.post(
            "/api/domains",
            json={"code": "x-domain", "short_code": "xd", "name": "越权"},
            cookies=member_cookies,
        )
        assert resp.status_code == 403

    async def test_create_conflict_409(self, app_client, admin_cookies, db_session):
        db_session.add(Domain(code="free-order", short_code="fo", name="已存在", created_by="t"))
        await db_session.commit()
        resp = await app_client.post(
            "/api/domains",
            json={"code": "free-order", "short_code": "fx", "name": "重复"},
            cookies=admin_cookies,
        )
        assert resp.status_code == 409

    async def test_patch_updates_allowed_fields(self, app_client, admin_cookies, db_session):
        db_session.add(Domain(code="free-order", short_code="fo", name="旧名", created_by="t"))
        await db_session.commit()
        resp = await app_client.patch(
            "/api/domains/free-order",
            json={"name": "新名", "default_ttl_days": 180, "type_topk": {"faq": 8}},
            cookies=admin_cookies,
        )
        assert resp.status_code == 200
        row = await db_session.get(Domain, "free-order")
        assert (row.name, row.default_ttl_days, row.type_topk) == ("新名", 180, {"faq": 8})

    async def test_unauthenticated_401(self, app_client):
        assert (await app_client.get("/api/domains")).status_code == 401


class TestMembers:
    @pytest.fixture
    async def domain(self, db_session):
        db_session.add(Domain(code="free-order", short_code="fo", name="x", created_by="t"))
        await db_session.commit()

    async def test_domain_admin_manages_members(self, app_client, db_session, domain):
        cookies = await login_as(db_session, "ou_dadmin")
        db_session.add(DomainMember(domain_code="free-order", user_id="ou_dadmin", role="admin"))
        db_session.add(ConsoleUser(user_id="ou_new", name="新成员"))
        await db_session.commit()

        resp = await app_client.post(
            "/api/domains/free-order/members",
            json={"user_id": "ou_new", "role": "member"},
            cookies=cookies,
        )
        assert resp.status_code == 200

        resp = await app_client.request(
            "DELETE",
            "/api/domains/free-order/members",
            params={"user_id": "ou_new"},
            cookies=cookies,
        )
        assert resp.status_code == 200

    async def test_plain_member_cannot_manage(self, app_client, db_session, domain):
        cookies = await login_as(db_session, "ou_dmember")
        db_session.add(DomainMember(domain_code="free-order", user_id="ou_dmember", role="member"))
        db_session.add(ConsoleUser(user_id="ou_x", name="x"))
        await db_session.commit()
        resp = await app_client.post(
            "/api/domains/free-order/members",
            json={"user_id": "ou_x", "role": "member"},
            cookies=cookies,
        )
        assert resp.status_code == 403


class TestApiKeys:
    @pytest.fixture
    async def domain(self, db_session):
        db_session.add(Domain(code="free-order", short_code="fo", name="x", created_by="t"))
        await db_session.commit()

    async def test_issue_returns_plaintext_once(self, app_client, admin_cookies, domain):
        resp = await app_client.post(
            "/api/domains/free-order/keys",
            json={"agent_name": "cs-agent", "qps_limit": 20},
            cookies=admin_cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["plaintext"].startswith("kp_")
        assert body["qps_limit"] == 20
        assert body["domain_whitelist"] == ["free-order"]

    async def test_issued_key_authenticates_then_revoke_immediate(
        self, app_client, admin_cookies, domain, db_session
    ):
        resp = await app_client.post(
            "/api/domains/free-order/keys",
            json={"agent_name": "cs-agent"},
            cookies=admin_cookies,
        )
        plaintext, key_id = resp.json()["plaintext"], resp.json()["key_id"]
        ctx = await authenticate(db_session, plaintext)  # 签发即可用（灌了缓存）
        assert ctx.key_id == key_id

        revoke = await app_client.delete(f"/api/keys/{key_id}", cookies=admin_cookies)
        assert revoke.status_code == 200
        # 吊销即时生效：置 revoked + 主动 DEL 缓存（技术 10.1）
        from app import errors

        with pytest.raises(errors.ApiError):
            await authenticate(db_session, plaintext)

    async def test_issue_requires_platform_admin(self, app_client, member_cookies, domain):
        resp = await app_client.post(
            "/api/domains/free-order/keys",
            json={"agent_name": "x"},
            cookies=member_cookies,
        )
        assert resp.status_code == 403


class TestAuditLogs:
    @pytest.fixture
    async def audit_rows(self, db_session):
        rows = [
            dict(
                ts=datetime(2026, 7, 3, 10, 0, tzinfo=UTC),
                key_id="k1",
                action="search",
                query="发票",
                latency_ms=100,
            ),
            dict(
                ts=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
                key_id="k2",
                action="read",
                kid="faq-fo-0001",
                version=1,
                latency_ms=10,
            ),
        ]
        await db_session.execute(insert(AuditLog), rows)
        await db_session.commit()

    async def test_query_with_filters(self, app_client, admin_cookies, audit_rows):
        resp = await app_client.get(
            "/api/audit-logs",
            params={"action": "read", "key_id": "k2"},
            cookies=admin_cookies,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["kid"] == "faq-fo-0001"

    async def test_export_csv(self, app_client, admin_cookies, audit_rows):
        resp = await app_client.get("/api/audit-logs/export", cookies=admin_cookies)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.strip().splitlines()
        assert len(lines) == 3  # 表头 + 2 行
        assert lines[0].startswith("ts,")

    async def test_requires_platform_admin(self, app_client, member_cookies):
        assert (await app_client.get("/api/audit-logs", cookies=member_cookies)).status_code == 403
