"""开发登录后门：仅 KG_DEV_LOGIN=1 显式开启，生产默认关闭（404）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.console import auth as console_auth
from app.storage.pg.models import ConsoleUser
from app.storage.pg.session import get_session


@pytest.fixture
async def client(db_session):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class TestDevLogin:
    async def test_disabled_by_default_404(self, client):
        resp = await client.get("/api/auth/dev-login", params={"user_id": "ou_dev"})
        assert resp.status_code == 404

    async def test_enabled_creates_user_and_session(self, client, db_session, monkeypatch):
        monkeypatch.setattr(get_settings(), "dev_login_enabled", True)
        resp = await client.get(
            "/api/auth/dev-login",
            params={"user_id": "ou_dev", "name": "开发者", "platform_admin": "true"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert console_auth.COOKIE_NAME in resp.headers.get("set-cookie", "")
        user = await db_session.get(ConsoleUser, "ou_dev")
        assert user is not None and user.is_platform_admin is True

    async def test_enabled_session_grants_access(self, client, db_session, monkeypatch):
        monkeypatch.setattr(get_settings(), "dev_login_enabled", True)
        resp = await client.get(
            "/api/auth/dev-login",
            params={"user_id": "ou_dev2", "platform_admin": "true"},
            follow_redirects=False,
        )
        cookie_value = resp.cookies.get(console_auth.COOKIE_NAME)
        listed = await client.get(
            "/api/domains", cookies={console_auth.COOKIE_NAME: cookie_value}
        )
        assert listed.status_code == 200
