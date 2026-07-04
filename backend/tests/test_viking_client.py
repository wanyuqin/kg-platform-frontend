"""OpenViking 客户端单测（技术设计文档 九；HTTP 形态为 PoC 实测结论）。

用 httpx.MockTransport 模拟服务端，不依赖真实 OpenViking。
"""

import json

import httpx
import pytest

from app.storage.viking.client import VikingClient, VikingError, build_uri

URI = "viking://resources/free-order/faq/faq-fo-0001.md"


def ok(result) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "result": result, "error": None})


def err(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status, json={"status": "error", "result": None, "error": {"code": code, "message": code}}
    )


def make_client(handler) -> VikingClient:
    return VikingClient(transport=httpx.MockTransport(handler))


class TestBuildUri:
    def test_two_level_layout(self):
        assert build_uri("free-order", "faq", "faq-fo-0001") == URI


class TestWrite:
    async def test_replace_existing_file(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return ok({"uri": URI, "mode": "replace", "content_updated": True})

        await make_client(handler).write(URI, "# 内容")
        assert len(calls) == 1
        assert calls[0]["mode"] == "replace"
        assert calls[0]["uri"] == URI

    async def test_upsert_creates_when_missing(self):
        # PoC 结论：replace 对不存在文件返回 404，须降级 create（父目录自动创建）
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body["mode"])
            if body["mode"] == "replace":
                return err(404, "NOT_FOUND")
            return ok({"uri": URI, "mode": "create", "content_updated": True})

        await make_client(handler).write(URI, "# 内容")
        assert calls == ["replace", "create"]

    async def test_uses_api_key_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("x-api-key")
            return ok({})

        await make_client(handler).write(URI, "x")
        assert seen["key"]  # PoC 结论：鉴权走 x-api-key header（Bearer 为兼容别名不使用）

    async def test_server_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return err(500, "INTERNAL")

        with pytest.raises(VikingError):
            await make_client(handler).write(URI, "x")


class TestDelete:
    async def test_delete_success(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["uri_param"] = httpx.QueryParams(request.url.query).get("uri")
            return ok({"uri": URI, "estimated_deleted_count": 1})

        await make_client(handler).delete(URI)
        assert seen["method"] == "DELETE"
        assert seen["uri_param"] == URI

    async def test_delete_missing_is_idempotent(self):
        # 下架重试场景：文件已删除时再删不报错
        def handler(request: httpx.Request) -> httpx.Response:
            return err(404, "NOT_FOUND")

        await make_client(handler).delete(URI)  # 不抛异常即通过

    async def test_server_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return err(500, "INTERNAL")

        with pytest.raises(VikingError):
            await make_client(handler).delete(URI)


def find_result(resources: list[dict]) -> dict:
    """search/find 成功响应结构（PoC 实测）：result.resources[]，含目录派生物。"""
    return {"memories": [], "resources": resources}


FILE_HIT = {"uri": URI, "level": 2, "score": 0.66, "abstract": "企业版发票申请入口与时效。"}
DIR_HIT = {
    "uri": "viking://resources/free-order/faq/.abstract.md",
    "level": 0,
    "score": 0.62,
    "abstract": "目录摘要",
}


class TestSearch:
    async def test_maps_file_level_results_only(self):
        # PoC 结论：find 命中含 level=0/1 的 .abstract.md/.overview.md 目录派生物，须过滤 level=2
        def handler(request: httpx.Request) -> httpx.Response:
            return ok(find_result([DIR_HIT, FILE_HIT]))

        results = await make_client(handler).search("发票", ["viking://resources/free-order"], 5)
        assert results == [{"path": URI, "score": 0.66, "summary": "企业版发票申请入口与时效。"}]

    async def test_sends_target_uri_array_and_limit(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return ok(find_result([]))

        prefixes = ["viking://resources/free-order", "viking://resources/common"]
        await make_client(handler).search("发票", prefixes, 15)
        assert seen["target_uri"] == prefixes  # PoC 结论：数组单次多前缀可行
        assert seen["limit"] == 15
        assert seen["query"] == "发票"

    async def test_retries_once_on_timeout_then_succeeds(self):
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("timed out")
            return ok(find_result([FILE_HIT]))

        results = await make_client(handler).search("发票", ["viking://resources/common"], 5)
        assert attempts["n"] == 2
        assert len(results) == 1

    async def test_raises_viking_error_after_retry_exhausted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(VikingError):
            await make_client(handler).search("发票", ["viking://resources/common"], 5)

    async def test_server_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return err(500, "INTERNAL")

        with pytest.raises(VikingError):
            await make_client(handler).search("发票", ["viking://resources/common"], 5)


class TestIsIndexed:
    """PoC 结论：content/abstract 对文件 URI 恒返回目录级 fallback、fs/ls 的 abstract
    不及时回填、tasks 不追踪写入任务——唯一可靠的文件级就绪判据是 find probe 检索
    命中该 uri（level=2），这与"检索可见"的业务语义一致（技术 8.4）。"""

    async def test_file_hit_means_ready(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return ok(find_result([DIR_HIT, FILE_HIT]))

        assert await make_client(handler).is_indexed(URI, probe_query="发票如何申请") is True

    async def test_only_directory_hits_means_not_ready(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return ok(find_result([DIR_HIT]))

        assert await make_client(handler).is_indexed(URI, probe_query="发票如何申请") is False

    async def test_probes_parent_directory(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return ok(find_result([FILE_HIT]))

        await make_client(handler).is_indexed(URI, probe_query="发票如何申请")
        assert seen["target_uri"] == ["viking://resources/free-order/faq"]  # 父目录，缩小探测面

    async def test_error_means_not_ready(self):
        # 就绪轮询容错：查询失败按未就绪处理，下一轮重试（不抛异常打断 scheduler）
        def handler(request: httpx.Request) -> httpx.Response:
            return err(500, "INTERNAL")

        assert await make_client(handler).is_indexed(URI, probe_query="发票如何申请") is False
