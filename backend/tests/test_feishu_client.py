"""飞书 OAuth 与 Client 单元测试。"""

import httpx
import pytest

from app.feishu.client import FeishuClient
from app.feishu.exceptions import FeishuError, FeishuPermissionError
from app.feishu.oauth import get_app_access_token


class TestGetAppAccessToken:
    async def test_returns_tenant_token(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant_tok", "expire": 7200},
            )

        token = await get_app_access_token(transport=httpx.MockTransport(handler))
        assert token == "tenant_tok"

    async def test_falls_back_to_app_access_token(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "app_access_token": "app_tok"})

        token = await get_app_access_token(transport=httpx.MockTransport(handler))
        assert token == "app_tok"

    async def test_failure_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 10003, "msg": "invalid app"})

        with pytest.raises(FeishuError):
            await get_app_access_token(transport=httpx.MockTransport(handler))


class TestFeishuClient:
    async def test_get_document_blocks(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            assert "with_descendants" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"block_id": "p", "block_type": 1, "children": []},
                        ]
                    },
                },
            )

        client = FeishuClient(transport=httpx.MockTransport(handler))
        blocks = await client.get_document_blocks("doc1")
        assert len(blocks) == 1
        assert blocks[0]["block_id"] == "p"

    async def test_maps_permission_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 99991663, "msg": "not found"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        with pytest.raises(FeishuPermissionError) as exc_info:
            await client.get_document_meta("missing")
        assert exc_info.value.platform_code == "feishu_doc_not_found"

    async def test_retries_on_5xx_then_raises(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            calls["n"] += 1
            return httpx.Response(503, json={"code": 500, "msg": "busy"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        with pytest.raises(FeishuError, match="飞书服务异常"):
            await client.get_document_meta("doc1")
        assert calls["n"] == 3
