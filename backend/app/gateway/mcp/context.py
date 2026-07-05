"""MCP 请求上下文（transport 写入，tool handler 读取）。"""

from contextvars import ContextVar
from dataclasses import dataclass

_mcp_ctx: ContextVar["McpRequestContext | None"] = ContextVar("mcp_request_ctx", default=None)


@dataclass
class McpRequestContext:
    authorization: str | None
    request_id: str


def set_request_context(ctx: McpRequestContext) -> object:
    return _mcp_ctx.set(ctx)


def reset_request_context(token: object) -> None:
    _mcp_ctx.reset(token)


def get_request_context() -> McpRequestContext | None:
    return _mcp_ctx.get()
