# console — 管理控制台 API（/api/*）

> **溯源**：技术设计文档 七；设计文档 七（7.1 功能清单 / 7.2 页面草图）；source_doc 设计 `docs/superpowers/specs/2026-07-04-source-doc-design.md`
> **代码入口**：`app/console/`
> **关联 ADR**：ADR-0021、ADR-0022
> **最后同步**：2026-07-05

## Agent 接入模型（API Key）

**一个 Agent 一把 Key**（ADR-0012「单 key」指每个 Agent 仅持有一把有效 Key，不做双 Key 轮换，而非「每个 domain 一把 Key」）。跨域访问通过 Key 的 `domain_whitelist` 数组授权，Gateway 一次 search 覆盖白名单内全部 domain；`common` 域对所有 Key 自动可见，无需写入 whitelist。

上游 Agent 只需配置单一环境变量（如 `KG_API_KEY`），**无需按 domain 维护多把 Key**。

| 接口 | 说明 | 最低角色 |
|-|-|-|
| GET /api/keys | Agent 中心化 Key 全量列表；可选 `created_by` / `status` 筛选；响应含 `created_by`、`created_by_name`、`calls_30d`、`last_used_at` | 平台管理员 |
| POST /api/keys | Agent 中心化签发：`agent_name` + `domain_whitelist[]` + `qps_limit`；明文仅返回一次 | 平台管理员 |
| PATCH /api/keys/{key_id} | 更新白名单或 QPS（不重发明文）；即时失效 Redis 缓存 | 平台管理员 |
| POST /api/domains/{code}/keys | 单域快捷签发（可传 `domain_whitelist` 覆盖为多域）；跨域优先走 POST /api/keys | 平台管理员 |
| GET /api/domains/{code}/keys | 域视角 Key 列表（含跨域 Key 的切片视图） | 平台管理员 |
| DELETE /api/keys/{key_id} | 吊销，即时生效 | 平台管理员 |

签发校验：whitelist 非空且元素为已注册 domain（`common` 可省略）；同一 `agent_name` 同时只能有一把 `active` Key（重复签发返回 409）。

## 登录与权限

控制台走飞书网页 OAuth：`GET /api/auth/login` 重定向到飞书授权页，回调 `/api/auth/callback` 换取身份后建 session（存 Redis，HttpOnly Cookie，有效期 12h，`KG_SESSION_TTL_HOURS` 可调）。用户首次登录自动创建 console_user（无任何角色）。错误 envelope 与错误码沿用 [gateway.md](gateway.md) 的通用约定。

三级角色（ADR-0021）：

| 角色 | 授予方式 | 权限 |
|-|-|-|
| 平台管理员 | 运维在库中置位，或通过 PATCH /api/users/{user_id} 授予 | 可操作一切（domain 注册、API Key 签发、审计查询、用户管理） |
| domain 管理员 | domain_member 表 role='admin' | 本域内配置与审知识 |
| domain 成员 | domain_member 表 role='member' | 本域内建知识、上传、编辑自己 owner 的条目 |

接口层以依赖注入的权限中间件统一校验。

## 接口清单（P1）

| 接口 | 说明 | 最低角色 |
|-|-|-|
| POST /api/domains | 注册 domain：code、short_code、name、default_ttl_days；code / short_code 全局唯一且不可改 | 平台管理员 |
| GET /api/domains ・ PATCH /api/domains/{code} | 列表；修改 name / default_ttl_days / type_topk | 平台管理员 |
| GET /api/users ・ GET /api/users/{user_id} ・ PATCH /api/users/{user_id} | 用户列表（q 搜索、分页）；详情（域角色 + 签发的 Key）；授予/撤销平台管理员（禁止自我撤销） | 平台管理员 |
| GET /api/domains/{code}/members ・ POST・DELETE /api/domains/{code}/members | 列出域成员；维护域成员与角色 | GET：domain 管理员；POST/DELETE：domain 管理员 |
| GET /api/knowledge | 列表：domain / type / status / tag / owner 筛选，page + page_size（≤100），updated_at 倒序 | domain 成员 |
| GET /api/knowledge/{kid} | 详情：元数据 + 当前正文 + 版本快照列表（溯源查看） | domain 成员 |
| POST /api/knowledge | 表单创建：`{domain, type, fields{段名: 内容}, tags[], owner, effective_date, expire_date?, save_mode: draft \| submit, source_doc_id? \| new_doc_name?}`；**所属知识文件必选**（选已有 active 同类型文件或就地新建 manual 文件，新建与首条条目同事务，ADR-0022）；submit 走流水线，响应含 kid、status、validation[] | domain 成员 |
| PUT /api/knowledge/{kid} | 内容更新，重新走校验；published 状态下 version+1 | owner / domain 管理员 |
| PATCH /api/knowledge/{kid}/meta | 元数据编辑：tags / owner / expire_date（飞书来源正文只读约束 P2 生效） | owner / domain 管理员 |
| POST /api/knowledge/{kid}/archive ・ /renew | 下架 / 续期（renew 更新 expire_date） | owner / domain 管理员 |
| POST /api/imports | Markdown 导入（multipart：domain + type + `file` 或 `text` 二选一 + doc_name；UTF-8，≤2MB）：**粘贴文本**（doc_name 必填，origin=manual）与**上传 .md**（doc_name 默认取文件名，origin=upload）双入口；同名文件此处即预查 409；同步解析 + 校验，返回 batch 与逐条结果 | domain 成员 |
| GET /api/imports/{id} ・ POST /api/imports/{id}/confirm | 预览批次（含 `stats`：total / valid / duplicate_in_batch / requires_review）；勾选 item_ids[] 确认入库，逐条返回 kid、status（published / pending_review）或失败原因，响应含 `summary` 与 `requires_review`。**首次导入 confirm 时才创建 source_doc**（预览放弃不留悬空文件，并发同名由唯一约束兜底 409）；更新批次按 align_action 分派（见下） | domain 成员 |
| GET /api/source-docs | 知识文件列表：domain / type / status / q（名称模糊）筛选；分页 `page` / `page_size`（默认 20，上限 100）；响应 `{ items, page, page_size, total }`；每条含 `entry_total` / `entry_published`（在架/总）及 `index_ready` / `index_indexing` / `index_failed`（published 条目按 index_state 聚合）；updated_at 倒序 | domain 成员 |
| GET /api/source-docs/{id} | 文件详情：基本信息 + 条目列表（按 doc_seq，含状态/版本/过期日）+ 批次历史（origin、操作人、对齐动作统计） | domain 成员 |
| GET /api/source-docs/{id}/content | 拼合全文 Markdown：非 draft / 非 archived 条目当前版本快照按 doc_seq 拼合（全文视图与在线编辑器预填共用） | domain 成员 |
| POST /api/source-docs/{id}/update | 全文更新（text 或 file 二选一）：重拆 + 对齐 → 生成带 align_action 的新批次返回预览；确认仍走 /api/imports/{id}/confirm | domain 成员 |
| POST /api/source-docs/{id}/renew | 整体续期：非 draft/archived 条目 expire_date 置为 today+days（缺省 domain 的 default_ttl_days）；expired 条目回 published | domain 成员 |
| POST /api/source-docs/{id}/offline | 整体下架：published/expired 条目 ARCHIVE + 文件置 archived；先 commit 再删索引，重复下架幂等成功 | domain 成员 |
| PATCH /api/source-docs/{id} | 重命名（domain 内同名 409）；归档文件不可改 | domain 成员 |
| GET /api/templates/{type}.md | 下载该类型标准 Markdown 模板（拒收提示中引用） | 登录即可 |
| GET /api/audit-logs ・ /api/audit-logs/export | 审计查询（时间 / key / action 过滤）与 CSV 导出 | 平台管理员 |

## 知识文件与更新对齐（ADR-0022，`source_docs.py` + `pipeline/align.py`）

文件＝管理容器，条目仍是生命周期原子；全部条目必须属于文件。文件级接口的权限与知识条目同口径：不存在或越权统一 404 不暴露存在性；归档文件的 update / renew / rename 返回 409 只读。

**对齐规则**（P1 零 LLM，标题精确匹配，FAQ 用「标准问法」段覆盖标题）：

| 情形 | align_action | 预览默认勾选 | confirm 动作 |
|-|-|-|-|
| 标题匹配且 content_hash 相同 | unchanged | 不勾 | 无（勾了也跳过，幂等） |
| 标题匹配、内容变化 | changed | 勾 | 原 kid 发布新版本（version+1），保留原 tags/owner |
| 新标题 | new | 勾 | 新条目入库 |
| 旧条目标题未出现 | disappeared | 勾 | 下架（归档条目重复 confirm 视为成功，不重复删索引） |

边界口径：**表单条目**（source_ref 前缀 `form:`）disappeared 时默认**不勾选**（响应的 `is_form` 字段驱动前端），避免外部文档粘贴更新时被误下架；**改标题**被判为"消失＋新增"，P1 已知边界由预览页人工纠正。confirm 后按新文本顺序重写存活条目 doc_seq，未出现在新文本且仍在架的条目排末尾保持原相对顺序；首次导入批次（全 new）跳过重写。

confirm **非原子**：批次内逐条调用 publish，每条各自内部提交（发布事务粒度在条目级，非整批次一次提交），中途失败不回滚已成功的条目，可安全重试未成功的 item_ids。changed 条目若目标 kid 状态不再允许更新（如 confirm 前被条目级下架），捕获后该条目返回 error 不影响其余条目；重试 changed 条目会再次 version+1（即使内容与上次相同也会产生新版本），此为已知边界，后续迭代考虑按 content_hash 相同跳过。

## 与前端页面的对应（设计 7.2，九页线框）

| 页面 | 阶段 | 后端支撑 |
|-|-|-|
| 知识列表 | P1 | GET /api/knowledge |
| 知识详情（含版本快照溯源） | P1 | GET /api/knowledge/{kid} |
| 表单录入 | P1 | POST /api/knowledge |
| 拆分预览确认（Markdown 上传与飞书首次导入共用；双 tab 粘贴/上传，更新模式带对齐徽标与汇总） | P1 | /api/imports 系列 |
| 知识文件列表 / 详情（条目、全文、变更历史三视图，在线编辑全文入口） | P1 | /api/source-docs 系列 |
| domain 列表 / domain 配置 | P1 | /api/domains 系列 |
| 平台管理（用户 / API Key） | P1 | /api/users 系列 + /api/keys 系列 |
| 审核待办队列（三 tab，含人工待补齐） | P2 | review_task 表随 P2 交付 |
| 打标 | P2 | 审计查询派生 |
| 飞书同步管理 | P2 | sync_state 表随 P2 交付 |

## 草稿约定（ADR-0021）

草稿不是独立页面，是知识列表的 draft 筛选视图；**仅本人可见**，超过 30 天未提交由后台清理。

## 实现补充（2026-07-04 落地时的口径细化）

- **校验拒收契约**：submit 命中 blocking（含敏感检测）返回 HTTP 200 + `{kid: null, status: "rejected", validation[]}`——拒收是业务响应而非 HTTP 错误，前端直接渲染 validation 列表；hash 重复返回 409 conflict（含已存在 kid）；warning 不阻塞，随成功响应返回。
- **草稿正文存储**：技术文档"快照只在发布时落"指版本历史（version≥1）；草稿正文借 `knowledge_version` 的 **version=0 槽位**承载（meta.fields 存表单原始字段供回填），发布时删除，不进入版本历史。清理任务同步删除该槽位。
- **错误码补充**：已登录但角色不足返回 **403 forbidden**（区别于未登录 401；Gateway 侧越权仍统一 404 不暴露存在性）；资源冲突（domain 重复 / 内容重复）返回 **409 conflict**。
- **快照 meta 含 fields**：发布快照的 meta 增加 `fields`（表单原始字段），编辑已发布知识时回填表单用。
- 模块文件结构：`auth.py`（session/OAuth/权限）、`admin.py`（domain/成员/key/审计）、`knowledge.py`（CRUD/导入/模板）、`source_docs.py`（知识文件查询与文件级操作）、`templates.py`（六类模板常量）、`router.py`（装配 + 登录端点）；对齐算法在 `pipeline/align.py`。
