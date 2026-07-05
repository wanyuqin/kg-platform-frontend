"""MCP Server 单元测试（mcp-server.md §8.1）。"""

from unittest.mock import AsyncMock

import pytest
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS
from pydantic import ValidationError

from app import errors
from app.gateway.auth import AuthContext
from app.gateway.core import SearchRequest
from app.gateway.mcp.context import McpRequestContext, reset_request_context, set_request_context
from app.gateway.mcp.errors import (
    MCP_NOT_FOUND,
    MCP_RATE_LIMITED,
    MCP_UNAUTHORIZED,
    api_error_to_mcp,
    validation_error_to_mcp,
)
from app.gateway.mcp.schema import READ_TOOL_SCHEMA, SEARCH_TOOL_SCHEMA
from app.gateway.mcp.server import call_tool, list_tools
from mcp.shared.exceptions import McpError


def _auth_ctx() -> AuthContext:
    return AuthContext(
        key_id="abcd1234",
        agent_name="mcp-test",
        domain_whitelist=["free-order", "common"],
        qps_limit=100,
    )


@pytest.fixture
def auth_header():
    token = set_request_context(
        McpRequestContext(authorization="Bearer kp_test_secret", request_id="req_test123")
    )
    yield
    reset_request_context(token)


class TestToolSchemas:
    def test_search_tool_schema(self):
        props = SEARCH_TOOL_SCHEMA["properties"]
        json_schema = SearchRequest.model_json_schema()
        json_props = json_schema["properties"]
        assert set(props) >= {"query", "type", "tag", "top_k"}
        assert SEARCH_TOOL_SCHEMA["required"] == ["query"]
        assert props["query"]["maxLength"] == json_props["query"]["maxLength"]
        assert props["top_k"]["maximum"] == 20

    def test_read_tool_schema(self):
        assert READ_TOOL_SCHEMA["required"] == ["kid"]
        assert READ_TOOL_SCHEMA["additionalProperties"] is False


class TestListTools:
    async def test_tools_list_returns_two(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert names == {"search", "read"}


class TestCallTool:
    async def test_search_call_delegates_to_core(self, monkeypatch, auth_header):
        mock_search = AsyncMock(return_value={"results": [], "excluded_expired": 0})
        mock_auth = AsyncMock(return_value=_auth_ctx())
        monkeypatch.setattr("app.gateway.mcp.server.do_search", mock_search)
        monkeypatch.setattr("app.gateway.mcp.server._require_auth", mock_auth)

        result = await call_tool("search", {"query": "发票"})

        mock_search.assert_awaited_once()
        assert result["results"] == []
        mock_auth.assert_awaited_once()

    async def test_read_call_delegates_to_core(self, monkeypatch, auth_header):
        payload = {"kid": "faq-fo-0001", "title": "t", "content": "c"}
        mock_read = AsyncMock(return_value=payload)
        mock_auth = AsyncMock(return_value=_auth_ctx())
        monkeypatch.setattr("app.gateway.mcp.server.do_get_knowledge", mock_read)
        monkeypatch.setattr("app.gateway.mcp.server._require_auth", mock_auth)

        result = await call_tool("read", {"kid": "faq-fo-0001"})

        mock_read.assert_awaited_once()
        assert result["kid"] == "faq-fo-0001"

    async def test_invalid_argument_maps_to_jsonrpc_error(self, monkeypatch, auth_header):
        monkeypatch.setattr(
            "app.gateway.mcp.server._require_auth",
            AsyncMock(return_value=_auth_ctx()),
        )
        with pytest.raises(McpError) as exc_info:
            await call_tool("search", {"query": ""})
        assert exc_info.value.error.code == INVALID_PARAMS

    async def test_unauthorized_when_missing_auth(self):
        token = set_request_context(McpRequestContext(authorization=None, request_id="req_x"))
        try:
            with pytest.raises(McpError) as exc_info:
                await call_tool("search", {"query": "发票"})
            assert exc_info.value.error.code == MCP_UNAUTHORIZED
        finally:
            reset_request_context(token)

    async def test_rate_limited_maps_correctly(self, monkeypatch, auth_header):
        async def _rate_limited(_session, _authorization):
            raise api_error_to_mcp(errors.rate_limited(10))

        monkeypatch.setattr("app.gateway.mcp.server._require_auth", _rate_limited)
        with pytest.raises(McpError) as exc_info:
            await call_tool("search", {"query": "发票"})
        assert exc_info.value.error.code == MCP_RATE_LIMITED


class TestErrorMapping:
    def test_api_error_mapping(self):
        exc = api_error_to_mcp(errors.not_found())
        assert exc.error.code == MCP_NOT_FOUND

    def test_rate_limited_uses_qps_limit_field(self):
        exc = api_error_to_mcp(errors.rate_limited(42))
        assert exc.error.code == MCP_RATE_LIMITED
        assert "42" in exc.error.message

    def test_unknown_api_error_maps_to_internal(self):
        exc = api_error_to_mcp(errors.forbidden())
        assert exc.error.code == INTERNAL_ERROR

    def test_validation_error_mapping(self):
        try:
            SearchRequest.model_validate({"query": ""})
        except ValidationError as exc:
            mapped = validation_error_to_mcp(exc)
            assert mapped.error.code == INVALID_PARAMS
