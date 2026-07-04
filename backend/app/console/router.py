"""控制台接口装配（技术设计文档 七，P1 全量实现）。

登录两端点在本文件；管理面见 admin.py、业务面见 knowledge.py。
"""

import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import get_settings
from app.console import admin, auth, knowledge
from app.storage.pg.session import get_session

router = APIRouter(prefix="/api", tags=["console"])
router.include_router(admin.router)
router.include_router(knowledge.router)


@router.get("/auth/login")
async def login() -> RedirectResponse:
    return RedirectResponse(auth.authorize_url(state=secrets.token_urlsafe(16)))


@router.get("/auth/dev-login")
async def dev_login(
    user_id: str,
    name: str = "开发者",
    platform_admin: bool = False,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """本地联调登录后门：仅 KG_DEV_LOGIN_ENABLED=1 显式开启（默认 404），生产严禁开启。"""
    if not get_settings().dev_login_enabled:
        raise errors.not_found()
    user = await auth.upsert_user(session, auth.FeishuUser(open_id=user_id, name=name))
    if platform_admin and not user.is_platform_admin:
        user.is_platform_admin = True
        await session.commit()
    session_id = await auth.create_session(user.user_id)
    resp = RedirectResponse("/")
    resp.set_cookie(
        auth.COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="lax",
        max_age=get_settings().session_ttl_hours * 3600,
    )
    return resp


@router.get("/auth/callback")
async def callback(code: str, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    feishu = await auth.exchange_code(code)  # 经模块属性调用，测试可替换
    user = await auth.upsert_user(session, feishu)
    session_id = await auth.create_session(user.user_id)
    resp = RedirectResponse("/")
    resp.set_cookie(
        auth.COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="lax",
        max_age=get_settings().session_ttl_hours * 3600,
    )
    return resp
