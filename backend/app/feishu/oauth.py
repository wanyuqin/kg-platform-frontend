"""飞书应用凭证（复用 console/auth 同一套 app_access_token 逻辑）。"""

import httpx

from app.config import get_settings
from app.feishu.exceptions import FeishuError

_FEISHU_BASE = "https://open.feishu.cn"


async def get_app_access_token(transport: httpx.BaseTransport | None = None) -> str:
    """获取 tenant_access_token（机器人身份，用于文档同步）。"""
    settings = get_settings()
    async with httpx.AsyncClient(
        base_url=_FEISHU_BASE, timeout=10.0, transport=transport
    ) as client:
        resp = await client.post(
            "/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": settings.lark_app_id, "app_secret": settings.lark_app_secret},
        )
        data = resp.json()
        token = data.get("tenant_access_token") or data.get("app_access_token")
        if not token:
            raise FeishuError(
                "获取 app_access_token 失败",
                feishu_code=str(data.get("code")),
            )
        return token
