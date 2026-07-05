"""Streamable HTTP transport 适配（FastAPI 胶水，mcp-server.md §6）。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.config import get_settings
from app.gateway.mcp.context import McpRequestContext, reset_request_context, set_request_context
from app.gateway.mcp.server import server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger(__name__)

_session_manager: StreamableHTTPSessionManager | None = None


def get_session_manager() -> StreamableHTTPSessionManager | None:
    return _session_manager


class _McpAsgiApp:
    def __init__(self, manager: StreamableHTTPSessionManager):
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id", "")
        if not request_id:
            state = scope.get("state")
            if isinstance(state, dict):
                request_id = state.get("request_id", "")
        token = set_request_context(
            McpRequestContext(
                authorization=headers.get("authorization"),
                request_id=request_id,
            )
        )
        try:
            await self._manager.handle_request(scope, receive, send)
        finally:
            reset_request_context(token)


@asynccontextmanager
async def mcp_lifespan():
    """StreamableHTTPSessionManager 生命周期（须在 FastAPI lifespan 内嵌套）。"""
    manager = get_session_manager()
    if manager is None:
        yield
        return
    async with manager.run():
        yield


def mount_mcp(app: FastAPI) -> None:
    """挂载 POST/GET/DELETE /mcp（KG_MCP_ENABLED 为 true 时由 main 调用）。"""
    global _session_manager
    settings = get_settings()
    _session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=False,
        security_settings=None,
        session_idle_timeout=float(settings.mcp_session_timeout_s),
    )
    asgi_app = _McpAsgiApp(_session_manager)
    app.router.routes.append(
        Route("/mcp", endpoint=asgi_app, methods=["GET", "POST", "DELETE"])
    )
    logger.info("MCP Streamable HTTP mounted at /mcp")
