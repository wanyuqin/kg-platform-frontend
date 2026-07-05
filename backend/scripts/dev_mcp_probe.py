#!/usr/bin/env python3
"""MCP 端点联调脚本：initialize → tools/list → search → read（mcp-server.md §8.3）。"""

import asyncio
import json
import os
import sys

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

BASE_URL = os.environ.get("KG_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("KG_API_KEY", "")


async def main() -> int:
    if not API_KEY:
        print("请设置 KG_API_KEY（Bearer API Key 明文）", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {API_KEY}"}
    url = f"{BASE_URL.rstrip('/')}/mcp"

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=30.0) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
            get_session_id,
        ):
            async with ClientSession(read, write) as session:
                print("→ initialize")
                await session.initialize()
                print(f"  session_id={get_session_id()}")

                print("→ tools/list")
                tools = await session.list_tools()
                print(f"  tools={[t.name for t in tools.tools]}")

                query = os.environ.get("KG_PROBE_QUERY", "发票")
                print(f"→ tools/call search query={query!r}")
                search_result = await session.call_tool("search", {"query": query})
                if search_result.isError:
                    print(f"  ERROR: {search_result.content}", file=sys.stderr)
                    return 1
                search_payload = search_result.structuredContent or json.loads(
                    search_result.content[0].text
                )
                print(json.dumps(search_payload, ensure_ascii=False, indent=2))

                results = search_payload.get("results") or []
                if not results:
                    print("  无命中，跳过 read")
                    return 0

                kid = results[0]["kid"]
                print(f"→ tools/call read kid={kid!r}")
                read_result = await session.call_tool("read", {"kid": kid})
                if read_result.isError:
                    print(f"  ERROR: {read_result.content}", file=sys.stderr)
                    return 1
                read_payload = read_result.structuredContent or json.loads(
                    read_result.content[0].text
                )
                preview = {
                    k: read_payload[k] for k in ("kid", "title", "source_url") if k in read_payload
                }
                print(json.dumps(preview, ensure_ascii=False, indent=2))

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
