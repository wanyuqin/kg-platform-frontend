"""控制台登录与权限（技术设计文档 7.1）。

飞书网页 OAuth 换取身份后建 session（Redis，HttpOnly Cookie，12h）；
首次登录自动创建 console_user（无任何角色），平台管理员由运维在库中置位。
三级角色：平台管理员可操作一切；domain 管理员本域配置与审核；
domain 成员本域建知识与编辑自己 owner 的条目。
"""

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import get_settings
from app.storage.pg.models import ConsoleUser, DomainMember
from app.storage.redis.client import get_redis

COOKIE_NAME = "kg_session"
_FEISHU_BASE = "https://open.feishu.cn"


@dataclass
class FeishuUser:
    open_id: str
    name: str


def authorize_url(state: str) -> str:
    settings = get_settings()
    params = urlencode(
        {
            "app_id": settings.lark_app_id,
            "redirect_uri": "/api/auth/callback",  # 相对地址，飞书侧配置完整回调域名
            "state": state,
        }
    )
    return f"{_FEISHU_BASE}/open-apis/authen/v1/authorize?{params}"


async def exchange_code(code: str, transport: httpx.BaseTransport | None = None) -> FeishuUser:
    """OAuth code 换飞书用户身份（app_access_token → user access_token）。"""
    settings = get_settings()
    async with httpx.AsyncClient(
        base_url=_FEISHU_BASE, timeout=10.0, transport=transport
    ) as client:
        app_token_resp = await client.post(
            "/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": settings.lark_app_id, "app_secret": settings.lark_app_secret},
        )
        app_token = app_token_resp.json().get("app_access_token")
        if not app_token:
            raise errors.unauthorized("feishu app_access_token failed")
        user_resp = await client.post(
            "/open-apis/authen/v1/access_token",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"grant_type": "authorization_code", "code": code},
        )
        data = user_resp.json().get("data") or {}
        if not data.get("open_id"):
            raise errors.unauthorized("feishu code exchange failed")
        return FeishuUser(open_id=data["open_id"], name=data.get("name") or data["open_id"])


def _session_key(session_id: str) -> str:
    return f"cs:{session_id}"


async def create_session(user_id: str) -> str:
    session_id = secrets.token_urlsafe(32)
    ttl = get_settings().session_ttl_hours * 3600
    await get_redis().set(_session_key(session_id), user_id, ex=ttl)
    return session_id


async def destroy_session(session_id: str) -> None:
    await get_redis().delete(_session_key(session_id))


async def load_user(session: AsyncSession, session_id: str | None) -> ConsoleUser:
    """session 无效一律 401。"""
    if not session_id:
        raise errors.unauthorized("login required")
    user_id = await get_redis().get(_session_key(session_id))
    if not user_id:
        raise errors.unauthorized("session expired")
    user = await session.get(ConsoleUser, user_id)
    if user is None:
        raise errors.unauthorized("unknown user")
    return user


async def upsert_user(session: AsyncSession, feishu: FeishuUser) -> ConsoleUser:
    user = await session.get(ConsoleUser, feishu.open_id)
    if user is None:
        user = ConsoleUser(user_id=feishu.open_id, name=feishu.name)
        session.add(user)
    else:
        user.name = feishu.name
    await session.commit()
    return user


def require_platform_admin(user: ConsoleUser) -> None:
    if not user.is_platform_admin:
        raise errors.forbidden()


async def require_domain_role(
    session: AsyncSession, user: ConsoleUser, domain_code: str, roles: set[str]
) -> None:
    """不满足角色档位抛 403；平台管理员旁路一切（7.1）。"""
    if user.is_platform_admin:
        return
    member = (
        await session.execute(
            select(DomainMember).where(
                DomainMember.domain_code == domain_code,
                DomainMember.user_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None or member.role not in roles:
        raise errors.forbidden()
