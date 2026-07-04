"""OpenViking 客户端（技术设计文档 九）。

使用面收敛为四类调用：write / delete / search / 索引就绪。
HTTP 形态为 2026-07-04 PoC 实测结论（scripts/poc_viking.py，服务 v0.4.7），
端点语义详见 doc/modules/storage.md 的 OpenViking 一节。
"""

import httpx

from app.config import get_settings


class VikingError(Exception):
    """OpenViking 调用失败（非 404 类语义错误）。"""


# 写入/删除/就绪查询不在 800ms 检索预算内（发布事务与 scheduler 场景），单独放宽
_WRITE_TIMEOUT_S = 10.0


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        raise VikingError(f"HTTP {r.status_code}: {r.text[:200]}")


def build_uri(domain: str, type_: str, kid: str) -> str:
    """viking://resources/{domain}/{type}/{kid}.md（MVP 两级平铺，5.2）"""
    return f"viking://resources/{domain}/{type_}/{kid}.md"


_default_client: "VikingClient | None" = None


def get_viking() -> "VikingClient":
    """FastAPI 依赖（进程级单例）；测试经 dependency_overrides 注入桩。"""
    global _default_client
    if _default_client is None:
        _default_client = VikingClient()
    return _default_client


class VikingClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        settings = get_settings()
        self._base_url = settings.viking_base_url
        self._api_key = (
            settings.viking_api_key
        )  # server.auth_mode=api_key（本地 compose 部署即此模式）
        self._timeout = settings.viking_timeout_ms / 1000
        self._transport = transport  # 测试注入 MockTransport 用

    def _http(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout or self._timeout,
            headers={"x-api-key": self._api_key},  # PoC 实测鉴权 header
            transport=self._transport,
        )

    async def write(self, uri: str, content: str) -> None:
        """content/write 精确写入，同 URI 幂等覆盖。禁用 add_resource（设计 5.2）。

        PoC 结论：mode=replace 覆盖已有文件（不存在 404）、mode=create 新建
        （父目录自动创建，已存在 409），upsert 由客户端组合：replace → 404 → create。
        wait=False 异步生成 L0/L1，就绪由 is_indexed 轮询驱动（8.4）。
        """
        payload = {"uri": uri, "content": content, "wait": False, "mode": "replace"}
        async with self._http(timeout=_WRITE_TIMEOUT_S) as http:
            r = await http.post("/api/v1/content/write", json=payload)
            if r.status_code == 404:
                r = await http.post("/api/v1/content/write", json={**payload, "mode": "create"})
            _raise_for_status(r)

    async def delete(self, uri: str) -> None:
        """archived 下架时删除；expired 不删（检索排除由 PG 回查实现）。

        404 视为成功：下架重试须幂等（8.4 重试收敛）。
        """
        async with self._http(timeout=_WRITE_TIMEOUT_S) as http:
            r = await http.delete("/api/v1/fs", params={"uri": uri, "recursive": False})
            if r.status_code == 404:
                return
            _raise_for_status(r)

    async def search(self, query: str, dir_prefixes: list[str], limit: int) -> list[dict]:
        """返回 [{path, score, summary}]，由 path 反解 kid。

        PoC 结论：POST search/find 的 target_uri 原生支持数组（单次多前缀，无需
        N 次合并）；命中含 level=0/1 的目录派生物（.abstract.md / .overview.md），
        知识文件恒为 level=2，必须过滤。800ms 超时 + 1 次重试，失败抛 VikingError
        （Gateway 映射 503 upstream_unavailable，技术 九）。
        """
        payload = {"query": query, "target_uri": dir_prefixes, "limit": limit}
        last_exc: Exception | None = None
        for _ in range(2):  # 1 次原始调用 + 1 次重试
            try:
                async with self._http() as http:
                    r = await http.post("/api/v1/search/find", json=payload)
                _raise_for_status(r)
                resources = (r.json().get("result") or {}).get("resources") or []
                return [
                    {
                        "path": item["uri"],
                        "score": item.get("score"),
                        "summary": item.get("abstract") or "",
                    }
                    for item in resources
                    if item.get("level") == 2
                ]
            except (httpx.TimeoutException, httpx.TransportError, VikingError) as exc:
                last_exc = exc
        raise VikingError(f"search failed after retry: {last_exc}")

    async def is_indexed(self, uri: str, probe_query: str) -> bool:
        """索引就绪判据，驱动 index_state: indexing → ready（8.4）。

        PoC 结论：content/abstract 对文件 URI 恒返回目录级 fallback、fs/ls 的
        abstract 不及时回填、tasks 不追踪写入任务——唯一可靠的文件级判据是
        find probe 检索（限定父目录）命中该 uri 的 level=2 条目，与"检索可见"
        的业务语义一致。probe_query 由调用方传知识标题。查询失败按未就绪处理，
        由 scheduler 下一轮重试。
        """
        parent = uri.rsplit("/", 1)[0]
        payload = {"query": probe_query, "target_uri": [parent], "limit": 10}
        try:
            async with self._http(timeout=_WRITE_TIMEOUT_S) as http:
                r = await http.post("/api/v1/search/find", json=payload)
            if r.status_code != 200:
                return False
            resources = (r.json().get("result") or {}).get("resources") or []
            return any(item.get("uri") == uri and item.get("level") == 2 for item in resources)
        except httpx.HTTPError:
            return False
