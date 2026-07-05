# gateway — 检索网关（/v1/*）

> **溯源**：技术设计文档 六、九（降级）、十（鉴权限流）；设计文档 六
> **代码入口**：`app/gateway/`
> **关联 ADR**：ADR-0001、ADR-0011、ADR-0012、ADR-0013、ADR-0014、ADR-0018
> **最后同步**：2026-07-05

## 职责边界

Agent 侧唯一入口，完全封装 OpenViking（ADR-0001）。P1 只有两个接口：`POST /v1/search` 与 `GET /v1/knowledge/{kid}`；MCP Server 为 P2，作为 HTTP 的薄封装挂载在本模块（ADR-0014）。

## Agent 鉴权与多域访问

- 每个 Agent 持**一把** API Key（`Authorization: Bearer kp_{key_id}_{secret}`），Key 绑定 `domain_whitelist`（domain code 数组）。
- 鉴权时自动并入 `common` 域；search 将白名单映射为 `viking://resources/{domain}/` 前缀集合，单次请求跨全部授权域检索。
- read 按 kid 所属 domain 校验是否在白名单内；越权统一 404（ADR-0013）。
- 上游只需配置单一 `KG_API_KEY`，无需按 domain 拆分多把 Key。

## 通用约定

- 所有接口 JSON / UTF-8；认证 `Authorization: Bearer kp_xxx`（Key 规格见 [storage.md](storage.md) 的 Redis 一节与技术 十）。
- 每个响应携带 `X-Request-Id`；429 附 `Retry-After` 头。
- 错误响应统一 envelope：

```json
{
  "error": {
    "code": "rate_limited",
    "message": "QPS limit exceeded (limit=10)",
    "request_id": "req_9f8a1c2b"
  }
}
```

| HTTP | code | 场景 |
|-|-|-|
| 400 | invalid_argument | 参数校验失败，message 指明字段与原因 |
| 401 | unauthorized | API Key 缺失、无效或已吊销 |
| 404 | not_found | kid 不存在 / domain 越权 / 非 published / 已过期——统一 404，不暴露存在性（ADR-0013） |
| 429 | rate_limited | 超出 QPS 限额 |
| 500 | internal | 服务内部错误 |
| 503 | upstream_unavailable | OpenViking 不可用（仅 search），消费方应退避重试 |

## POST /v1/search

| 参数 | 类型 | 必填 | 校验 |
|-|-|-|-|
| query | string | ✅ | 1～512 字符（trim 后） |
| type | string[] | 否 | 元素 ∈ 六类枚举，非法值 400 |
| tag | string[] | 否 | 精确匹配，多值 OR |
| top_k | int | 否 | 1～20 |

**top_k 三层取值**（ADR-0011）：

```text
生效值 = min( 入参 top_k ?? 类型级配置 ?? 平台默认 5, 硬上限 20 )

类型级配置：仅当请求 type 恰为单一类型时启用；
取 Key 白名单内各 domain 的 type_topk 中该类型配置的最大值；均未配置回落平台默认。
```

执行流程五步：

1. 鉴权 + 限流；
2. 白名单 domain（自动并入 common）映射为 `viking://resources/{domain}/` 前缀集合，调用 OpenViking 检索，取生效 top_k × 3 的候选（重排余量）；
3. 候选 kid 批量回查 PostgreSQL：仅保留 `status='published'`，其中 `expire_date` 已过者剔除并累计 `excluded_expired`（过期兜底语义见 [domain.md](domain.md)）；type / tag 过滤在此层执行；
4. 按得分排序截断 top_k，组装响应——title / domain / type 一律以 PG 为准，L0 摘要来自 OpenViking；
5. 异步写审计（含命中 kid + version 列表，见 [audit.md](audit.md)）。

响应 `results[]` 含 kid / title / summary / uri / score / domain / type，外加 `excluded_expired` 计数；结果为空时 results 为空数组、HTTP 仍 200。

## GET /v1/knowledge/{kid}

鉴权后依次校验：kid 存在 ∧ domain 在白名单内 ∧ status='published' ∧ 未过期——任一不满足统一返回 404（ADR-0013）。响应含溯源字段：

- `title`：知识条目标题（如 FAQ 标准问法）；
- `source`：来源类型 `manual`（平台自建）/ `upload`（上传 .md）/ `feishu`（飞书文档，P2）；
- `source_title`：原文/所属文章标题——自建取平台侧标题（粘贴 doc_name 或 frontmatter `title:`），飞书取同步的文档标题；
- `source_url`：外部原文链接（飞书/doc 等 HTTP URL），条目级优先、否则回退文件级；
- `source_doc`：平台内知识文件 `{id, name, source, title}`，`name` 为内部文件名，`title` 同 `source_title`。

**实现决策**（ADR-0018）：content 从 knowledge_version 最新快照读取，不回源 OpenViking L2。两处内容由同一发布事务写入、必然一致；PG 读延迟更低，且 OpenViking 故障时取全文不受影响。

## 降级策略

search 对 OpenViking 的调用设 800ms 超时 + 1 次重试，失败返回 503；read 不依赖 OpenViking，故障时仍可用。限流依赖 Redis，Redis 故障时放行并告警——检索是读操作，可用性优先于限流精度，故障期间请求仍全量落审计。

## 性能目标

50 并发、万级知识库：search P95 ≤ 800ms（含 OpenViking），read P95 ≤ 200ms。
