"""MCP JSON-RPC 错误码映射（mcp-server.md §4）。"""

import logging

from pydantic import ValidationError

from app.errors import ApiError
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, ErrorData

logger = logging.getLogger(__name__)

MCP_UNAUTHORIZED = -32001
MCP_NOT_FOUND = -32002
MCP_RATE_LIMITED = -32003
MCP_UPSTREAM_UNAVAILABLE = -32004


def mcp_unauthorized(message: str = "invalid or missing API key") -> McpError:
    return McpError(ErrorData(code=MCP_UNAUTHORIZED, message=message))


def mcp_not_found(message: str = "knowledge not found") -> McpError:
    return McpError(ErrorData(code=MCP_NOT_FOUND, message=message))


def mcp_rate_limited(limit: int) -> McpError:
    return McpError(ErrorData(code=MCP_RATE_LIMITED, message=f"QPS limit exceeded (limit={limit})"))


def mcp_upstream_unavailable(message: str = "knowledge index temporarily unavailable") -> McpError:
    return McpError(ErrorData(code=MCP_UPSTREAM_UNAVAILABLE, message=message))


def mcp_invalid_argument(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def api_error_to_mcp(exc: ApiError) -> McpError:
    if exc.code == "invalid_argument":
        return mcp_invalid_argument(exc.message)
    if exc.code == "unauthorized":
        return mcp_unauthorized(exc.message)
    if exc.code == "not_found":
        return mcp_not_found(exc.message)
    if exc.code == "rate_limited":
        return mcp_rate_limited(exc.qps_limit or 0)
    if exc.code == "upstream_unavailable":
        return mcp_upstream_unavailable(exc.message)
    logger.warning("unmapped ApiError code=%s, using INTERNAL_ERROR", exc.code)
    return McpError(ErrorData(code=INTERNAL_ERROR, message=exc.message))


def validation_error_to_mcp(exc: ValidationError) -> McpError:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", ()))
    msg = first.get("msg", "invalid")
    message = f"{loc}: {msg}" if loc else str(msg)
    return mcp_invalid_argument(message)
