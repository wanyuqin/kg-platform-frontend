# storage — 存储层（PostgreSQL / Redis / OpenViking）

> **溯源**：技术设计文档 三（DDL）、九（OpenViking）、十（Key 与限流）、十二（部署）；设计文档 五
> **代码入口**：`app/storage/`（pg/ redis/ viking/）
> **关联 ADR**：ADR-0002、ADR-0003、ADR-0004、ADR-0012、ADR-0018
> **最后同步**：2026-07-04

## PostgreSQL（storage/pg/）

### 总则（技术 3.1）

时间字段一律 TIMESTAMPTZ；枚举用 VARCHAR + CHECK 约束（P2 扩展时免锁表改枚举类型）；迁移工具 Alembic，DDL 变更只走迁移脚本（当前基线 `alembic/versions/0001`，含 P1 全部 DDL 与 common 域种子数据）。**PostgreSQL 是元数据与状态的唯一事实来源**（ADR-0002），OpenViking 侧字段皆为冗余。

### 表清单（P1）

完整 DDL 以 alembic 迁移脚本为准，此处记设计意图：

| 表 | 用途 | 关键点 |
|-|-|-|
| domain | 权限隔离与治理配置单位（设计 5.1） | code 即 viking:// 一级目录名（`^[a-z][a-z0-9-]{1,31}$`）；short_code 为 kid 域段（common 固定空串）；default_ttl_days 默认 365；type_topk JSONB 存类型级 top_k 覆盖；reviewer_user_id / feishu_folder_token 为 P2 预留 |
| console_user / domain_member | 三级角色（设计 7.1） | user_id 为飞书 open_id；is_platform_admin 由运维置位；domain_member.role ∈ admin / member |
| knowledge | 知识元数据主表（设计 4.2） | tags TEXT[] 自由输入可空（ADR-0016）+ GIN 索引；status 五态 CHECK；index_state 与 status 正交；risk_note P1 仅记录 |
| knowledge_version | 版本正文快照（设计 4.2） | 审计还原与打标的数据基础；UNIQUE (kid, version)；read 接口从此表取全文（ADR-0018） |
| kid_sequence | kid 序号发生器 | (domain_code, type) 主键，发布事务内取号 |
| api_key | Agent API Key（设计 6.1.3） | 库内仅存 SHA-256(完整明文)；domain_whitelist 不含 common（鉴权时自动并入）；默认 10 QPS（ADR-0012） |
| import_batch / import_item | Markdown 上传批次与拆分明细 | 拆分预览确认页的后端；item.validation 记录逐条校验结果，result_kid 确认入库后回填 |
| audit_log | 审计日志（设计 6.3） | 按月 RANGE 分区，保留 180 天；详见 [audit.md](audit.md) |

### 关键索引与约束

- `idx_knowledge_list (domain_code, type, status, updated_at DESC)`——列表查询；
- `idx_knowledge_expire (status, expire_date)`——过期兜底过滤与 P3 扫描；
- `idx_knowledge_tags GIN (tags)`——tag 过滤；
- `uq_knowledge_hash ON knowledge (content_hash) WHERE status <> 'archived'`——全库去重第一级漏斗，冲突返回 409。

### P2 预留（技术 3.3）

sync_state（飞书同步状态）与 review_task（审核任务）随 P2 迁移脚本交付，本期**不建表**——避免未联调的结构提前入库。knowledge 的 pending_review 状态、risk_note、domain 的 reviewer_user_id / feishu_folder_token 已预留，P2 无需改主表结构。

## Redis（storage/redis/）

三个用途，容量要求低：

| 用途 | 键 / 机制 |
|-|-|
| API Key 缓存 | `ak:{key_id}`，TTL 60s，缓存 hash / 白名单 / qps / status，miss 回源 PG；吊销时置 revoked + 主动 DEL，即时生效（60s TTL 只是兜底） |
| 限流（rate_limit.py） | 按 Key 固定窗口 1 秒：`INCR rl:{key_id}:{epoch_sec}`，首次自增 `EXPIRE 2`；超 qps_limit 返回 429 + `Retry-After: 1`。Redis 故障放行并告警（可用性优先于限流精度） |
| 控制台 session | HttpOnly Cookie 对应的服务端 session，有效期 12h |

API Key 明文格式 `kp_{key_id}_{secret}`：key_id 8 位小写 base32（库内主键），secret 32 位 URL-safe 密码学随机串。签发时明文只在响应出现一次；泄露走吊销重发，不支持双 key 轮换（ADR-0012）。校验路径：解析 key_id → 读缓存 → **常数时间比对** SHA-256 → 校验 status=active。

## OpenViking（storage/viking/）

独立 HTTP 服务部署，平台经本客户端访问，仅使用 resources 子集（设计 5.2）。四类调用的 HTTP 形态已经 PoC 实测定案（2026-07-04，服务版本 v0.4.7）：

| 用途 | 实测端点 | PoC 结论 |
|-|-|-|
| 写入 / 更新 | `POST /api/v1/content/write` | mode 语义：`create`=新建（父目录自动建，已存在 409）、`replace`=覆盖（不存在 404）；**"同 URI 幂等覆盖"由客户端组合实现**（replace → 404 → create）。`wait=false` 异步写约 100ms 返回、语义索引后台生成；`wait=true` 会阻塞到 L0/L1 生成完成，线上禁用。**禁用 add_resource**（ADR-0004） |
| 下架 | `DELETE /api/v1/fs?uri=` | 404 视为成功（下架重试幂等）；expired 不删（检索排除由 PG 回查实现，保留正文供续期） |
| 检索 | `POST /api/v1/search/find` | `target_uri` **原生支持数组**，单次多前缀调用可行、无需 N 次合并（ADR-0003 的白名单前缀直接下推）；命中含 level=0/1 的目录派生物（`.abstract.md` / `.overview.md`），**知识文件恒为 level=2，必须过滤**；结果字段 uri / score / abstract |
| 索引就绪 | find probe（限父目录）level=2 命中该 uri | **唯一可靠的文件级判据**，与"检索可见"语义一致，驱动 index_state：indexing → ready；已排除的候选：content/abstract（对文件恒返回目录级 fallback）、fs/ls 的 abstract 字段（不及时回填）、tasks（不追踪写入任务）、system/wait（全局等待，仅适合测试）。`is_indexed(uri, probe_query)` 的 probe_query 传知识标题 |

**鉴权与租户初始化**：请求带 `x-api-key` header。**ROOT key（ov.conf 的 root_api_key）被禁止访问数据 API**（403 PERMISSION_DENIED）——部署时须先用 root key 调 `POST /api/v1/admin/accounts`（body：account_id + admin_user_id）创建租户，将返回的**用户级 key** 配到 `KG_VIKING_API_KEY`；账号数据存于 workspace，重建 workspace 后须重新初始化（参考 `scripts/poc_viking.py` 的 `ensure_user_key`）。

⚠️ **embedding 维度冻结**：ov.conf 的 embedding 配置必须显式声明 `dimension`（如 bge-m3=1024、text-embedding-3-large=3072）；向量集合按初始化时的维度建立且冻结，声明与模型实际输出不一致会持续报 `Dense vector dimension mismatch` 且检索调用直接崩溃服务进程；修正维度或更换 embedding 模型都必须清空 workspace 重建（无在线迁移），**线上更换 embedding 模型 = 全量重灌，属重大变更**。

写入正文格式为 Frontmatter + 模板化 Markdown。Frontmatter 只冗余检索展示所需字段（kid、title、domain、type、tags、source_url、updated_at），**事实一律以 PG 为准**。

**模型网关依赖**：OpenViking 每次写入后自动生成 L0/L1 摘要、SDK 不支持直填或跳过（设计 4.5 已查证）。需在 OpenViking 侧配置公司模型网关 endpoint 与 key，为 P1 上线前置项。

**降级**：search 调用 800ms 超时 + 1 次重试，失败 503；read 不依赖 OpenViking（ADR-0018）。

### PoC 验证清单（2026-07-04 实测，脚本 `scripts/poc_viking.py`，服务 v0.4.7 本地 Docker）

- [x] content/write 覆盖写语义与幂等性——**通过**：replace 覆盖 / create 新建，upsert 组合后同 URI 幂等，read 返回最新内容（结论已落地 client.write）
- [x] 写入 → 可被检索的实际延迟——**约 16s**（异步写约 100ms 返回 + 后台语义处理；本地环境 + 第三方模型 API，线上以公司网关实测为准），符合设计"秒～分钟级"假设，发布→可检索的秒级延迟口径成立（ADR-0021 第 5 条）
- [x] 多目录前缀检索的接口形态——**单次多前缀可行**：target_uri 原生支持数组，search 第 2 步无需 N 次合并
- [x] L0/L1 生成状态的查询方式——**无按条状态接口**，落入设计预判的兜底分支："可检索到该 path"即就绪（find probe level=2 命中，已落地 client.is_indexed）
- [x] 中文 L0 摘要质量抽检（20 条六类 mock）——20/20 生成；**发现质量问题：L0 语言不稳定**，部分中文内容生成英文摘要（本地 VLM 为第三方模型，实测 sop/product/case/term 目录命中英文）。影响：search 返回给 Agent 的 summary 可能中英混杂。对策：线上接公司模型网关后复测；若仍不稳定，Gateway 组装响应时 summary 回落用 PG 的 title（6.2 第 4 步本就以 PG 为准）
- [x] 单目录批量条目检索延迟——本地 20+ 条：**avg 233ms / p95 378ms**，在 800ms 预算内；**500 条压测后置**至免单域真实数据灌入后（本地灌入耗模型配额，性价比低）
- [ ] AGPLv3 开源合规评审发起（流程动作，由业务侧走法务流程，平台侧不阻塞开发）

> 附加发现（未列入原清单）：① ROOT key 禁止访问数据 API，须建租户用用户级 key（见上文"鉴权与租户初始化"）；② embedding 维度冻结问题（见上文 ⚠️）；③ find 命中含目录派生物需按 level=2 过滤；④ 账号数据随 workspace 清空而丢失。

## 备份

数据库每日全量备份 + WAL 归档——正文快照都在 PG，备份即覆盖全部事实数据；**OpenViking 数据可由 PG 重放重建，不单独备份**。
