# audit — 审计日志

> **溯源**：技术设计文档 十一；设计文档 6.3、4.4.2
> **代码入口**：`app/audit/`（writer.py）
> **关联 ADR**：ADR-0010
> **最后同步**：2026-07-04

## 定位

审计留存 **Phase 1 首日开启**（设计第九章调整①），承担双职：合规留痕 + **治理数据主源**。平台不做 /v1/feedback 接口（ADR-0010），质量发现内部化——审计留存查询记录 + 版本快照 + 控制台人工打标，取代 Agent 侧反馈通道。

## 记录内容

字段见 [storage.md](storage.md) 的 audit_log 表：

- **search**：query、过滤条件（filter_type / filter_tag）、命中列表 hits `[{kid, version, score}]`、excluded_expired、latency_ms；
- **read**：kid、version、latency_ms。

kid + version 联合 knowledge_version 快照即可精确还原"**Agent 当时拿到了什么**"——这是答错溯源与打标的数据基础。

## 写入路径（writer.py）

请求处理完成后将记录投入**进程内有界队列**（maxsize 10000），后台协程每 1 秒或攒满 500 条批量 INSERT。队列满时丢弃并计数告警。

P1 接受进程崩溃丢失秒级尾部数据的风险——审计驱动的是治理统计而非计费；P2 若需强保证，写入口切 RocketMQ 即可，**表结构不变**。

## 分区与保留

按月 RANGE 分区；scheduler 每月 25 日预建下月分区、每日 DROP 超过 180 天的分区（见 [scheduler.md](scheduler.md)）。删分区代替删行，无膨胀。

## 查询与下游

- 控制台查询与 CSV 导出（平台管理员）：时间 / key / action 过滤；导出流式返回，单次上限 10 万行；
- P3 的零命中清单、高频清单、缺失聚类周报、打标队列**全部由本表派生查询实现，不另建埋点体系**；
- Redis 故障限流放行期间，请求仍全量落审计（可用性事件不产生审计盲区）。
