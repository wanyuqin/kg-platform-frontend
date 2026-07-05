"""飞书图片下载并转存 OSS（feishu-sync §6）。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from app.feishu.client import FeishuClient
from app.feishu.docx_to_markdown import PendingMedia
from app.storage.oss.client import OssClient

logger = logging.getLogger(__name__)

_IMAGE_PENDING_RE = re.compile(r"<IMAGE_PENDING:([^>]+)>")
_EXT_BY_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


class MediaUploader(Protocol):
    async def upload(self, key: str, data: bytes, content_type: str) -> str: ...


async def download_and_upload_one(
    client: FeishuClient,
    oss: MediaUploader,
    *,
    feishu_doc_token: str,
    media: PendingMedia,
) -> str | None:
    """下载单张图片并上传 OSS；失败返回 None（§7.4 单图失败不阻塞）。"""
    if not media.token:
        logger.warning("飞书图片 block=%s 缺少 media token，跳过", media.block_id)
        return None
    try:
        data, content_type = await client.download_media(media.token)
    except Exception:
        logger.exception("飞书图片下载失败 block=%s token=%s", media.block_id, media.token)
        return None
    ext = _EXT_BY_TYPE.get(content_type, "bin")
    key = f"feishu/{feishu_doc_token}/{media.block_id}.{ext}"
    try:
        return await oss.upload(key, data, content_type)
    except Exception:
        logger.exception("OSS 上传失败 key=%s", key)
        return None


async def download_and_upload_all(
    client: FeishuClient,
    oss: MediaUploader,
    *,
    feishu_doc_token: str,
    pending: list[PendingMedia],
) -> dict[str, str]:
    """并发下载上传；返回 block_id → 公网 URL 映射。"""
    if not pending:
        return {}

    async def _one(media: PendingMedia) -> tuple[str, str | None]:
        url = await download_and_upload_one(
            client, oss, feishu_doc_token=feishu_doc_token, media=media
        )
        return media.block_id, url

    pairs = await asyncio.gather(*(_one(m) for m in pending))
    return {bid: url for bid, url in pairs if url}


def apply_media_urls(markdown: str, urls: dict[str, str]) -> str:
    """将 `<IMAGE_PENDING:block_id>` 替换为 MinIO URL；失败留 `![](PENDING)`。"""
    if not urls and not _IMAGE_PENDING_RE.search(markdown):
        return markdown

    def _replace(match: re.Match[str]) -> str:
        block_id = match.group(1)
        url = urls.get(block_id)
        if url:
            return f"![image]({url})"
        return "![](PENDING)"

    return _IMAGE_PENDING_RE.sub(_replace, markdown)


async def resolve_media_in_markdown(
    markdown: str,
    pending: list[PendingMedia],
    *,
    client: FeishuClient,
    oss: OssClient | MediaUploader,
    feishu_doc_token: str,
) -> str:
    """主流程编排入口：下载全部 pending 图片并替换占位符。"""
    urls = await download_and_upload_all(
        client, oss, feishu_doc_token=feishu_doc_token, pending=pending
    )
    return apply_media_urls(markdown, urls)
