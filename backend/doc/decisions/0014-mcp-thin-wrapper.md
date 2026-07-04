# ADR-0014：MCP Server 为 HTTP 薄封装、仅两工具（P2）

- **状态**：已采纳
- **日期**：2026-07-04
- **来源**：设计文档 6.2；第六章评审拍板

## 背景

Agent 生态需要 MCP 接入方式，但 MCP 层若承载独立逻辑会与 HTTP 契约漂移。

## 决策

MCP Server 是 HTTP API 的薄封装，仅暴露 search、read 两个工具，鉴权、限流、审计全部复用 HTTP 链路；read 返回 source_url。P2 交付，挂载于 gateway 模块。

## 理由

单一实现、双协议暴露，杜绝两套行为；工具面越小 Agent 误用越少。
