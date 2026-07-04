# console — 管理控制台 API（/api/*）

> **溯源**：技术设计文档 七；设计文档 七（7.1 功能清单 / 7.2 页面草图）
> **代码入口**：`app/console/`
> **关联 ADR**：ADR-0021
> **最后同步**：2026-07-04

## 登录与权限

控制台走飞书网页 OAuth：`GET /api/auth/login` 重定向到飞书授权页，回调 `/api/auth/callback` 换取身份后建 session（存 Redis，HttpOnly Cookie，有效期 12h，`KG_SESSION_TTL_HOURS` 可调）。用户首次登录自动创建 console_user（无任何角色）。错误 envelope 与错误码沿用 [gateway.md](gateway.md) 的通用约定。

三级角色（ADR-0021）：

| 角色 | 授予方式 | 权限 |
|-|-|-|
| 平台管理员 | 运维在库中置位 `is_platform_admin` | 可操作一切（domain 注册、API Key 签发、审计查询） |
| domain 管理员 | domain_member 表 role='admin' | 本域内配置与审知识 |
| domain 成员 | domain_member 表 role='member' | 本域内建知识、上传、编辑自己 owner 的条目 |

接口层以依赖注入的权限中间件统一校验。

## 接口清单（P1）

| 接口 | 说明 | 最低角色 |
|-|-|-|
| POST /api/domains | 注册 domain：code、short_code、name、default_ttl_days；code / short_code 全局唯一且不可改 | 平台管理员 |
| GET /api/domains ・ PATCH /api/domains/{code} | 列表；修改 name / default_ttl_days / type_topk | 平台管理员 |
| POST・DELETE /api/domains/{code}/members | 维护域成员与角色 | domain 管理员 |
| POST /api/domains/{code}/keys | 为 Agent 签发 API Key；**明文仅在本响应返回一次** | 平台管理员 |
| DELETE /api/keys/{key_id} | 吊销，即时生效 | 平台管理员 |
| GET /api/knowledge | 列表：domain / type / status / tag / owner 筛选，page + page_size（≤100），updated_at 倒序 | domain 成员 |
| GET /api/knowledge/{kid} | 详情：元数据 + 当前正文 + 版本快照列表（溯源查看） | domain 成员 |
| POST /api/knowledge | 表单创建：`{domain, type, fields{段名: 内容}, tags[], owner, effective_date, expire_date?, save_mode: draft \| submit}`；submit 走流水线，响应含 kid、status、validation[] | domain 成员 |
| PUT /api/knowledge/{kid} | 内容更新，重新走校验；published 状态下 version+1 | owner / domain 管理员 |
| PATCH /api/knowledge/{kid}/meta | 元数据编辑：tags / owner / expire_date（飞书来源正文只读约束 P2 生效） | owner / domain 管理员 |
| POST /api/knowledge/{kid}/archive ・ /renew | 下架 / 续期（renew 更新 expire_date） | owner / domain 管理员 |
| POST /api/imports | Markdown 上传（multipart：file + domain + type；UTF-8，≤2MB）；同步解析 + 校验，返回 batch 与逐条结果 | domain 成员 |
| GET /api/imports/{id} ・ POST /api/imports/{id}/confirm | 预览批次；勾选 item_ids[] 确认入库，逐条返回 kid 或失败原因 | domain 成员 |
| GET /api/templates/{type}.md | 下载该类型标准 Markdown 模板（拒收提示中引用） | 登录即可 |
| GET /api/audit-logs ・ /api/audit-logs/export | 审计查询（时间 / key / action 过滤）与 CSV 导出 | 平台管理员 |

## 与前端页面的对应（设计 7.2，九页线框）

| 页面 | 阶段 | 后端支撑 |
|-|-|-|
| 知识列表 | P1 | GET /api/knowledge |
| 知识详情（含版本快照溯源） | P1 | GET /api/knowledge/{kid} |
| 表单录入 | P1 | POST /api/knowledge |
| 拆分预览确认（Markdown 上传与飞书首次导入共用） | P1 | /api/imports 系列 |
| domain 列表 / domain 配置 | P1 | /api/domains 系列 |
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
- 模块文件结构：`auth.py`（session/OAuth/权限）、`admin.py`（domain/成员/key/审计）、`knowledge.py`（CRUD/导入/模板）、`templates.py`（六类模板常量）、`router.py`（装配 + 登录端点）。
