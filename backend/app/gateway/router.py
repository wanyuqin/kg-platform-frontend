"""Gateway 对外接口（技术设计文档 六）。

/v1/search 五步流程（6.2）：鉴权限流 → OpenViking 多前缀检索（top_k×3 余量）→
PG 回查过滤（published / 过期剔除计数 / type / tag）→ 排序截断 → 异步审计。
/v1/knowledge/{kid}（6.3）：校验链任一不满足统一 404；content 走 PG 快照（ADR-0018）。
"""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.gateway.auth import AuthContext, authenticate
from app.gateway.core import SearchRequest, do_get_knowledge, do_search
from app.storage.pg.session import get_session
from app.storage.redis.rate_limit import check_rate_limit
from app.storage.viking.client import VikingClient, get_viking

router = APIRouter(prefix="/v1", tags=["gateway"])


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise errors.unauthorized()
    return authorization.removeprefix("Bearer ").strip()


async def require_auth(authorization: str | None, session: AsyncSession) -> AuthContext:
    """鉴权（十）+ 限流（10.2）。

    在 endpoint 体内调用而非作为 Depends：FastAPI 依赖先于 body 校验执行，
    作依赖会让"坏参数 + 坏 key"返回 401，破坏"参数校验失败一律 400"的契约（6.1）。
    """
    token = _require_bearer(authorization)
    ctx = await authenticate(session, token)
    if not await check_rate_limit(ctx.key_id, ctx.qps_limit):
        raise errors.rate_limited(ctx.qps_limit)
    return ctx


@router.post("/search")
async def search(
    body: SearchRequest,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    t0 = time.perf_counter()
    ctx = await require_auth(authorization, session)
    return await do_search(session, viking, ctx, body, t0=t0)


@router.get("/knowledge/{kid}")
async def get_knowledge(
    kid: str,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
):
    t0 = time.perf_counter()
    ctx = await require_auth(authorization, session)
    return await do_get_knowledge(session, ctx, kid, t0=t0)
