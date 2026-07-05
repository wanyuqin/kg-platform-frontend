"""飞书 OpenAPI 客户端（统一鉴权 + 限流 + 重试，feishu-sync §3）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.feishu.exceptions import FeishuError, FeishuPermissionError, map_feishu_error
from app.feishu.oauth import get_app_access_token
from app.feishu.rate_limiter import AsyncTokenBucket

logger = logging.getLogger(__name__)

_FEISHU_BASE = "https://open.feishu.cn"
_MAX_RETRIES = 3
_RETRY_BACKOFF = (1.0, 2.0, 4.0)


class FeishuClient:
    """飞书 OpenAPI 薄封装；token 懒加载，block/media 请求走独立限流桶。"""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        block_qps: float | None = None,
        media_qps: float | None = None,
    ):
        settings = get_settings()
        self._transport = transport
        self._token: str | None = None
        self._block_bucket = AsyncTokenBucket(block_qps or settings.feishu_block_qps)
        self._media_bucket = AsyncTokenBucket(media_qps or settings.feishu_media_qps)

    async def _ensure_token(self) -> str:
        if not self._token:
            self._token = await get_app_access_token(self._transport)
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        bucket: AsyncTokenBucket | None = None,
    ) -> dict[str, Any]:
        if bucket:
            await bucket.acquire()
        token = await self._ensure_token()
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            async with httpx.AsyncClient(
                base_url=_FEISHU_BASE, timeout=30.0, transport=self._transport
            ) as client:
                resp = await client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code >= 500:
                last_exc = FeishuError(
                    f"飞书服务异常 HTTP {resp.status_code}",
                    http_status=resp.status_code,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF[attempt])
                    continue
                raise last_exc
            body = resp.json()
            code = body.get("code", 0)
            if code != 0:
                raise map_feishu_error(code, body.get("msg") or "飞书 API 错误")
            return body.get("data") or {}

        raise last_exc or FeishuError("飞书请求失败")

    async def get_wiki_node(self, node_token: str) -> dict[str, Any]:
        """wiki URL → 实际文档 obj_type + obj_token。"""
        return await self._request(
            "GET",
            "/open-apis/wiki/v2/spaces/get_node",
            params={"token": node_token},
        )

    async def get_document_meta(self, document_token: str) -> dict[str, Any]:
        """权限预检 + 元信息（§4.4，仅拉 document 元数据）。"""
        return await self._request(
            "GET",
            f"/open-apis/docx/v1/documents/{document_token}",
        )

    async def get_document_blocks(self, document_token: str) -> list[dict[str, Any]]:
        """拉 Block 树（with_descendants=true）。"""
        data = await self._request(
            "GET",
            f"/open-apis/docx/v1/documents/{document_token}/blocks/{document_token}/children",
            params={"document_revision_id": -1, "page_size": 500, "with_descendants": "true"},
            bucket=self._block_bucket,
        )
        return data.get("items") or []

    async def download_media(self, media_token: str) -> tuple[bytes, str]:
        """下载文档内图片二进制（drive medias API，§6.2）。"""
        await self._media_bucket.acquire()
        token = await self._ensure_token()
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            async with httpx.AsyncClient(
                base_url=_FEISHU_BASE, timeout=60.0, transport=self._transport
            ) as client:
                resp = await client.get(
                    f"/open-apis/drive/v1/medias/{media_token}/download",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                )
            if resp.status_code >= 500:
                last_exc = FeishuError(
                    f"飞书 media 下载异常 HTTP {resp.status_code}",
                    http_status=resp.status_code,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF[attempt])
                    continue
                raise last_exc
            if resp.status_code != 200:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                raise map_feishu_error(body.get("code"), body.get("msg") or f"HTTP {resp.status_code}")
            content_type = resp.headers.get("content-type") or "application/octet-stream"
            return resp.content, content_type.split(";")[0].strip()
        raise last_exc or FeishuError("飞书 media 下载失败")

    async def check_document_permission(self, document_token: str) -> None:
        """成功则静默；失败抛 FeishuPermissionError。"""
        try:
            await self.get_document_meta(document_token)
        except FeishuPermissionError:
            raise
        except FeishuError as exc:
            raise map_feishu_error(exc.feishu_code, str(exc)) from exc

    async def send_message(
        self,
        receive_id: str,
        *,
        msg_type: str,
        content: str,
        receive_id_type: str = "open_id",
    ) -> str:
        """发送 IM 消息（审核卡片等）；返回 message_id。"""
        data = await self._request(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json={"receive_id": receive_id, "msg_type": msg_type, "content": content},
            bucket=self._media_bucket,
        )
        return data.get("message_id") or ""
