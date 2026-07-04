"""控制台路由共享依赖（router / admin / knowledge 子路由共用，避免循环导入）。"""

from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.console import auth
from app.storage.pg.models import ConsoleUser
from app.storage.pg.session import get_session


async def current_user(
    kg_session: Annotated[str | None, Cookie(alias=auth.COOKIE_NAME)] = None,
    session: AsyncSession = Depends(get_session),
) -> ConsoleUser:
    """登录态依赖：cookie → Redis session → console_user，任一缺失 401。"""
    return await auth.load_user(session, kg_session)
