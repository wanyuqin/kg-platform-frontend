# backend 设计文档目录

本目录沉淀知识管理平台后端的**项目规划**与**各模块设计**，是面向开发者的实现视角文档，随代码一起迭代。

## 与飞书文档的关系

设计的权威源是两份已评审的飞书文档：

| 文档 | 链接 | 角色 |
|-|-|-|
| 设计文档 | https://my.feishu.cn/docx/C3dsddOUdojwZRxyypPc98pHnth | 回答"做什么、为什么"（九章 + 附录，评审已通过） |
| 技术设计文档（MVP） | https://my.feishu.cn/docx/F2RzduAxQoPUXax8mfucbePanLf | 回答"怎么建"（十三章 + 附录） |

本目录**不是**飞书文档的镜像，而是按 `app/` 实际代码模块重新组织的提炼：每篇文档标注对应的飞书章节作为溯源锚点。引用约定与代码注释一致——"设计 x.x"指设计文档章节，"技术 x.x"指技术设计文档章节。

## 目录结构

```text
doc/
├── README.md        # 本文件：导航 + 文档规范
├── overview.md      # 总体架构：分层、进程结构、模块↔文档↔章节映射
├── roadmap.md       # 项目规划：MVP 定义、三阶段里程碑、验收口径、当前进度
├── decisions/       # ADR 决策记录（可追溯的核心载体）
│   ├── README.md    # ADR 索引
│   └── NNNN-*.md    # 每个已拍板决策一个文件
└── modules/         # 按代码模块一篇设计文档
    ├── gateway.md   # app/gateway/   /v1/* 检索网关
    ├── console.md   # app/console/   控制台 API 与权限
    ├── pipeline.md  # app/pipeline/  解析 / 校验 / 敏感检测 / 发布
    ├── domain.md    # app/domain/    kid 规则 + 知识状态机
    ├── storage.md   # app/storage/   PG / Redis / OpenViking
    ├── scheduler.md # app/scheduler/ 定时任务
    └── audit.md     # app/audit/     审计日志
```

## 追溯约定

每篇模块文档头部带统一元信息块：

```markdown
> **溯源**：技术设计文档 第 X 章；设计文档 第 Y 章
> **代码入口**：`app/xxx/`
> **关联 ADR**：ADR-NNNN、ADR-NNNN
> **最后同步**：YYYY-MM-DD
```

由此任何一篇文档都能双向追到：飞书哪一章（设计依据）→ 代码哪个目录（实现落点）→ 哪些 ADR（决策约束）。

## 迭代流程

设计发生变更时按以下顺序操作，保证"为什么改"永远有记录：

1. **涉及决策变更**（推翻或修订已有 ADR 的结论）→ 先新增一条 ADR，旧 ADR 状态改为 `superseded by ADR-NNNN`，不删除旧文件；
2. **修改受影响的模块文档**，更新其"关联 ADR"和"最后同步"日期；
3. **纯实现细节调整**（不动决策）→ 直接改模块文档并更新同步日期，git 历史即时间线；
4. 若变更同时影响飞书文档口径，先在飞书侧达成一致再回写本目录（飞书是评审基线）。

## 写作约定

- 全部内容用**中文**撰写；
- 飞书画板类内容在本目录用 Mermaid 图或文字重表达，并注明原画板出处章节；
- 阶段标注沿用 P1 / P2 / P3（含义见 [roadmap.md](roadmap.md)）；未实现的预留设计显式标注阶段，不写"TODO"。
