"""DocResolver 单元测试（feishu-sync §4）。"""

import httpx
import pytest

from app.feishu.client import FeishuClient
from app.feishu.doc_resolver import parse_feishu_url, resolve_with_permission
from app.feishu.exceptions import FeishuError


class TestParseFeishuUrl:
    def test_docx_url(self):
        doc = parse_feishu_url("https://bytedance.feishu.cn/docx/abc123XYZ")
        assert doc.obj_type == "docx"
        assert doc.document_token == "abc123XYZ"
        assert "/docx/abc123XYZ" in doc.doc_url

    def test_docx_url_with_underscore(self):
        doc = parse_feishu_url("https://feishu.cn/docx/feishu_tok_1")
        assert doc.document_token == "feishu_tok_1"

    def test_old_doc_url(self):
        doc = parse_feishu_url("https://feishu.cn/docs/oldDoc99")
        assert doc.obj_type == "doc"
        assert doc.document_token == "oldDoc99"

    def test_wiki_url(self):
        doc = parse_feishu_url("https://feishu.cn/wiki/wikiNode1")
        assert doc.obj_type == "wiki"
        assert doc.document_token == "wikiNode1"

    def test_invalid_url_raises(self):
        with pytest.raises(FeishuError, match="无法识别"):
            parse_feishu_url("https://example.com/not-feishu")


class TestResolveWithPermission:
    async def test_docx_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            if request.url.path.endswith("/documents/abc123"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"document": {"title": "退货流程 SOP", "document_id": "abc123"}},
                    },
                )
            raise AssertionError(f"unexpected {request.url}")

        client = FeishuClient(transport=httpx.MockTransport(handler))
        resolved, perm = await resolve_with_permission(
            client, "https://feishu.cn/docx/abc123"
        )
        assert perm.ok is True
        assert resolved.document_token == "abc123"
        assert resolved.title == "退货流程 SOP"

    async def test_permission_denied_app_not_in_kb(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 231002, "msg": "no permission"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        _, perm = await resolve_with_permission(client, "https://feishu.cn/docx/denied1")
        assert perm.ok is False
        assert perm.error_code == "feishu_app_not_in_kb"
        assert perm.action_guide is not None

    async def test_wiki_resolves_to_docx(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            if "get_node" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "node": {
                                "obj_type": 22,
                                "obj_token": "innerDoc",
                                "title": "Wiki 内文档",
                            }
                        },
                    },
                )
            if request.url.path.endswith("/documents/innerDoc"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"document": {"title": "Wiki 内文档", "document_id": "innerDoc"}},
                    },
                )
            raise AssertionError(f"unexpected {request.url}")

        client = FeishuClient(transport=httpx.MockTransport(handler))
        resolved, perm = await resolve_with_permission(
            client, "https://feishu.cn/wiki/wikiNode1"
        )
        assert perm.ok is True
        assert resolved.document_token == "innerDoc"
        assert resolved.title == "Wiki 内文档"
        assert any("get_node" in c for c in calls)

    async def test_wiki_resolves_string_obj_type_docx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            if "get_node" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "node": {
                                "obj_type": "docx",
                                "obj_token": "innerDoc2",
                                "title": "Wiki 内文档2",
                            }
                        },
                    },
                )
            if request.url.path.endswith("/documents/innerDoc2"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"document": {"title": "Wiki 内文档2", "document_id": "innerDoc2"}},
                    },
                )
            raise AssertionError(f"unexpected {request.url}")

        client = FeishuClient(transport=httpx.MockTransport(handler))
        resolved, perm = await resolve_with_permission(
            client, "https://feishu.cn/wiki/wikiNode2"
        )
        assert perm.ok is True
        assert resolved.document_token == "innerDoc2"
        assert resolved.obj_type == "docx"
