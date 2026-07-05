"""MCP Server 实例与 tools/list、tools/call handler（ADR-0014 薄封装）。"""

import time
from contextlib import asynccontextmanager

import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel.server import request_ctx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.gateway.auth import AuthContext, authenticate
from app.gateway.core import SearchRequest, do_get_knowledge, do_search
from app.gateway.mcp.context import get_request_context
from app.gateway.mcp.errors import (
    api_error_to_mcp,
    mcp_invalid_argument,
    mcp_unauthorized,
    validation_error_to_mcp,
)
from app.gateway.mcp.schema import READ_TOOL_SCHEMA, SEARCH_TOOL_SCHEMA
from app.storage.pg.session import get_session_factory
from app.storage.redis.rate_limit import check_rate_limit
from app.storage.viking.client import get_viking

server = Server("kg-gateway")


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise mcp_unauthorized()
    return authorization.removeprefix("Bearer ").strip()


async def _require_auth(session: AsyncSession, authorization: str | None) -> AuthContext:
    token = _require_bearer(authorization)
    try:
        ctx = await authenticate(session, token)
    except errors.ApiError as exc:
        raise api_error_to_mcp(exc) from exc
    if not await check_rate_limit(ctx.key_id, ctx.qps_limit):
        raise api_error_to_mcp(errors.rate_limited(ctx.qps_limit))
    return ctx


def _current_request_id() -> str:
    req_ctx = get_request_context()
    if req_ctx and req_ctx.request_id:
        return req_ctx.request_id
    mcp_req = request_ctx.get(None)
    if mcp_req and mcp_req.request_id is not None:
        return str(mcp_req.request_id)
    return ""


@asynccontextmanager
async def _with_session():
    async with get_session_factory()() as session:
        yield session


def _read_kid(raw: str) -> str:
    return raw.removesuffix(".md")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search",
            description="检索已发布知识条目",
            inputSchema=SEARCH_TOOL_SCHEMA,
        ),
        types.Tool(
            name="read",
            description="读取知识条目全文",
            inputSchema=READ_TOOL_SCHEMA,
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> dict:
    req = get_request_context()
    authorization = req.authorization if req else None

    async with _with_session() as session:
        t0 = time.perf_counter()
        ctx = await _require_auth(session, authorization)
        viking = get_viking()
        args = arguments or {}

        try:
            if name == "search":
                body = SearchRequest.model_validate(args)
                result = await do_search(session, viking, ctx, body, t0=t0)
            elif name == "read":
                kid = _read_kid(str(args.get("kid", "")))
                if not kid:
                    raise validation_error_to_mcp(
                        ValidationError.from_exception_data(
                            "ReadRequest",
                            [{"type": "missing", "loc": ("kid",), "msg": "Field required"}],
                        )
                    )
                result = await do_get_knowledge(session, ctx, kid, t0=t0)
            else:
                raise mcp_invalid_argument(f"unknown tool: {name}")
        except ValidationError as exc:
            raise validation_error_to_mcp(exc) from exc
        except errors.ApiError as exc:
            raise api_error_to_mcp(exc) from exc

        request_id = _current_request_id()
        if request_id:
            result = {**result, "_meta": {"request_id": request_id}}
        return result
