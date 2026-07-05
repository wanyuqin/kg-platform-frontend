"""S3 兼容对象存储客户端（MinIO，feishu-sync §6）。"""

from __future__ import annotations

import asyncio
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.config import Settings, get_settings


@lru_cache
def _build_client() -> BaseClient:
    settings = get_settings()
    endpoint = settings.oss_endpoint.rstrip("/")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.oss_access_key,
        aws_secret_access_key=settings.oss_secret_key,
        region_name=settings.oss_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class OssClient:
    """MinIO 上传 + 公网 URL（本地 dev 用 path-style 公开前缀）。"""

    def __init__(self, settings: Settings | None = None, *, client: BaseClient | None = None):
        self._settings = settings or get_settings()
        self._client = client or _build_client()

    def _public_url(self, key: str) -> str:
        base = self._settings.oss_public_base_url.rstrip("/")
        return f"{base}/{key.lstrip('/')}"

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """上传对象并返回对外 URL。"""
        bucket = self._settings.oss_bucket

        def _put() -> None:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_put)
        return self._public_url(key)


def get_oss() -> OssClient:
    return OssClient()
