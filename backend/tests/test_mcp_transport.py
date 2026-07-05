"""MCP Streamable HTTP 集成测试（mcp-server.md §8.2）。"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.gateway.mcp.transport import get_session_manager
from app.main import create_app
from tests.test_gateway import drain_audit, hit

pytest_plugins = ["tests.test_gateway"]


@pytest.fixture
def mcp_storage(db_session, fake_viking, monkeypatch):
    """函数级 monkeypatch 注入 PG session / Viking 桩；pytest 自动还原，无模块级全局状态。"""

    class _SessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("app.gateway.mcp.server.get_viking", lambda: fake_viking)
    monkeypatch.setattr("app.gateway.mcp.server.get_session_factory", lambda: _SessionFactory())


def mcp_headers(key) -> dict[str, str]:
    return {"Authorization": f"Bearer {key[1]}"}


async def _run_mcp(fn):
    """在同一 asyncio task 内启动 session manager 并执行 MCP 客户端逻辑。"""
    app = create_app()
    manager = get_session_manager()
    assert manager is not None
    transport = ASGITransport(app=app)
    async with manager.run():
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            return await fn(http_client)


class TestMcpTransport:
    async def test_post_mcp_initialize(self, mcp_storage):
        async def _body(http_client):
            async with streamable_http_client("http://test/mcp", http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()

        await _run_mcp(_body)

    async def test_post_mcp_tools_list(self, mcp_storage):
        async def _body(http_client):
            async with streamable_http_client("http://test/mcp", http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert names == {"search", "read"}

        await _run_mcp(_body)

    async def test_post_mcp_tools_call_search(self, mcp_storage, seeded, fake_viking, api_key):
        async def _body(http_client):
            http_client.headers.update(mcp_headers(api_key))
            fake_viking.results = [hit(seeded["ok"], score=0.9)]
            drain_audit()

            async with streamable_http_client(
                "http://test/mcp", http_client=http_client, terminate_on_close=True
            ) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("search", {"query": "发票"})

            assert not result.isError, result
            payload = result.structuredContent or json.loads(result.content[0].text)
            assert len(payload["results"]) == 1
            assert payload["results"][0]["kid"] == seeded["ok"]

            records = drain_audit()
            assert len(records) == 1
            assert records[0]["action"] == "search"
            assert records[0]["key_id"] == api_key[0]

        await _run_mcp(_body)

    async def test_post_mcp_session_id_header(self, mcp_storage, api_key):
        async def _body(http_client):
            http_client.headers.update(mcp_headers(api_key))
            seen: list[str | None] = []

            async with streamable_http_client(
                "http://test/mcp", http_client=http_client, terminate_on_close=True
            ) as (read, write, get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    seen.append(get_session_id())
                    await session.list_tools()
                    seen.append(get_session_id())

            assert seen[0]
            assert seen[1] == seen[0]

        await _run_mcp(_body)
