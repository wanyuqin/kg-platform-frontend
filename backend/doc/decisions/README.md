# ADR 索引

架构决策记录（Architecture Decision Record）。每个已拍板决策一个文件；**已拍板的决策不重新讨论**，推翻或修订须新增 ADR 并将旧条目标记 `superseded by ADR-NNNN`（流程见 [../README.md](../README.md)）。

编号 0001–0021 为 2026-07-04 初建目录时对飞书评审期决策的**补录**，日期为原拍板日期。

| 编号 | 标题 | 状态 | 日期 |
|-|-|-|-|
| [0001](0001-gateway-unified-entry.md) | Gateway 统一收口，Agent 不直连 OpenViking | 已采纳 | 2026-07-03 |
| [0002](0002-pg-single-source-of-truth.md) | PostgreSQL 为元数据与状态的唯一事实来源 | 已采纳 | 2026-07-03 |
| [0003](0003-domain-directory-isolation.md) | domain 白名单映射 viking:// 一级目录实现权限隔离 | 已采纳 | 2026-07-03 |
| [0004](0004-one-knowledge-one-file.md) | 知识按条存储：一条知识 = 一个文件 | 已采纳 | 2026-07-03 |
| [0005](0005-normalize-before-ingest.md) | 规范化在入库前完成，不依赖存储层 | 已采纳 | 2026-07-03 |
| [0006](0006-rules-first-llm-fallback.md) | LLM 规则优先、分级触发；Phase 1 零 LLM 上线 | 已采纳 | 2026-07-03 |
| [0007](0007-template-freeze.md) | 模板发布后冻结；六类类型与模板定稿 | 已采纳 | 2026-07-04 |
| [0008](0008-no-knowledge-relation.md) | 不做知识关联字段 | 已采纳 | 2026-07-03 |
| [0009](0009-full-resplit-realign.md) | 原文更新采用全量重拆 + 智能对齐 | 已采纳 | 2026-07-03 |
| [0010](0010-no-feedback-api.md) | 不做 /v1/feedback，质量发现内部化 | 已采纳 | 2026-07-04 |
| [0011](0011-topk-three-tier.md) | top_k 三层取值规则 | 已采纳 | 2026-07-04 |
| [0012](0012-platform-issued-api-key.md) | 平台自发 API Key：单 key、默认 10 QPS | 已采纳 | 2026-07-04 |
| [0013](0013-unauthorized-as-404.md) | 越权与不存在统一返回 404 | 已采纳 | 2026-07-04 |
| [0014](0014-mcp-thin-wrapper.md) | MCP Server 为 HTTP 薄封装、仅两工具（P2） | 已采纳 | 2026-07-04 |
| [0015](0015-feishu-single-doc-readonly.md) | 飞书来源：单文档注册、平台侧正文只读、事件+轮询双通道（P2） | 已采纳 | 2026-07-04 |
| [0016](0016-tags-free-input.md) | tags 自由输入、可为空（取代受控词表） | 已采纳 | 2026-07-04 |
| [0017](0017-no-rocketmq-in-p1.md) | P1 不引入 RocketMQ，后台任务用 APScheduler | 已采纳 | 2026-07-04 |
| [0018](0018-read-from-pg-snapshot.md) | read 全文从 PG 版本快照读取，不回源 OpenViking | 已采纳 | 2026-07-04 |
| [0019](0019-kid-and-uri-rules.md) | kid 三段式与 URI 两级平铺规则 | 已采纳 | 2026-07-04 |
| [0020](0020-expired-filter-at-query.md) | expired 语义 P1 用查询时兜底过滤实现 | 已采纳 | 2026-07-04 |
| [0021](0021-console-roles-draft-reject.md) | 控制台三级角色、草稿可见性、驳回回退 | 已采纳 | 2026-07-04 |
