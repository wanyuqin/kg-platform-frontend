"""控制台登录与权限（技术设计文档 7.1）。

飞书 OAuth 的 code 交换封装为 exchange_code，HTTP 层测试直接 monkeypatch；
exchange_code 自身用 MockTransport 单测。
"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import errors
from app.console import auth as console_auth
from app.main import create_app
from app.storage.pg.models import ConsoleUser, Domain, DomainMember
from app.storage.pg.session import get_session


@pytest.fixture
async def client(db_session):
    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def seed_user(db_session, user_id="ou_alice", *, platform_admin=False) -> ConsoleUser:
    user = ConsoleUser(user_id=user_id, name="Alice", is_platform_admin=platform_admin)
    db_session.add(user)
    await db_session.commit()
    return user


class TestOAuthEndpoints:
    async def test_login_redirects_to_feishu_authorize(self, client):
        resp = await client.get("/api/auth/login", follow_redirects=False)
        assert resp.status_code == 307 or resp.status_code == 302
        location = resp.headers["location"]
        assert "open.feishu.cn" in location
        assert "redirect_uri=" in location and "state=" in location

    async def test_callback_creates_user_and_session_cookie(self, client, db_session, monkeypatch):
        async def fake_exchange(code):
            assert code == "code123"
            return console_auth.FeishuUser(open_id="ou_new", name="新用户")

        monkeypatch.setattr(console_auth, "exchange_code", fake_exchange)
        resp = await client.get("/api/auth/callback?code=code123", follow_redirects=False)
        assert resp.status_code in (302, 307)
        cookie = resp.headers.get("set-cookie", "")
        assert console_auth.COOKIE_NAME in cookie and "HttpOnly" in cookie

        user = await db_session.get(ConsoleUser, "ou_new")
        assert user is not None and user.name == "新用户"
        assert user.is_platform_admin is False  # 首次登录无任何角色（7.1）

    async def test_callback_existing_user_not_duplicated(self, client, db_session, monkeypatch):
        await seed_user(db_session, "ou_alice")

        async def fake_exchange(code):
            return console_auth.FeishuUser(open_id="ou_alice", name="Alice 改名")

        monkeypatch.setattr(console_auth, "exchange_code", fake_exchange)
        resp = await client.get("/api/auth/callback?code=x", follow_redirects=False)
        assert resp.status_code in (302, 307)
        user = await db_session.get(ConsoleUser, "ou_alice")
        assert user.name == "Alice 改名"  # 更新姓名而非重复建行


class TestExchangeCode:
    async def test_exchange_flow_with_mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "app_access_token": "aat"})
            assert request.headers.get("authorization") == "Bearer aat"
            return httpx.Response(
                200,
                json={"code": 0, "data": {"open_id": "ou_x", "name": "某人"}},
            )

        user = await console_auth.exchange_code("code-1", transport=httpx.MockTransport(handler))
        assert user.open_id == "ou_x" and user.name == "某人"


class TestSessionAndPermission:
    async def test_session_roundtrip(self, db_session):
        user = await seed_user(db_session)
        sid = await console_auth.create_session(user.user_id)
        loaded = await console_auth.load_user(db_session, sid)
        assert loaded.user_id == user.user_id

    async def test_load_user_401_without_or_bad_session(self, db_session):
        for sid in (None, "not-a-session"):
            with pytest.raises(errors.ApiError) as exc_info:
                await console_auth.load_user(db_session, sid)
            assert exc_info.value.http_status == 401

    async def test_platform_admin_check(self, db_session):
        admin = await seed_user(db_session, "ou_admin", platform_admin=True)
        member = await seed_user(db_session, "ou_member")
        console_auth.require_platform_admin(admin)  # 不抛
        with pytest.raises(errors.ApiError) as exc_info:
            console_auth.require_platform_admin(member)
        assert exc_info.value.http_status == 403

    async def test_domain_role_check(self, db_session):
        db_session.add(Domain(code="free-order", short_code="fo", name="x", created_by="t"))
        admin = await seed_user(db_session, "ou_dadmin")
        member = await seed_user(db_session, "ou_dmember")
        outsider = await seed_user(db_session, "ou_out")
        platform = await seed_user(db_session, "ou_plat", platform_admin=True)
        db_session.add(DomainMember(domain_code="free-order", user_id="ou_dadmin", role="admin"))
        db_session.add(DomainMember(domain_code="free-order", user_id="ou_dmember", role="member"))
        await db_session.commit()

        # domain 管理员与成员各自通过对应档位
        await console_auth.require_domain_role(db_session, admin, "free-order", {"admin"})
        await console_auth.require_domain_role(
            db_session, member, "free-order", {"admin", "member"}
        )
        # 成员不够 admin 档位；非成员一律 403；平台管理员旁路一切（7.1）
        with pytest.raises(errors.ApiError):
            await console_auth.require_domain_role(db_session, member, "free-order", {"admin"})
        with pytest.raises(errors.ApiError):
            await console_auth.require_domain_role(
                db_session, outsider, "free-order", {"admin", "member"}
            )
        await console_auth.require_domain_role(db_session, platform, "free-order", {"admin"})
