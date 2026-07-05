# mcp-server — MCP 接入（P2，方案稿）

> **溯源**：ADR-0014；roadmap Phase 2；gateway 模块（`app/gateway/`）
> **关联**：ADR-0001、ADR-0012、ADR-0013、ADR-0018
> **状态**：方案稿，待评审
> **最后同步**：2026-07-05

## 1. 目标 & 范围

把现有 `/v1/search` + `/v1/knowledge/{kid}` 暴露为 **MCP Server**，让支持 MCP 协议的 Agent（Claude Desktop / Cursor / 自研 Agent 等）能像调用本地工具一样调用知识检索，无需自己拼 HTTP、签 JWT、对接限流。

**核心约束**（ADR-0014）：

- **薄封装**：MCP Server 只把 HTTP 两个接口翻译成 MCP tools/call，**不引入任何独立业务逻辑**
- **单一实现**：鉴权、限流、审计、降级、参数校验**全部复用 HTTP 链路**——杜绝两套行为漂移
- **工具面最小**：仅 `search` / `read` 两个工具，read 返 `source_url`（P2 read 已实现）
- **挂载位置**：作为 `app/gateway/` 的子模块，跟 HTTP `/v1/*` 同进程、共享依赖注入

**明确不做**：

- 不做 stdio 模式（远程 Agent 才是目标用户，本地进程管道增加运维负担）
- 不做 tools/list 之外的 capabilities（不暴露 resources / prompts / sampling，避免 Agent 误用）
- 不做会话状态（无状态 + Mcp-Session-Id 透传，复杂状态交 HTTP session 管）
- 不做 MCP 协议升级（协议升级走 ADR 流程，不在本模块硬编码）

## 2. 技术选型

### 2.1 传输协议：Streamable HTTP（2025-03 PR #206）

| 候选 | 评估 | 决策 |
|---|---|---|
| **Streamable HTTP**（2025-03+ 默认） | 统一端点 `/mcp`、支持无状态模式、企业网友好（标准 HTTP）、与现有 FastAPI 0 摩擦 | ✅ **采用** |
| 旧 HTTP + SSE（2024 协议） | 双端点（`/sse` + `/messages`）、强制长连接、高并发下连接数爆炸、企业网穿透差 | ❌ 不采用 |
| stdio | 本地进程管道，需 Agent 与 Server 同机；远程 Agent 用不上 | ❌ 不采用 |
| WebSocket | 非 MCP 标准、客户端库支持差 | ❌ 不采用 |

**理由**：Streamable HTTP 是 2025-03 MCP 官方 PR #206 引入的新默认传输，**服务端可完全无状态运行**——跟我们 FastAPI 单进程 + 同请求内调 HTTP handler 的模型天然契合；旧 SSE 方案在高并发（>1000 连接）下 TCP 文件描述符爆炸，HTTP 网关/防火墙兼容差，**对我们的网关场景是反向选择**。

### 2.2 MCP Python SDK（**引入**）

- **官方 SDK**：`mcp`（PyPI，`pip install mcp`）—— MCP 协议官方维护的 Python 实现
- 关键 API：`Server("kg-gateway")` + `@server.list_tools()` + `@server.call_tool()`
- Streamable HTTP transport：`mcp.server.streamable_http` + `StreamableHTTPServerTransport`
- 与 FastAPI 集成：手动创建 transport + session_manager，挂到 FastAPI 路由上（FastAPI 不直接支持，需 ~30 行胶水）

### 2.3 不引入的（明确拒绝）

- ❌ **FastMCP / LangMCP 等第三方"上层封装"**：增加间接层，调试与协议升级成本高；我们只需要薄包装，过度封装反而是负担
- ❌ **自研 JSON-RPC 框架**：直接复用官方 SDK 即可，重复造轮子
- ❌ **stdio 传输**：远程 Agent 才是目标用户，本地进程管道增加运维负担
- ❌ **WebSocket 传输**：非 MCP 标准、客户端库支持差

## 3. 架构

```
┌──────────┐    Streamable HTTP    ┌──────────────────────────────────┐
│  Agent   │ ──────────────────→  │ FastAPI app                      │
│ (Claude  │   POST /mcp           │  ├─ /v1/search   (现有 HTTP)     │
│  Desktop │   JSON-RPC over HTTP  │  ├─ /v1/knowledge/{kid}          │
│  / 自研) │                       │  └─ /mcp   (MCP 薄封装,新增)     │
└──────────┘   Mcp-Session-Id: x   │         │                        │
                                    │         ▼                        │
                                    │   mcp.server.tool                │
                                    │     ├─ search  ─┐                │
                                    │     └─ read    ─┴─→ 调用现有     │
                                    │                  gateway 函数    │
                                    │                  （同一进程）    │
                                    └─────────────────┬────────────────┘
                                                      │
                                ┌─────────────────────┴─────────────┐
                                ▼                                   ▼
                          ┌──────────┐                       ┌──────────┐
                          │   PG     │                       │ OpenViking│
                          │  (限流/  │                       │ (检索/   │
                          │   审计)  │                       │  向量)   │
                          └──────────┘                       └──────────┘
```

**关键设计**：

1. **同进程**：MCP 端点 `/mcp` 与 HTTP `/v1/*` 共享 FastAPI app，共享 Depends 注入（get_session / get_viking / 鉴权 / 限流）
2. **函数级复用**：把现有 `search()` / `get_knowledge()` 的**核心逻辑**抽成内部函数 `do_search()` / `do_get_knowledge()`，HTTP endpoint 与 MCP tool handler 都调它——而不是 tool handler 内部再发 HTTP 请求自己（避免无谓的进程内 HTTP 开销）
3. **薄适配层**：MCP tool 只做三件事——① JSON-RPC ↔ Pydantic 互转、② error code 映射、③ request_id 关联；其他一律透传

## 4. 工具契约

### 4.1 `search`

**输入 schema**（MCP tool inputSchema）：

```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string", "minLength": 1, "maxLength": 512, "description": "检索关键词" },
    "type": { "type": "array", "items": { "type": "string", "enum": ["faq","sop","policy","product","case","term"] }, "description": "按知识类型过滤" },
    "tag": { "type": "array", "items": { "type": "string" }, "description": "按标签过滤（OR）" },
    "top_k": { "type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数" }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

**输出**（MCP tool result，content 类型 `text`）：

```json
{
  "results": [
    {
      "kid": "faq-fo-0001",
      "title": "免单申请条件",
      "summary": "...",
      "uri": "viking://resources/fo/faq-fo-0001",
      "score": 0.87,
      "domain": "fo",
      "type": "faq"
    }
  ],
  "excluded_expired": 0
}
```

**错误映射**：

| HTTP | MCP error code |
|---|---|
| 400 invalid_argument | `InvalidArgument` (JSON-RPC -32602) |
| 401 unauthorized | `Unauthorized` (custom code -32001) |
| 404 not_found | `NotFound` (custom code -32002) |
| 429 rate_limited | `RateLimited` (custom code -32003) |
| 503 upstream_unavailable | `UpstreamUnavailable` (custom code -32004) |

### 4.2 `read`

**输入 schema**：

```json
{
  "type": "object",
  "properties": {
    "kid": { "type": "string", "description": "知识条目 ID（含 .md 后缀亦可）" }
  },
  "required": ["kid"],
  "additionalProperties": false
}
```

**输出**：

```json
{
  "kid": "faq-fo-0001",
  "title": "免单申请条件",
  "domain": "fo",
  "type": "faq",
  "tags": ["高频"],
  "version": 3,
  "content": "## 申请条件\n...",
  "source": "feishu",
  "source_title": "免单知识库 V3",
  "source_url": "https://xxx.feishu.cn/docx/abc",
  "source_doc": { "id": 12, "name": "免单FAQ", "source": "feishu", "title": "..." },
  "effective_date": "2026-07-01",
  "expire_date": "2027-07-01",
  "updated_at": "2026-07-05T10:00:00Z"
}
```

## 5. 鉴权

**MCP 协议本身不定义鉴权**——靠 transport 层携带。我们沿用 HTTP 的 Bearer API Key，**在 Streamable HTTP 入口处把 Authorization header 提取出来**：

```python
# StreamableHTTP request 进入时
auth_header = request.headers.get("authorization")  # Bearer kp_xxx_xxx
ctx = await authenticate(session, auth_header.removeprefix("Bearer "))
# → ctx 存入 RequestContext.state，MCP tool handler 直接读
```

**关键点**：

- ✅ **不复用 HTTP Depends 注入**——MCP SDK 的 transport 与 FastAPI Depends 不同生命周期，手动提取更可控
- ✅ **API Key 与 HTTP 完全共用**——同一个 Key 走 HTTP 或 MCP 都行，平台侧无需新增凭据类型
- ✅ **QPS 限额共用**——`check_rate_limit(ctx.key_id, ctx.qps_limit)` 同一个 Redis bucket
- ⚠️ **Mcp-Session-Id 不作鉴权**——仅作会话关联，防止 Agent 误把会话当凭据

## 6. 路由 & 配置

### 6.1 路由

| Method | Path | 说明 |
|---|---|---|
| `POST` | `/mcp` | MCP JSON-RPC over Streamable HTTP（统一端点） |
| `GET`  | `/mcp` | SSE 流（按需升级，Server-initiated notification） |
| `DELETE` | `/mcp` | 显式终止 session（可选，Agent 通常 close 即走） |

**全路径 `POST /mcp`** 一次请求-响应；复杂通知才升级 SSE（我们场景用不到）。

### 6.2 配置

`app/config.py`（Pydantic Settings，前缀 `KG_`）：

```python
mcp_enabled: bool = True               # 默认开启（2026-07-05 用户拍板），Agent 凭 API Key 直接可走 MCP
mcp_session_timeout_s: int = 300       # StreamableHTTPServerTransport session 闲置超时
```

`.env.example`：

```bash
# MCP Server（P2，2026-07-05 用户拍板默认开启）
KG_MCP_ENABLED=true
KG_MCP_SESSION_TIMEOUT_S=300
```

**默认开启的理由**（用户拍板）：

- 内部平台场景，部署即暴露符合"少配置即能用"原则
- API Key 鉴权 + domain 白名单 + 限流 + 审计已足够兜住攻击面
- Agent 接入零摩擦，避免"想用但运维没开"的体验断点
- 关闭路径仍在：生产如需临时禁用，置 `KG_MCP_ENABLED=false` 即可

### 6.3 模块结构

```
backend/app/gateway/
├── router.py              # 现有 /v1/* (HTTP)
├── auth.py                # 现有 API Key 鉴权（共用）
├── core.py                # 新增：do_search() / do_get_knowledge() 内部函数
└── mcp/
    ├── __init__.py
    ├── server.py          # MCP Server 实例 + tools/list + tools/call handler
    ├── transport.py       # Streamable HTTP transport 适配（FastAPI 胶水）
    └── schema.py          # MCP inputSchema 定义（dict 形式，给 SDK list_tools 用）
```

`app/main.py`：

```python
from app.gateway.mcp.transport import mount_mcp  # 新增

def create_app():
    app = FastAPI(...)
    app.include_router(gateway_router)
    app.include_router(console_router)
    if get_settings().mcp_enabled:  # KG_MCP_ENABLED 默认 true
        mount_mcp(app)
    ...
```

## 7. 限流 / 审计复用

| 关注点 | 复用方式 |
|---|---|
| **限流** | tool handler 入口调 `check_rate_limit(ctx.key_id, ctx.qps_limit)`，与 HTTP 完全共用 Redis bucket |
| **审计** | tool handler 内部调 `_audit({key_id, action: "search"\|"read", ...})`，与 HTTP 共用 `app/audit/writer.py` 入队 |
| **request_id** | middleware 仍生成 `req_xxx`，写入 MCP JSON-RPC response 的 `_meta.request_id` 字段 |
| **降级** | tool handler 沿用 `errors.upstream_unavailable()` → JSON-RPC 错误映射，Agent 收到后自行退避 |

**审计字段一致性**：`key_id` / `query` / `hits` / `latency_ms` / `kid` / `version` 等与 HTTP 完全一致——控制台审计查询看到的 search/read 来源不区分 HTTP/MCP。

## 8. 测试

### 8.1 单元测试（`tests/test_mcp_server.py`，~8 个）

| 用例 | 覆盖点 |
|---|---|
| `test_search_tool_schema` | inputSchema 与 gateway SearchRequest 字段一致 |
| `test_read_tool_schema` | inputSchema 与 gateway read 字段一致 |
| `test_tools_list_returns_two` | 仅暴露 search / read |
| `test_search_call_delegates_to_core` | mock core.search，验证 tool handler 不发 HTTP 请求 |
| `test_read_call_delegates_to_core` | 同上 |
| `test_invalid_argument_maps_to_jsonrpc_error` | 空 query → -32602 |
| `test_unauthorized_when_missing_auth` | 无 Authorization header → -32001 |
| `test_rate_limited_maps_correctly` | mock check_rate_limit 返回 False → -32003 |

### 8.2 集成测试（`tests/test_mcp_transport.py`，~4 个）

| 用例 | 覆盖点 |
|---|---|
| `test_post_mcp_initialize` | JSON-RPC initialize 握手 |
| `test_post_mcp_tools_list` | tools/list 返回 search/read |
| `test_post_mcp_tools_call_search` | end-to-end：鉴权 → search → 审计 → 响应 |
| `test_post_mcp_session_id_header` | 第二次请求带 Mcp-Session-Id，复用 session |

**测试客户端**：用官方 SDK 的 `Client` + `streamablehttp_client` 走真 HTTP（test client），不走 mock——确保协议层 bug 能被捕获。

### 8.3 联调脚本（`scripts/dev_mcp_probe.py`）

一键验证 MCP 端点可用：

```bash
KG_MCP_ENABLED=true uv run python scripts/dev_mcp_probe.py
# 输出：initialize → tools/list → tools/call(search) → tools/call(read) 全链路结果
```

## 9. 工作量 & 落地步骤

### 9.1 工作量估算

| 模块 | 行数 | 工时 |
|---|---|---|
| `core.py`（内部函数抽取） | ~120 | 1h |
| `mcp/server.py`（tool handler） | ~150 | 1.5h |
| `mcp/transport.py`（FastAPI 胶水） | ~80 | 1h |
| `mcp/schema.py` | ~50 | 0.5h |
| `main.py` 挂载 | ~20 | 0.2h |
| 单元 + 集成测试 | ~250 | 2h |
| 联调脚本 + 文档 | ~80 | 0.5h |
| **合计** | **~750 行 + 12 测试** | **~1 工作日** |

### 9.2 落地步骤（TDD）

| # | 任务 | 验证 |
|---|---|---|
| 1 | `core.py` 抽函数 + 加测试（do_search / do_get_knowledge 行为与现有 endpoint 一致） | pytest 现有 search/read 测试全绿 |
| 2 | `mcp/schema.py` 定义 inputSchema | import 不报错 |
| 3 | `mcp/server.py` 实现 tool handler（先调通 list_tools + call_tool 单元测试） | pytest test_mcp_server.py 8 个全绿 |
| 4 | `mcp/transport.py` FastAPI 胶水 + `mount_mcp(app)` | curl POST /mcp → JSON-RPC 响应 |
| 5 | `main.py` + 配置 KG_MCP_ENABLED | `KG_MCP_ENABLED=true` 启动后 `/mcp` 200，默认无 |
| 6 | 集成测试（真客户端） | pytest test_mcp_transport.py 4 个全绿 |
| 7 | `scripts/dev_mcp_probe.py` 端到端验证 | 本地 dev 跑通 initialize→search→read |
| 8 | 文档 `mcp-server.md` 落地 + ADR-0014 链接 | review |

### 9.3 依赖

- 新增 PyPI 依赖：`mcp`（官方 SDK）
- 不引入其他

## 10. 风险与备选

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 官方 MCP SDK 仍在快速演进（每 2-4 周 minor） | 高 | 中 | 锁版本 `mcp>=1.0,<2.0`，升级走 ADR |
| Agent 端 SDK 兼容性（Claude Desktop / Cursor 版本差） | 中 | 中 | 集成测试用官方 SDK 真客户端，覆盖主流版本 |
| Streamable HTTP 在某些企业网穿透有问题 | 低 | 低 | 文档明确"如企业网不通，回退到 HTTP `/v1/*`"——HTTP 永远是兜底 |
| MCP 工具 schema 与 HTTP Pydantic 漂移 | 低 | 中 | schema.py 单文件定义，避免双写 |
| MCP session 状态泄露 | 低 | 中 | 无状态设计 + session_id 仅 transport 层用，不绑业务 |

**备选方案**（如未来 MCP 协议再次大改）：

- 完全回退到 HTTP `/v1/*`（Agent 端用通用 HTTP tool，损失 MCP 标准化优势）
- 加一层协议网关（MCP ↔ HTTP），与本方案互斥

## 11. 待评审决策点

| # | 决策点 | 我的建议 |
|---|---|---|
| 1 | 传输协议选 Streamable HTTP 而非旧 HTTP+SSE | ✅ Streamable HTTP（理由 §2.1） |
| 2 | 仅暴露 search / read 两个 tool，不暴露 resources/prompts | ✅ ADR-0014 已定 |
| 3 | `KG_MCP_ENABLED` 默认 true | ✅ 已决策（用户拍板）：部署即暴露，Agent 零摩擦接入，鉴权/白名单/限流/审计兜底（理由 §6.2） |
| 4 | MCP 与 HTTP 共进程、共函数（`core.py` 抽取）而非 MCP 内发 HTTP 请求 | ✅ 零额外开销（理由 §3） |
| 5 | API Key 复用，不引入 MCP 专属凭据 | ✅ 降低运维复杂度 |
| 6 | 测试用官方 SDK 真客户端，不用 mock transport | ✅ 协议层 bug 才能抓到 |
| 7 | MCP SDK 锁版本范围 | ✅ `>=1.0,<2.0`，升级走 ADR |
| 8 | 文档放 `backend/doc/modules/mcp-server.md`（跟 gateway / feishu-sync 同级） | ✅ 一致 |

## 12. 验收标准

P2 验收"运营只改飞书原文跑通"场景下，MCP Server 验收口径：

- [ ] 默认启动即挂载 `/mcp` 端点（`KG_MCP_ENABLED=true` 默认）
- [ ] `KG_MCP_ENABLED=false` 时 `/mcp` 不挂载，HTTP `/v1/*` 不受影响
- [ ] `tools/list` 仅返回 search / read
- [ ] 同一 API Key 走 HTTP `/v1/search` 与 MCP `tools/call(search)` 两条路径，PG 审计记录可查且 key_id 一致
- [ ] MCP search P95 ≤ HTTP search P95 × 1.2（薄封装开销上限）
- [ ] 集成测试 4 个 + 单元测试 8 个全绿，pytest 整体全绿
- [ ] `scripts/dev_mcp_probe.py` 本地一键跑通
- [ ] 文档 `backend/doc/modules/mcp-server.md` 与代码同步提交

## 13. 后续演进（P3+ 候选）

- [ ] `resources/list` + `resources/read`（暴露知识目录树给 Agent 探索）
- [ ] `prompts/list`（内置检索 query 模板，如"按 domain 聚合"）
- [ ] MCP sampling（Agent 反向调用平台 LLM 兜底）—— 暂不需要
- [ ] 多租户 MCP namespace（按 domain 隔离 tools）—— 与现有 domain 白名单一致即可，无需额外
- [ ] WebSocket transport（如果 Streamable HTTP 在某些客户端不被支持）—— 当前无明确需求