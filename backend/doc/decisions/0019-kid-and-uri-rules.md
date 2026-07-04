# ADR-0019：kid 三段式与 URI 两级平铺规则

- **状态**：已采纳
- **日期**：2026-07-04（MVP 技术设计阶段）
- **来源**：技术设计文档 五；设计文档 4.2、5.1

## 背景

kid 与存储 URI 是 Agent 会引用、缓存的对外标识，规则一旦上线不可再改，必须在 P1 前定死。

## 决策

- **kid**：`{type_code}-{domain_short}-{seq}`；type_code 六类固定映射（policy→pol、product→prd，其余同名）；**common 域省略 domain 段**（如 term-0002）；seq 按 (domain, type) 独立自增、4 位零填充、允许空洞；kid 跨版本稳定、终生不复用。
- **URI**：`viking://resources/{domain}/{type}/{kid}.md`；只含 kid 不含版本号，更新为同 URI 覆盖写、**URI 永不变**；domain/type 两级平铺，**tag 不进 URI**（tags 自由输入后更不可能，ADR-0016）；标题不参与 URI。

## 理由

kid 自描述（一眼看出类型与域）便于排查与审计；URI 稳定性是 Agent 缓存引用的契约；两级平铺控制目录扇出，超 500 条的分桶是逻辑路径下的后台操作、无迁移成本。
