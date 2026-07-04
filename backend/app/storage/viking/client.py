"""OpenViking 客户端（技术设计文档 九）。

使用面收敛为四类调用：write / delete / search / 索引状态。
具体 API 形态待 PoC 验证（9.1 清单）后落实，当前为接口壳。
"""

import httpx

from app.config import get_settings


def build_uri(domain: str, type_: str, kid: str) -> str:
    """viking://resources/{domain}/{type}/{kid}.md（MVP 两级平铺，5.2）"""
    return f"viking://resources/{domain}/{type_}/{kid}.md"


class VikingClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.viking_base_url
        self._api_key = settings.viking_api_key  # server.auth_mode=api_key（本地 compose 部署即此模式）
        self._timeout = settings.viking_timeout_ms / 1000

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    async def write(self, uri: str, content: str) -> None:
        """content/write 精确写入，同 URI 幂等覆盖。禁用 add_resource（设计 5.2）。"""
        raise NotImplementedError("待 PoC 确认 content/write 的 HTTP 形态（9.1）")

    async def delete(self, uri: str) -> None:
        """archived 下架时删除；expired 不删（检索排除由 PG 回查实现）。"""
        raise NotImplementedError("待 PoC 确认 delete 的 HTTP 形态（9.1）")

    async def search(self, query: str, dir_prefixes: list[str], limit: int) -> list[dict]:
        """返回 [{path, score, summary}]，由 path 反解 kid。"""
        raise NotImplementedError("待 PoC 确认多目录前缀检索形态（9.1）")

    async def is_indexed(self, uri: str) -> bool:
        """索引就绪判据，驱动 index_state: indexing → ready（8.4）。"""
        raise NotImplementedError("待 PoC 确认 L0/L1 就绪查询方式（9.1）")
