"""飞书图片转存单元测试（feishu-sync §6）。"""

import httpx

from app.feishu.client import FeishuClient
from app.feishu.docx_to_markdown import PendingMedia
from app.feishu.media import (
    apply_media_urls,
    download_and_upload_all,
    download_and_upload_one,
    resolve_media_in_markdown,
)


class FakeOss:
    def __init__(self):
        self.uploads: list[tuple[str, bytes, str]] = []

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        self.uploads.append((key, data, content_type))
        return f"http://oss.test/{key}"


class TestApplyMediaUrls:
    def test_replaces_pending_placeholder(self):
        md = "前文\n![image](<IMAGE_PENDING:img1>)\n后文"
        out = apply_media_urls(md, {"img1": "http://oss/a.png"})
        assert "http://oss/a.png" in out
        assert "IMAGE_PENDING" not in out

    def test_failed_image_becomes_pending(self):
        md = "![image](<IMAGE_PENDING:img1>)"
        out = apply_media_urls(md, {})
        assert "![](PENDING)" in out


class TestDownloadAndUpload:
    async def test_uploads_with_correct_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            assert "/medias/media_abc/download" in str(request.url)
            return httpx.Response(200, content=b"\x89PNG", headers={"Content-Type": "image/png"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        oss = FakeOss()
        url = await download_and_upload_one(
            client,
            oss,
            feishu_doc_token="docTok",
            media=PendingMedia(block_id="blk1", token="media_abc"),
        )
        assert url == "http://oss.test/feishu/docTok/blk1.png"
        assert oss.uploads[0][0] == "feishu/docTok/blk1.png"

    async def test_download_failure_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(404, json={"code": 99991663, "msg": "not found"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        oss = FakeOss()
        url = await download_and_upload_one(
            client,
            oss,
            feishu_doc_token="docTok",
            media=PendingMedia(block_id="blk1", token="bad"),
        )
        assert url is None
        assert oss.uploads == []

    async def test_download_and_upload_all(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, content=b"data", headers={"Content-Type": "image/jpeg"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        oss = FakeOss()
        pending = [
            PendingMedia(block_id="a", token="t1"),
            PendingMedia(block_id="b", token="t2"),
        ]
        urls = await download_and_upload_all(client, oss, feishu_doc_token="d1", pending=pending)
        assert len(urls) == 2
        assert len(oss.uploads) == 2

    async def test_resolve_media_in_markdown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        oss = FakeOss()
        md = "# t\n\n![image](<IMAGE_PENDING:img1>)"
        out = await resolve_media_in_markdown(
            md,
            [PendingMedia(block_id="img1", token="tok1")],
            client=client,
            oss=oss,
            feishu_doc_token="doc1",
        )
        assert "http://oss.test/feishu/doc1/img1.png" in out
