"""api 进程入口：Gateway + 控制台同进程、按路由划分（技术设计文档 二）。"""

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from redis.exceptions import RedisError
from sqlalchemy import text

from app import errors
from app.audit import writer as audit_writer
from app.console.router import router as console_router
from app.gateway.router import router as gateway_router
from app.storage.pg.session import get_session_factory
from app.storage.redis.client import get_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """审计消费协程随进程起停（技术 十一）；shutdown 时清尾一次（尽力而为）。"""
    consumer = asyncio.create_task(audit_writer.run_consumer(get_session_factory()))
    yield
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer
    async with get_session_factory()() as session:
        await audit_writer.flush_once(session)


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge Gateway", version="0.1.0", docs_url="/docs", lifespan=_lifespan)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex[:12]}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    errors.install_handlers(app)
    app.include_router(gateway_router)
    app.include_router(console_router)

    @app.get("/healthz")
    async def healthz():
        """探活 PG 与 Redis（12.2）；依赖故障时报 degraded 但不 5xx，供网关判活。"""
        checks = {}
        try:
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "unavailable"
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except (RedisError, OSError):
            checks["redis"] = "unavailable"
        status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    return app


app = create_app()
