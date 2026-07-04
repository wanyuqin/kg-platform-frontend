# domain — 领域模型：kid 规则与知识状态机

> **溯源**：技术设计文档 四、五；设计文档 4.2、5.1
> **代码入口**：`app/domain/`（kid.py / state_machine.py）
> **关联 ADR**：ADR-0019、ADR-0020
> **最后同步**：2026-07-04

## kid 生成规则（kid.py，技术 5.1）

格式 `{type_code}-{domain_short}-{seq}`（ADR-0019），示例：faq-fo-0086、pol-fo-0003、term-0002。

- type_code 固定映射：faq→faq、sop→sop、policy→pol、product→prd、case→case、term→term；
- domain_short 取 `domain.short_code`（注册时必填，2～4 位小写字母）；**common 域 short_code 为空串，kid 退化为两段**（如 term-0002）；
- seq 按 (domain, type) 独立自增，4 位零填充，超 9999 自然扩位；取号在发布事务内 `UPDATE kid_sequence SET next_seq = next_seq + 1 ... RETURNING`，事务回滚产生的空洞可接受（kid 不要求连续）。

kid 一经分配**跨版本稳定、终生不复用**。

## 存储 URI 规则（技术 5.2）

```text
viking://resources/{domain}/{type}/{kid}.md
```

三条硬规则（ADR-0019）：① URI 只含 kid、不含版本号，内容更新为同 URI 覆盖写，**URI 永不变**（Agent 会引用与缓存它）；② 按 domain/type 两级平铺，tag 不进 URI（tags 已改自由输入，ADR-0016）；单目录超 500 条的自动分桶由后台任务处理，viking:// 是逻辑路径、无迁移成本；③ 文档标题不参与 URI。

## 知识状态机（state_machine.py，技术 四）

status 五态：draft / pending_review / published / expired / archived。完整转移表如下，P1 只实现标注 P1 的行，**但状态机代码一次写全**并对非法迁移抛错，避免后续阶段改核心逻辑：

| 当前状态 | 事件 | 目标状态 | 副作用 | 阶段 |
|-|-|-|-|-|
| （新建） | 表单保存草稿 | draft | 写 knowledge 行（不写快照，快照只在发布时落） | P1 |
| draft | 提交且校验通过（P1 全部视为低风险） | published | 生成 kid（新建时）、写 version 快照、写 OpenViking | P1 |
| draft | 提交且命中中/高风险 | pending_review | 建审核任务、发飞书卡片 | P2 |
| pending_review | 审核通过 | published | 同发布副作用 | P2 |
| pending_review | 驳回（必填理由，ADR-0021） | draft | 通知提交人，修改后重提 | P2 |
| published | 内容更新提交 | published | version+1、新快照、同 URI 覆盖写 | P1 |
| published | 过期扫描命中 expire_date | expired | 状态落库 + 复审卡片；OpenViking 正文保留 | P3 |
| expired | owner 续期 | published | 更新 expire_date | P3 |
| published / expired | owner 下架 | archived | OpenViking 删除文件；**终态** | P1 |

实现要点：

- 状态迁移收敛到唯一入口 `state_machine.transition(kid, event)`，内部对照上表校验合法性，非法迁移抛业务异常；并发用 `SELECT ... FOR UPDATE` 行锁保护。
- archived 是终态、不支持恢复；需要重新启用时按新知识入库（新 kid）。
- index_state 与 status **正交**：OpenViking 写入失败不回退 published，由重试收敛（见 [pipeline.md](pipeline.md) 发布事务）。

## 过期语义的 P1 兜底（ADR-0020）

P1 没有过期扫描进程，但检索与取全文在查询时按 `status='published' AND expire_date >= 今天` 过滤，不满足者剔除并计入 excluded_expired——P1 首日即具备设计 4.4.1 的"过期排除"语义，用查询时间判定。P3 的扫描任务只负责把状态落库并发复审卡片，**不改变对外行为**。
