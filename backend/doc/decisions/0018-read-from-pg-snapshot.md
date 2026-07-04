# ADR-0018：read 全文从 PG 版本快照读取，不回源 OpenViking

- **状态**：已采纳
- **日期**：2026-07-04（MVP 技术设计阶段）
- **来源**：技术设计文档 6.3

## 背景

`GET /v1/knowledge/{kid}` 的 content 有两个可选来源：OpenViking L2 原文，或 knowledge_version 最新快照。

## 决策

从 knowledge_version 最新快照读取，不回源 OpenViking。

## 理由

两处内容由同一发布事务写入、必然一致；PG 读延迟更低（P95 ≤ 200ms 目标的基础）；OpenViking 故障时 read 不受影响——search 降级 503、read 仍可用，故障半径减半。
