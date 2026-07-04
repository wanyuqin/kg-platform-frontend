# scheduler — 定时任务

> **溯源**：技术设计文档 二（进程结构）、八（重试）、十一（分区维护）
> **代码入口**：`app/scheduler/`（main.py，独立进程入口）
> **关联 ADR**：ADR-0017
> **最后同步**：2026-07-04

## 定位

独立进程，APScheduler 驱动（P1 不引入 RocketMQ，ADR-0017）。**单实例部署**，因此所有任务必须幂等——重复执行不产生副作用，进程重启后从数据库状态自然恢复。

## P1 任务清单

| 任务 | 触发 | 行为 |
|-|-|-|
| OpenViking 写入失败重试 | 指数退避，初始 1min | 扫 `index_state='failed'` 的知识，重试 content/write，最多 10 次，仍失败告警平台管理员。期间知识 read 可用、search 不可见（见 [pipeline.md](pipeline.md) 发布事务） |
| 索引就绪轮询 | 周期轮询 | 扫 `index_state='indexing'`，查询 OpenViking L0/L1 就绪状态（形态待 PoC 确认），就绪则置 ready |
| 审计分区预建 | 每月 25 日 | 预建下月 audit_log RANGE 分区 |
| 审计分区清理 | 每日 | DROP 超过 180 天（`KG_AUDIT_RETENTION_DAYS`）的分区——删分区代替删行、无膨胀 |

另有草稿清理：draft 超 30 天未提交清理（ADR-0021，实现归属本进程）。

## P3 扩展

- 过期扫描：`expire_date` 命中的 published 知识置 expired 并发复审卡片（对外行为 P1 已由查询时兜底覆盖，ADR-0020）；
- 使用度报表：零命中清单、高频清单、缺失聚类周报（全部由 audit_log 派生查询实现，见 [audit.md](audit.md)）；
- OpenViking 孤儿文件对账清理。

## P2 演进

接入 RocketMQ 后，流水线异步投递（飞书同步、审核卡片超时）走消息队列，本进程保留纯定时类任务；表结构不变。这是实现层演进，不改变设计文档第八章的选型结论。
