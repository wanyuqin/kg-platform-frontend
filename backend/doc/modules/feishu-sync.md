# feishu-sync — 飞书单文档同步

> **溯源**：设计文档 三、第四章 4.3；技术设计文档 八；roadmap.md Phase 2
> **代码入口**：`app/feishu/`（client.py / oauth.py / event.py / parser.py / media.py / sync.py / doc_resolver.py / docx_to_markdown.py）+ `app/scheduler/jobs/feishu_poll.py` + `app/storage/mq/`
> **关联 ADR**：ADR-0014、ADR-0015、ADR-0022、ADR-0006、ADR-0009、ADR-0016、ADR-0017
> **最后同步**：2026-07-05（骨架稿）

## 1. 总则

### 1.1 目标

把飞书云文档（docx / doc / wiki）作为知识文件的**外部权威源**，自动同步到平台，重拆对齐为条目；运营侧"只改飞书原文"即可触发内容更新，无需在平台内重复编辑。

### 1.2 三原则（ADR-0015）

1. **单文档注册**：不做目录挂载；运营在控制台粘贴飞书 URL → 平台后台解析为 (obj_type, document_token)，写入 `source_doc.feishu_*`
2. **平台侧正文只读**：飞书来源的条目在控制台**不显示"编辑"按钮**；任何修改必须回飞书原文，改完触发再同步
3. **事件 + 轮询双通道**：事件订阅做低延迟触发，轮询兜底（事件无内容、无 SLA，调研结论）

### 1.3 明确不做（P2 范围外）

- 增量 Block 同步（依赖本地 cursor 做 diff）—— P3
- 跨文档引用解析（Synced Block 类型 49/50）—— 收敛为占位文本 + 告警
- 飞书评论 / 协作信息导入 —— 飞书 API 不支持，调研结论
- 飞书侧删除 / 归档的"软删除回滚" —— 检测到 `is_deleted` 后只标记 `source_doc.archived_at`，不回滚已发布的条目
- 飞书目录批量注册 —— 目录内容不可控（ADR-0015）
- 飞书表格（Sheet）/ 多维表格（Bitable）/ 幻灯片（Slide） —— 仅文档类型（docx/doc/wiki 节点指向 docx）

## 2. 架构与数据流

```
┌────────────┐  事件回调    ┌──────────────┐  鉴权 + 入队    ┌──────────────────┐
│ 飞书事件    │ ─────────→ │ gateway 路由  │ ────────────→ │ RocketMQ          │
│ doc_change │             │ /feishu/event │              │ topic:            │
└────────────┘             └──────────────┘              │   kg.feishu.event  │
                                                          └────────┬─────────┘
┌────────────┐  定时 5min   ┌──────────────┐  按 sync_state  │                  │
│ scheduler  │ ─────────→ │ feishu_poll   │  cursor 拉增量   │                  │
│ 进程       │             │ .py           │                 │                  │
└────────────┘             └──────────────┘                 ▼                  ▼
                                                         ┌──────────────────┐
                                                         │ sync worker       │
                                                         │  消费 MQ 消息     │
                                                         │  同步主流程       │
                                                         └────────┬─────────┘
                                                                  │
                                                                  ▼
                                                   ┌──────────────────────────┐
                                                   │ 1. DocResolver 解析 URL   │
                                                   │ 2. docx_to_markdown 渲染  │
                                                   │ 3. MediaDownloader 图片  │
                                                   │    → MinIO kg-assets     │
                                                   │ 4. parser.py 切条目       │
                                                   │    （复用 P1，零 LLM）    │
                                                   │ 5. align.py 重拆对齐      │
                                                   │    （复用 P1）            │
                                                   │ 6. risk_matrix 评分       │
                                                   │    低 → publish()         │
                                                   │    中/高 → review_task    │
                                                   │         + 飞书卡片        │
                                                   └──────────────────────────┘
```

## 3. 模块划分与代码组织

```
backend/app/feishu/
├── client.py            # 飞书 OpenAPI 客户端（统一鉴权 + 限流 + 重试）
├── oauth.py             # 复用 app/console/auth.py 的 app_access_token；不重复实现
├── event.py             # 事件回调签名校验 + 解析（Encrypt Key / Verification Token）
├── doc_resolver.py      # URL/wiki → (obj_type, document_token, doc_url)
├── docx_to_markdown.py  # 核心：Block 树 → Markdown 字符串 + block_id 映射表
├── media.py             # 图片下载（drive medias API）→ MinIO 上传
├── parser.py            # 复用 app/pipeline/parser.py 的同款入口（不在此处重写）
├── risk_matrix.py       # 风险评分规则（hash 变化 / 标题数差 / 敏感字段命中）
├── card.py              # 飞书卡片消息发送（im/v1/messages）
└── sync.py              # 同步主流程编排（被 MQ consumer + scheduler poll 调用）

backend/app/scheduler/jobs/
└── feishu_poll.py       # 轮询 worker，按 KG_FEISHU_POLL_INTERVAL_SEC 触发

backend/app/storage/mq/
├── producer.py          # RocketMQ 生产者封装（封装 topic 名 + 序列化）
└── consumer.py          # RocketMQ 消费者封装（封装 group + 幂等 + 重试 + DLQ）

backend/app/gateway/router.py
└── /feishu/event 路由    # 飞书事件回调入口（POST）
```

**复用原则**：
- P1 `pipeline/parser.py`、`pipeline/align.py`、`pipeline/publish.py`、`pipeline/sensitive.py`、`pipeline/content_hash.py` **一行不改**，直接 import 复用
- P1 `console/auth.py` 的 `app_access_token` 获取函数直接复用，飞书同步流不重复实现 OAuth

## 4. DocResolver（URL/wiki/document_id 解析）

### 4.1 支持的 URL 形式

| 形式 | 例子 | 解析路径 |
|---|---|---|
| 新版 docx | `https://xxx.feishu.cn/docx/abc123` | obj_type=docx, token=`abc123` |
| 旧版 doc | `https://xxx.feishu.cn/docs/abc123` | obj_type=doc, token=`abc123` |
| wiki 节点 | `https://xxx.feishu.cn/wiki/abc123` | `wiki/v2/spaces/get_node` → obj_type + obj_token |

### 4.2 解析流程

1. 正则匹配 URL 前缀，确定初始类型
2. wiki 类型调 `GET /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token` 拿到 `obj_type` + `obj_token`
3. obj_type 校验：仅接受 22 (Docx) / 16 (Wiki→递归) / 1 (Doc)；其他返回明确错误
4. 返回 `ResolvedDoc(obj_type, document_token, doc_url, title?)`

### 4.3 决策点 D1（已拍板）

- **D1.1**：控制台绑定流程是否需要 OAuth picker？
  - ✅ **不需要**：运营粘贴 URL，平台后台用 app_access_token 拉取
- **D1.2**：是否支持运营在控制台"搜索"飞书文档列表再选？
  - ✅ **不做**

### 4.4 权限预检（核心环节，绑定前必做）

**问题**：飞书权限分两层——
- 应用级 scope（在飞书开发者后台开通：`docx:document:readonly` / `wiki:wiki:readonly` / `drive:media:download` / `im:message:send_as_bot`）
- 文档级授权（运营必须把"知识平台机器人"加到文档所在知识库的成员列表）

只有 scope 但未被加到知识库 → API 返回 `99991663` / `99991672` / `231002` 等权限错误。

**关键事实（飞书权限模型调研结论）**：
- 飞书的 OAuth 流程**仅用于用户登录**（拿 `user_access_token`），**不能用来"授权应用访问资源"**
- 资源授权必须在飞书客户端**手动操作**："添加文档应用" / 知识库成员管理添加成员
- 飞书**没有 deep link** 可以直接跳到"添加成员"页面
- 即使拿到运营的 `user_access_token`，机器人（应用）的 token 仍然没被加到知识库，下次同步还是失败
- 因此"弹 OAuth 弹窗让运营一键授权 → 自动拉取"**这条路飞书没开放**，必须人工去飞书端操作

**预检流程**（在 `resolve` 接口返回前执行）：

1. 用 `tenant_access_token`（机器人身份）调 `GET /open-apis/docx/v1/documents/:token`（仅拿元信息，不拉 Block，便宜）
2. wiki URL 先走 §4.2 第 2 步拿到 document_id，再走此预检
3. 失败码精确映射：

| 飞书错误码 | 含义 | 平台错误码 | 运营提示 |
|---|---|---|---|
| `99991663` | 文档不存在或无权限 | `feishu_doc_not_found` | 文档已被删除 / 分享链接失效 |
| `99991672` | wiki 节点权限不足 | `feishu_wiki_no_scope` | 申请 `wiki:wiki:readonly` scope |
| `231002` | 应用未被授权到此文档 | `feishu_app_not_in_kb` | 把"知识平台机器人"加到知识库成员 |
| `99991668` | 应用被禁用 | `feishu_app_disabled` | 联系平台管理员 |
| 其他 5xx | 飞书侧异常 | `feishu_api_error` | 稍后重试 |

4. **失败时不创建 `source_doc`**（D9.2 决策：避免脏数据）
5. **成功后才创建 source_doc + 触发首次同步**

### 4.5 诊断式验证（运营授权后的智能感知）

**目标**：运营在飞书端完成授权后，平台**自动感知**并立即触发同步，**不需要运营再次操作**。

**完整诊断流程**（运营粘贴 URL 后，平台自动执行）：

```
1. POST /api/source-docs/resolve {feishu_url}
2. DocResolver 解析 URL → 拿到 obj_type + document_token
3. 用 tenant_access_token（机器人身份）调 GET /documents/:token
   ├─ ✅ 成功 → 返回 source_doc 预览 + 创建 + 首次同步
   └─ ❌ 失败（feishu_app_not_in_kb）
         ↓
4. 弹"权限诊断"窗口：自动调用 GET /documents/:token 用运营的 user_access_token
   ├─ ✅ 运营能访问 → 弹窗"你去飞书端把'知识平台机器人'加为知识库成员"
   │                   + 操作指引（含截图）
   │                   + "我已授权"按钮 + 自动轮询启动
   └─ ❌ 运营也不能访问 → 弹窗"联系文档所有者/知识库管理员开权限"
                          + 显示运营自己的 user_access_token 诊断信息
5. 运营去飞书端授权完成后：
   选项 A：点"我已授权" → 平台立即调 resolve 重试，成功则触发同步
   选项 B：什么都不做 → 平台每 60s 自动重试 resolve（最多持续 24h）
6. 失败累计 24h → sync_status='auth_timeout'，停止轮询
7. 运营可手动重新触发
```

**关键设计**：
- **`user_access_token` 仅用于诊断**，不用于实际同步
- **实际同步始终用 `tenant_access_token`**（机器人身份，确保一致性）
- **轮询有上限**（24h × 60 次/小时 = 1440 次），避免资源浪费
- **运营主动点击优先于轮询**（按钮点击立即触发，跳过下次轮询）

**资源消耗估算**：
- 每个待授权文档每小时 60 次 resolve 调用
- 100 个待授权文档 × 1440 次/24h = 14.4 万次/天
- 飞书 API 限流 3 QPS/应用，足够
- 单次 resolve 仅调一次 `GET /documents/:token`，成本可接受

**实现要点**：
- `source_doc.sync_status` 新增 `'awaiting_auth'` 状态
- 启动轮询时写 `awaiting_auth_since` 时间戳
- 24h 兜底：scheduler 每小时检查一次，超时文档标 `'auth_timeout'`
- 授权成功后清理 `awaiting_auth_since` 字段

### 4.6 应用 scope 清单（写到 `local-dev-p2.md` 飞书权限申请段）

| Scope | 用途 | 必需 |
|---|---|---|
| `docx:document:readonly` | 读新版文档 + Block 树 | ✅ |
| `wiki:wiki:readonly` | wiki URL → document_id 解析 | ✅ |
| `drive:drive:readonly` | 读云空间文件元信息 | ✅ |
| `drive:media:download` | 下载图片二进制 | ✅ |
| `im:message:send_as_bot` | 发送审核卡片 | ✅ |
| `docs:doc:readonly` | 旧版文档兼容 | P3 再申请 |

**控制台操作指引**（绑定失败时弹窗展示，文案锁定）：

> ### 📋 把"知识平台机器人"加到知识库
>
> 1. 在飞书客户端打开文档所在的**知识库**
> 2. 点击右上角「···」→「**知识库设置**」→「**成员管理**」
> 3. 点击「**添加成员**」→ 搜索「**知识平台机器人**」（或应用名）
> 4. 权限选「**仅阅读**」
> 5. 回到本平台，点击「**我已授权**」

### 4.7 决策点 D9（已拍板 + 部分待拍板）

- **D9.1**：知识库授权失败时，是否在控制台直接展示一个**飞书端的"添加成员"链接**？
  - ✅ **不做**：飞书目前没有 deep link 直达知识库成员管理页
- **D9.2**：权限预检失败的 source_doc 是否仍然创建（标 `sync_status='failed'`）？
  - ✅ **否**：未通过预检不创建 source_doc，避免脏数据
- **D9.3**：权限被撤销后（原本能同步，后来失败），平台侧如何感知？
  - ✅ **立即告警 + 暂停同步 + 提供重新开启方式**（详见 §4.8 权限撤销恢复流程）
- **D9.4**（新增）：运营授权后平台感知策略
  - ✅ **接受建议：双轨（按钮 + 60s 轮询 24h）**
- **D9.5**（新增）：诊断时是否用 user_access_token？
  - ✅ **接受建议：是（精准诊断，与控制台 OAuth 复用）**
- **D9.6**（新增）：轮询 24h 超时后状态
  - ✅ **接受建议：`'auth_timeout'`**（控制台明确显示，可手动重新触发）
- **D9.7**（新增）：诊断式验证是否 P2 必须，还是 P3 优化？
  - ✅ **接受建议：P2 必须**（体验差距大，~1.5 天工作量）

### 4.8 权限撤销 + 恢复同步（D9.3 实现细节）

**场景**：source_doc 曾成功同步过（`sync_status='success'`），后来运营在飞书端把机器人从知识库成员里移除了。

**感知时机**（D9.3 拍板：**立即告警**）：
- 飞书**没有公开的"应用被移出知识库"事件**（调研结论）
- 实际感知路径：**下次同步触发时检测**（事件触发 / 轮询触发 / 手动触发）
- 检测到 `feishu_app_not_in_kb` 错误码 + sync_status 历史为 success → 视为"权限被撤销"

**处理流程**：

```
1. 同步触发 → 阶段一权限预检失败（feishu_app_not_in_kb）
2. 检查 source_doc 历史：
   ├─ 历史 sync_status 是 'success' 或 'awaiting_auth'
   │   → 标 sync_status='permission_revoked'
   │   → 写 last_sync_error='permission_revoked: 飞书端已移除机器人权限'
   │   → 发控制台红字告警（"⚠️ 文档 [xxx] 的飞书权限被撤销，已暂停同步"）
   │   → **不进 MQ 重试**（避免重试风暴）
   │   → **触发授权恢复流程**（见 §4.5 复用 60s 轮询 + 按钮）
   │
   └─ 历史 sync_status 是 'pending' / 'awaiting_auth'（首次绑定就没权限）
       → 走 §4.4 权限预检失败流程
```

**重新开启同步**（用户拍板要求"留有重新开启的方式"）：

1. **运营在飞书端重新添加机器人到知识库**
2. **感知恢复**（复用 §4.5 机制）：
   - 选项 A：运营点控制台"恢复同步"按钮 → 立即重试 resolve，成功则触发同步
   - 选项 B：什么都不做 → 60s 轮询自动重试，最长 24h（与 awaiting_auth 共用机制）
3. **状态流转**：
   - `permission_revoked` + resolve 成功 → `sync_status='pending'` → 触发同步 → `success`
   - `permission_revoked` 持续 24h → `sync_status='auth_timeout'`（共用超时状态）
4. **运营可手动重新触发**：控制台"立即同步"按钮绕过轮询，立即重试 resolve

**控制台告警恢复**：resolve 成功后，自动清除控制台红字告警 + 通知运营"文档 [xxx] 同步已恢复"。

**实现复用**：
- `permission_revoked` 与 `awaiting_auth` 共用 §4.5 的轮询机制（60s 间隔、24h 超时）
- 区别仅在控制台展示：`permission_revoked` 红字告警（"曾同步过，现在失败"），`awaiting_auth` 黄字提示（"首次绑定，等待授权"）
- 代码层用一个统一的"等待授权"状态机：`PERMISSION_REVOKED` 和 `AWAITING_AUTH` 都触发轮询，UI 层差异化展示

## 5. docx_to_markdown.py（核心渲染器）

### 5.1 设计约束

- **必须保留 Block 结构**：输出 `MarkdownWithMap` dataclass，包含 `markdown: str` + `block_map: dict[seq, block_id]`（seq 是 P1 parser 切出的条目编号）
- **不支持的 Block type**（999 / 50 / 51 / Grid / Chat Card / UML 等）：跳过 + 在 `skipped_blocks: list[block_id]` 记录，**全文档结束后汇总告警到控制台**，但不阻塞同步
- **递归处理嵌套**（Synced Block 49/50）：调用 `with_descendants` 接口一次拉完，由渲染器按 parent_id 树形组装

### 5.2 Block type → Markdown 映射表（核心）

| Block type | 名称 | Markdown 渲染 | 备注 |
|---|---|---|---|
| 1 | Page | （根，跳过） | |
| 3-11 | Heading 1-9 | `#` ~ `######` + 空格 + text_run 拼接 | 超过 6 级截断到 6 级（CommonMark 限制） |
| 2 | Text | 直接拼接 elements[].text_run.content，段尾 `\n\n` | |
| 12 | Bullet List | `- ` 缩进递归 | 嵌套按 children 缩进 |
| 13 | Ordered List | `1. ` 缩进递归 | sequence 字段指定编号 |
| 14 | Code Block | ```` ```language\n...\n``` ```` | language 枚举见 5.3 |
| 15 | Quote | `> ` 缩进递归 | |
| 17 | Todo | `- [ ] ` 或 `- [x] ` | 按 done 字段 |
| 18 | Bitable | 跳过 + 告警 | 暂不支持 |
| 19 | Callout | `> [!NOTE]\n> ...` | 转 quote |
| 22 | Divider | `---` | |
| 23 | File | `📎 [filename](url)` + 告警 | 文件下载走独立流程 |
| 24-25 | Grid / Grid Column | 跳过 + 告警 | 暂不支持 |
| 26 | Iframe | 跳过 + 告警 | 安全考虑 |
| **27** | **Image** | **`![](minio_url)`** | **关键：URL 替换为 MinIO 公网地址** |
| 28 | ISV | 跳过 | 第三方应用块 |
| 29 | Mindnote | 跳过 + 告警 | |
| 30 | Sheet | 跳过 + 告警 | |
| **31 + 32** | **Table + TableCell** | **`\| col \| col \|\n\|---\|---\|\n\| cell \| cell \|`** | **递归拉子 cell 的 text_run** |
| 33 | View | 跳过 | |
| 34 | Quote Container | `> ` 包裹子块 | |
| 35 | Task | 跳过 + 告警 | |
| 49 | Source Synced | 渲染为 `> [同步自 xxx](url)` | 提示运营本地化 |
| 50 | Reference Synced | 递归拉取引用块内容 | 调 `reference_synced_block/get` |
| 51 | Sub Page List | 跳过 + 告警 | |
| 999 | Unsupported | 跳过 + 告警 | |

### 5.3 Code Block language 枚举（节选常用）

| 枚举值 | 语言 | Markdown lang |
|---|---|---|
| 39 | Markdown | `markdown` |
| 60 | Shell | `shell` |
| 50 | Python | `python` |
| 31 | JavaScript | `javascript` |
| 23 | Go | `go` |
| 67 | YAML | `yaml` |
| 28 | JSON | `json` |
| 1 | PlainText | `text` |

完整映射见 `feishu/docx_to_markdown.py:_CODE_LANG_MAP`。

### 5.4 富文本元素（elements[]）处理

每个 Block 的 `elements` 是富文本元素数组，按类型分别渲染：

| element type | Markdown |
|---|---|
| `text_run` | 直接拼接 content |
| `text_run` + `bold=true` | `**content**` |
| `text_run` + `italic=true` | `*content*` |
| `text_run` + `strikethrough=true` | `~~content~~` |
| `text_run` + `underline=true` | `<u>content</u>`（CommonMark 无原生下划线） |
| `text_run` + `inline_code=true` | `` `content` `` |
| `text_run` + `link.url` | `[content](url)` |
| `mention_user` | `@用户名` |
| `mention_doc` | `[doc_title](doc_url)` |
| `equation` | `$$content$$` |
| `file` | 跳过 + 告警 |
| `inline_block` | 跳过 + 告警 |

## 6. MediaDownloader（图片转 OSS）

### 6.1 问题

飞书 Image Block 返回的 URL 是**临时鉴权 URL**，有效期 ~24h，平台侧无法长期引用。

### 6.2 解决路径

1. docx_to_markdown 渲染时遇到 Image Block，先记录原始 URL 到 `pending_media: list[(block_id, url, filename)]`，返回占位符 `<IMAGE_PENDING:block_id>`
2. 主流程编排时，调 `media.py:download_and_upload()` 把每张图：
   - 调 `GET /open-apis/drive/v1/medias/:media_token/download`（带 app_access_token）拿二进制流
   - 上传到 MinIO bucket `kg-assets`，路径 `feishu/{feishu_doc_token}/{block_id}.{ext}`
   - 生成 MinIO 公网 URL（带签名，TTL 7 天）
   - 替换占位符为 `![](minio_url)`
3. 全部完成后，markdown 字符串才进入 P1 parser 流水线

### 6.3 限流

- 飞书 media 下载限流：5 QPS/应用（旧版）/ 未明确（新版）
- 实现：令牌桶，5 QPS 默认，可配

## 7. 同步主流程（sync.py）

### 7.1 触发入口

```python
async def sync_feishu_doc(
    source_doc_id: str,
    triggered_by: Literal["event", "poll", "manual"],
    cursor: str | None = None,  # sync_state.last_block_hash
) -> SyncResult:
    """主入口：被 MQ consumer 和 scheduler poll 共用 — 跑完整链路"""

async def sync_feishu_doc_phase1(
    source_doc_id: str,
    triggered_by: Literal["event", "poll", "manual", "bind"],
) -> Phase1Result:
    """首次绑定同步阻塞阶段：只跑到 P1 parser + 模板校验完成"""

async def sync_feishu_doc_phase2(
    source_doc_id: str,
    phase1_result: Phase1Result,
) -> Phase2Result:
    """异步阶段：重拆对齐 + 风险矩阵 + publish/review"""
```

### 7.2 同步主流程（两阶段拆分）

**关键设计**：主流程拆为同步阻塞阶段（首次绑定等待）和异步阶段（MQ consumer / poll 调用全链路），由 `bind_flow` 标志控制是否拆分。

#### 阶段一：同步阻塞（首次绑定等待，HTTP 等这一段，期望 < 30s）

1. **加载 source_doc**：校验 `source='feishu'` 且 `feishu_doc_token` 非空
2. **设置 status = 'syncing'**：写 `source_doc.sync_status='syncing'`，并发锁（PG advisory lock 防同 doc 重复同步）
3. **DocResolver**：调 `doc_resolver.resolve(feishu_url)` 拿到 `obj_type + document_token + title`（首次绑定时已存，此处校验 + 拿最新 title）
4. **权限预检**：
   - 调 `GET /open-apis/docx/v1/documents/:token` 拉元信息（便宜）
   - 失败码映射见 §4.4 表 → **抛出 FeishuPermissionError**，HTTP 直接 403
   - **不重试**，等运营修复权限后下次 poll/event 触发自动恢复
5. **拉 Block 树**：`GET /open-apis/docx/v1/documents/:document_token/blocks/:document_token/children?with_descendants=true`（限流 3 QPS）
6. **docx_to_markdown**：渲染 Markdown 字符串 + block_map + skipped_blocks
7. **MediaDownloader**：并发（限流 5 QPS）下载 + 上传所有图片，替换占位符
8. **P1 parser**：复用 `app/pipeline/parser.py:parse_markdown(markdown_str, doc_type=feishu_doc_type)`
9. **P1 模板校验**：复用 `app/pipeline/validators.py` 检测 blocking 错误
10. **落 import_batch 记录**：写入 PG（带 doc_type、parsed_items、validation_results）
11. **返回 Phase1Result**：含 parsed_items 数、blocking 数、skipped_blocks 数、import_batch_id

**HTTP 响应映射**：

| 阶段一结果 | HTTP 状态码 | 响应体 |
|---|---|---|
| 全部 OK（无 blocking） | 201 + Phase1Result | `next: "phase2 running"` |
| 模板 blocking 错误 | 422 + errors[] | `next: "wait for content fix"` |
| 权限失败 | 403 + permission_check | `next: "fix permission then retry"` |
| 飞书 5xx | 502 | `next: "feishu api error, retry"` |
| 阶段一超时（> 120s） | 202 | `sync_status: "syncing", poll_url: ...` |

#### 阶段二：异步（不阻塞 HTTP，后台跑）

12. **P1 align**：复用 `app/pipeline/align.py:align(parsed_items, existing_entries)` 得到 AlignedItem 列表
13. **risk_matrix**：对每个 AlignedItem 评分（见 §10）
14. **分派动作**：
    - low → `publish(mode='publish')` 自动生效（reuse P1 publish 逻辑）
    - mid/high → 写 `review_task` + `card.send_review_card()`
15. **更新 sync_state**：
    - `last_sync_at = now()`
    - `last_content_hash = sha256(markdown_str)`
    - `last_block_ids = json.dumps(block_ids)`（为 P3 增量同步做准备）
    - `sync_cursor = ?`（P2 暂用 last_sync_at，P3 改用 Block 序列号）
16. **设置 status**：`source_doc.sync_status='success'` 或 `'failed'`，写 `last_sync_error`

### 7.3 首次绑定 vs 事件/轮询 的调用差异

| 触发方式 | 调用入口 | 阶段一 | 阶段二 | HTTP 行为 |
|---|---|---|---|---|
| **首次绑定**（POST /api/source-docs） | `sync_phase1()` | 同步阻塞，等待结果 | 异步触发（不阻塞） | 等阶段一返回 201/422/403/202 |
| **事件回调**（MQ 消费） | `sync_phase1() + sync_phase2()` | 串行 | 串行 | MQ 内部，失败重试 3 次 |
| **轮询**（scheduler） | `sync_phase1() + sync_phase2()` | 串行 | 串行 | scheduler 内部，失败下次继续 |

**幂等保护**：阶段一与阶段二都用 `import_batch.id` 做幂等键，重放不会重复入库。

### 7.4 失败处理

| 失败点 | 处理 |
|---|---|
| DocResolver 解析失败 | `sync_status='failed'`，写 error，不重试 |
| 权限预检失败（阶段一） | 403 HTTP 返回，控制台弹窗 |
| 拉 Block 树失败（5xx / 限流） | 退避后重试，最多 3 次；仍失败 → 死信 topic `kg.feishu.event.dlq` |
| docx_to_markdown 跳过率 > 30% | 警告（不阻塞） + 控制台展示 |
| MediaDownloader 单图失败 | 跳过该图，markdown 留占位符 `![](PENDING)` + 告警；不影响其他图 |
| 阶段一超时（> 120s） | HTTP 202 + `sync_status='syncing'`，前端轮询 |
| P1 parser blocking 错误 | 422 HTTP 返回，运营改飞书原文后下次同步自动恢复 |
| 阶段二失败（重拆 / publish） | `sync_status='failed'` + 错误详情；source_doc 保留；下次 poll 自动重试 |
| risk_matrix 评估超时 | 默认中风险，走 review_task |

## 8. 事件回调（event.py）

### 8.1 路由

`POST /api/feishu/event`（挂在 console 路由下，因为用现有的 console OAuth）

### 8.2 校验

1. 解密 body（如果有 Encrypt Key）
2. 校验 Verification Token
3. 校验签名（飞书 v2 用 HMAC-SHA256）

### 8.3 事件类型处理

| event_type | action | 路由 |
|---|---|---|
| `drive.file.bitable_field_changed` / `drive.file.title_updated` / `drive.file.edited` | 触发 `sync_feishu_doc(source_doc_id, triggered_by='event')` | 入 MQ `kg.feishu.event` |
| `drive.file.deleted` | 调 `archive_source_doc(source_doc_id)` | 同步走，不入 MQ |
| URL verification challenge | 返回 challenge 字符串 | 直接回 |

**关键**：事件**只携带元信息**（file_token + event_type），**不带内容**。消费者必须按 file_token 反查 `source_doc` 找到 source_doc_id，再走 §7 主流程。

## 9. 轮询 worker（feishu_poll.py）

### 9.1 触发

scheduler 进程按 `KG_FEISHU_POLL_INTERVAL_SEC=300`（5 分钟）触发：

```python
async def feishu_poll_tick():
    # 找所有 active 的飞书 source_doc
    docs = await pg.fetch("SELECT id FROM source_doc WHERE source='feishu' AND active=true AND archived_at IS NULL")
    for doc in docs:
        # 判断是否需要拉：last_sync_at 距离现在 > interval 才拉
        if should_poll(doc):
            await sync_feishu_doc(doc.id, triggered_by='poll')
```

### 9.2 去重与并发

- 同一 doc **不允许 poll 和 event 并发**：用 PG advisory lock 串行化（§7.2 第 2 步）
- poll 间隔默认 5 分钟，但**首次绑定立即触发一次全量同步**——但**只等阶段一**（见 §7.2 / §7.3 表）
- poll 失败的 doc **下次 poll 继续尝试**（不被标记为永久失败，避免临时飞书故障导致永久不更新）

### 9.3 决策点 D2（已澄清）

- **D2**：首次绑定是否走异步任务？
  - ✅ **混合方案**（详见 §7.2 / §7.3）：
    - HTTP 等阶段一（权限预检 + 拉 Block + 渲染 + 解析 + 模板校验），期望 < 30s
    - 阶段二（重拆对齐 + 风险矩阵 + publish/review）后台异步跑
    - 阶段一失败 → HTTP 直接报错（403 / 422 / 502），不创建 source_doc 或不阻塞下次重试
    - 阶段一超时（> 120s）→ HTTP 202 + `sync_status='syncing'`，前端轮询
    - 阶段二失败 → source_doc 保留 + `sync_status='failed'`，下次 poll 自动重试

## 10. 风险矩阵（risk_matrix.py）

### 10.1 评分维度

| 维度 | low | mid | high |
|---|---|---|---|
| content_hash 是否变化 | 不变 | 变化 | — |
| 标题数差（new + disappeared 数量） | 0~2 | 3~5 | >5 |
| 新增敏感字段命中 | 无 | — | 有 |
| 高危动作词命中（policy / sop） | 无 | — | 有 |
| 跳过 Block 占比 | <5% | 5%~30% | >30% |
| 模板 blocking 错误 | 0 | 1~2 | >2 |
| 整体文档规模变化 | <20% | 20%~50% | >50% |

**评分规则**：任一维度为 high → high；否则取所有维度中最高的等级。

### 10.2 分派动作

- **low**：直接 `publish(mode='publish')` 自动生效，无人工介入（满足 roadmap P2 验收）
- **mid**：落 `review_task` + 推飞书卡片（待人工 approve）
- **high**：落 `review_task`（高优先级）+ 推飞书卡片 + 控制台红色标记

## 11. MQ 消息协议

### 11.1 生产

```python
# topic: kg.feishu.event
{
    "source_doc_id": "uuid",
    "feishu_doc_token": "abc123",
    "feishu_doc_type": "docx",
    "triggered_by": "event" | "poll" | "manual",
    "retry_count": 0,
    "enqueued_at": "2026-07-05T16:00:00+08:00"
}
```

### 11.2 消费幂等

- MQ consumer 在 PG 事务中更新 `sync_state` 时用 `(source_doc_id, last_content_hash)` 做唯一索引
- 重复消息直接跳过（return success，不重试）

### 11.3 重试与死信

- 失败重试 3 次，间隔 1min / 5min / 15min
- 仍失败 → topic `kg.feishu.event.dlq` + 控制台告警

### 11.4 决策点 D5（待拍板）

- **D5**：是否需要"同步优先级"区分？
  - 建议**暂不需要**：P2 都是同步优先级，P3 增量同步 + 用户手动触发"立即同步"按钮时再考虑

## 12. 数据模型（已有 + 待加字段）

### 12.1 `source_doc` 现有字段（来自 ADR-0022）

```python
class SourceDoc(Base):
    __tablename__ = "source_doc"
    id: str  # uuid
    domain: str  # 业务域
    name: str  # 文件名（与飞书 title 同步）
    type: str  # faq / sop / policy / product / case / term
    source: str  # 'manual' | 'upload' | 'feishu'
    active: bool
    archived_at: datetime | None
    feishu_folder_token: str | None  # P2（已有，目录挂载未启用）
```

### 12.2 需新增字段（alembic 0006 migration）

**D4 决策（已拍板）**：业务状态字段在 `source_doc`，技术状态字段在 `sync_state`，**两边都有**，通过 `source_doc_id` 关联。

```python
class SourceDoc(Base):
    # ... 已有字段
    feishu_doc_token: str | None          # 飞书 document_token
    feishu_doc_type: str | None           # 'docx' | 'doc' | 'wiki'
    feishu_url: str | None                # 飞书原文 URL
    # ── 业务状态字段（D4 拍板）──
    sync_status: str = 'pending'
        # 取值：
        #   pending              待首次同步
        #   syncing              同步中（阶段一/二）
        #   success              同步成功
        #   failed               同步失败（一般错误）
        #   awaiting_auth        等待运营飞书端授权（机器人未加知识库）
        #   permission_revoked   权限被撤销（曾同步过，现在失败）
        #   auth_timeout         授权超时（轮询 24h 未授权）
        #   archived             已归档（D6 软删除）
    last_sync_at: datetime | None         # 最近一次同步完成时间
    last_sync_error: str | None           # TEXT，最近一次错误详情
    sync_interval_sec: int | None        # D4.2 单文档覆盖，可空
        # NULL → 使用全局默认 KG_FEISHU_POLL_INTERVAL_SEC_DEFAULT
        # > 0   → 单文档轮询间隔（覆盖默认值）
```

```python
class SyncState(Base):
    __tablename__ = "sync_state"
    source_doc_id: str  # PK, FK -> source_doc.id
    feishu_doc_token: str
    feishu_doc_type: str
    feishu_title: str | None
    feishu_url: str | None
    # ── 技术状态字段（D4 拍板）──
    last_content_hash: str | None        # sha256(markdown)
    last_block_ids: str | None           # JSON array, P3 增量同步用
    last_sync_revision: int | None       # 飞书 revision_id，P3 增量同步
    last_sync_started_at: datetime | None  # 最近一次同步开始时间（区别于 source_doc.last_sync_at 的"完成时间"）
    created_at: datetime
    updated_at: datetime
```

**字段归属原则**：
- `source_doc.sync_status` / `last_sync_at` / `last_sync_error` —— **业务状态**，给运营看的，控制台展示
- `sync_state.last_content_hash` / `last_block_ids` / `last_sync_revision` / `last_sync_started_at` —— **技术状态**，给系统用的，幂等键 + P3 增量同步
- 两表通过 `source_doc_id` 一对一关联（PG 同步保证）

**D4.2 决策（已拍板）**：单文档可配 + 全局默认混合方案。
- 全局默认：`KG_FEISHU_POLL_INTERVAL_SEC_DEFAULT=300`（5 分钟），写入 .env
- 单文档覆盖：`source_doc.sync_interval_sec` 可空，空时使用全局
- 优先级：单文档值 > 全局默认值

### 12.3 `sync_state` 表（已有，P2 迁移已建）

```python
class SyncState(Base):
    __tablename__ = "sync_state"
    source_doc_id: str  # PK, FK -> source_doc.id
    feishu_doc_token: str
    feishu_doc_type: str
    feishu_title: str | None
    feishu_url: str | None
    last_content_hash: str | None       # sha256(markdown)
    last_block_ids: str | None          # JSON array, P3 增量同步用
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

## 13. 控制台接口契约

### 13.1 已有（来自 ADR-0022）

- `POST /api/source-docs`：创建（支持 `source='feishu'` + `feishu_url`）
- `GET /api/source-docs`：列表
- `GET /api/source-docs/:id`：详情
- `PATCH /api/source-docs/:id`：改名 / 改 type / archive
- `DELETE /api/source-docs/:id`：删除（仅 source='manual' 允许）

### 13.2 需新增（P2）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/source-docs/:id/sync` | POST | 手动触发同步（operator 按钮"立即同步"） |
| `/api/source-docs/:id/sync-status` | GET | 查询最近一次同步状态 + 错误 |
| `/api/source-docs/:id/sync-history` | GET | 历史同步记录（limit 20） |
| `/api/feishu/event` | POST | 飞书事件回调（gateway 公开） |
| `/api/source-docs/resolve` | POST | 输入飞书 URL，返回 ResolvedDoc + 权限预检结果（绑定前预览） |

**`/api/source-docs/resolve` 响应体**：

```json
{
  "resolved": true,
  "feishu_doc_type": "docx",
  "feishu_doc_token": "abc123",
  "feishu_url": "https://xxx.feishu.cn/docx/abc123",
  "title": "退货流程 SOP",
  "permission_check": {
    "ok": true,
    "error_code": null,
    "error_message": null
  }
}
// 失败时：
{
  "resolved": true,
  "permission_check": {
    "ok": false,
    "error_code": "feishu_app_not_in_kb",
    "error_message": "应用未被授权到此文档所在知识库",
    "action_guide": "请在飞书客户端打开文档所属知识库，将「知识平台机器人」添加为「仅阅读」成员"
  }
}
```

绑定（`POST /api/source-docs`）时若 `permission_check.ok=false`，**返回 403 + 错误详情**，不创建 source_doc。

### 13.3 决策点 D6 / D7 / D8（已拍板）

- **D6**：飞书文档删除 / 归档时平台侧怎么处理？
  - ✅ **软删除 30 天可恢复**：
    - 检测到飞书 `is_deleted=true` 或 404 → `source_doc.archived_at=now()`，`sync_status='archived'`
    - 控制台显示「已归档 - 可恢复」，30 天内可手动"恢复归档"
    - 30 天后由 scheduler 物理删除条目 + 清掉 `sync_state`（保留 `source_doc` 记录）
- **D7**：wiki / drive 是否同套？
  - ✅ **是**：DocResolver 统一处理，按 `feishu_doc_type`（'docx' / 'doc' / 'wiki'）路由不同 endpoint
- **D8**：运营解绑飞书后已同步知识怎么处理？
  - ✅ **保留本地副本**：
    - 控制台"解绑飞书"操作：`source_doc.source='manual'` + 清空 `feishu_*` 字段 + 清空 `sync_state`
    - 条目保留为本地副本（ADR-0022 已支持 source 切换，文档条目不变）
    - 运营可继续在控制台编辑这些条目

## 14. 限流策略

| 接口 | 限流 | 实现 |
|---|---|---|
| docx block 拉取 | 3 QPS/应用 + 3 并发/文档 | `asyncio.Semaphore(3)` + 令牌桶 |
| drive media 下载 | 5 QPS/应用 | 令牌桶 |
| drive file 下载 | 5 QPS/应用 | 令牌桶 |
| 飞书卡片发送 | 5 QPS/应用 | im/v1/messages 默认限流 |

**令牌桶实现**：`app/feishu/rate_limiter.py`，单例 AsyncTokenBucket，全应用共享。

## 15. 已知边界（P2 不做）

| 项 | 影响 | 计划 |
|---|---|---|
| 增量 Block 同步 | 每次全量拉 Block 树，10 万级文档量会触发限流 | P3 用 last_block_ids 做 diff |
| 飞书评论 / @ 提及导入 | 飞书 API 不支持 | 不做 |
| 飞书表格（Sheet）导入 | 暂不支持 | P3 评估 |
| Synced Block 跨文档引用解析 | 渲染为占位文本 | P3 |
| 飞书端权限变更感知 | 用户撤销机器人权限后仍能拉到缓存内容 | P3 加权限校验 |
| 飞书协作文档（多人同时编辑）的冲突解决 | 飞书侧自动解决，平台侧只接最终版本 | 不做 |

## 16. 测试策略

### 16.1 单元测试（必须 100% 覆盖）

- `docx_to_markdown`：每种 Block type 一个 fixture + 断言
- `doc_resolver`：三种 URL 形式各一个用例
- `media`：mock MinIO + 飞书 media API
- `risk_matrix`：各维度组合
- `event`：签名校验 + 解密
- `sync`：mock 整条链路（飞书 → parser → publish），验证分支

### 16.2 集成测试

- 用飞书测试企业 + 测试 docx 跑一次完整同步
- 验证：source_doc 创建 → MQ 触发 → 同步 → 重拆对齐 → publish（或 review_task 落库）

### 16.3 性能测试

- 单文档 1000 Block 同步 < 30s（不含图片）
- 100 张图文档 < 2min
- 100 个绑定文档并发轮询，应用 QPS 不超 3

## 17. 配置项（.env）

```bash
# 飞书 OAuth
KG_LARK_APP_ID=cli_xxx
KG_LARK_APP_SECRET=xxx
KG_LARK_ENCRYPT_KEY=xxx          # 事件订阅 Encrypt Key
KG_LARK_VERIFICATION_TOKEN=xxx   # 事件订阅 Verification Token

# 飞书轮询（D4.2 拍板：全局默认 + 单文档可覆盖）
KG_FEISHU_POLL_INTERVAL_SEC_DEFAULT=300  # 默认 5 分钟
KG_FEISHU_POLL_INTERVAL_SEC_MAX=86400    # 单文档覆盖上限 24h（防止运营误配）
KG_FEISHU_POLL_INTERVAL_SEC_MIN=60       # 单文档覆盖下限 1min（保护飞书 API）

# RocketMQ
KG_ROCKETMQ_NAME_SRV=localhost:9876
KG_ROCKETMQ_TOPIC_FEISHU_EVENT=kg.feishu.event
KG_ROCKETMQ_TOPIC_FEISHU_EVENT_DLQ=kg.feishu.event.dlq
KG_ROCKETMQ_CONSUMER_GROUP_FEISHU=kg_feishu_consumer

# MinIO（图片转存）
KG_OSS_ENDPOINT=localhost:9000
KG_OSS_ACCESS_KEY=minio
KG_OSS_SECRET_KEY=minio123
KG_OSS_BUCKET=kg-assets
KG_OSS_PUBLIC_BASE_URL=http://localhost:9000/kg-assets

# 限流
KG_FEISHU_BLOCK_QPS=3
KG_FEISHU_MEDIA_QPS=5

# 诊断式验证（D9.5~D9.7）
KG_FEISHU_AUTH_POLL_INTERVAL_SEC=60      # awaiting_auth 轮询间隔
KG_FEISHU_AUTH_TIMEOUT_HOURS=24          # 授权超时阈值

# MQ 消费
KG_FEISHU_CONSUMER_WORKERS=2
```

## 18. 部署与启动验证

参考 `doc/local-dev-p2.md`：
- docker-compose 启动 RocketMQ + MinIO
- 飞书侧权限申请：docx:readonly / wiki:readonly / drive:readonly / drive:media:readonly / im:message:send_as_bot / event 订阅
- 事件回调 URL：`https://<your-domain>/api/feishu/event`（需要公网可达或内网穿透）
- 启动验证：手动调 `POST /api/source-docs` 创建一个 `source='feishu'` 的 source_doc → 等 5 分钟看是否自动同步成功

## 19. 决策汇总

### 19.1 已拍板

| ID | 决策点 | 拍板结果 |
|---|---|---|
| D1.1 | 飞书文档 picker | ❌ 不需要（粘贴 URL 够用） |
| D1.2 | 控制台搜索飞书文档列表 | ❌ 不做 |
| D2 | 首次绑定同步阻塞 | 混合方案：等阶段一（< 30s），阶段二异步 |
| D4 | sync_status 字段归属 | ✅ **两边都有**：业务状态放 `source_doc`，技术状态放 `sync_state`（详见 §12.2） |
| D4.2 | 单文档轮询间隔可配 | ✅ **混合方案**：全局默认 `KG_FEISHU_POLL_INTERVAL_SEC_DEFAULT=300` + 单文档 `source_doc.sync_interval_sec` 可空覆盖 |
| D5 | 同步优先级展示 | ❌ 不需要（控制台不展示优先级字段） |
| D6 | 飞书删除后处理 | ✅ **软删除 30 天可恢复**：标 `archived_at`，30 天内可手动恢复，30 天后物理删除 |
| D7 | wiki/drive 是否同套 | ✅ **是**：DocResolver 按 `feishu_doc_type` 路由 |
| D8 | 运营解绑 | ✅ **保留本地副本**：`source='manual'` + 清空飞书字段 + 保留条目 |
| D9.1 | 飞书 deep link 直达成员管理 | ❌ 不做（飞书没有 deep link） |
| D9.2 | 权限预检失败时是否创建 source_doc | ❌ 否（避免脏数据） |
| D9.3 | 权限被撤销后平台侧感知 | ✅ **立即告警 + 暂停同步 + 重新开启方式**（详见 §4.8） |
| D9.4 | 运营授权后平台感知策略 | ✅ **双轨（按钮 + 60s 轮询 24h）** |
| D9.5 | 诊断时是否用 user_access_token | ✅ **是（精准诊断，与控制台 OAuth 复用）** |
| D9.6 | 轮询 24h 超时后状态 | ✅ **`'auth_timeout'`** |
| D9.7 | 诊断式验证是否 P2 必须 | ✅ **P2 必须**（~1.5 天工作量） |

> ✅ **全部决策拍板完成**（D1~D9 全套），§4 权限章节闭环。下一步进入 §5 Block 渲染器代码实现。

### 19.3 已澄清（无需拍板，但记录决策依据）

| ID | 决策点 | 说明 |
|---|---|---|
| D2 | 首次绑定阻塞的边界 | 等阶段一（权限预检 + 拉 Block + 渲染 + 图片 + 解析 + 模板校验），**不等**阶段二（重拆 + 风险矩阵 + publish/review）。HTTP 超时（> 120s）返回 202 + 轮询 status |
| D9 | 飞书 OAuth 不能做资源授权 | OAuth 仅用于用户登录拿 `user_access_token`，不能授权机器人访问文档。资源授权必须人工在飞书客户端操作"添加文档应用"或"知识库添加成员" |

## 20. 关联文档

- [pipeline.md](pipeline.md)：复用 P1 流水线（parser / align / publish / sensitive / content_hash）
- [domain.md](domain.md)：条目状态机、kid 规则
- [storage.md](storage.md)：PG 表结构、ORM
- [console.md](console.md)：控制台接口、权限
- [scheduler.md](scheduler.md)：scheduler 进程集成
- ADR-0015：飞书三原则
- ADR-0022：source_doc 设计
- ADR-0009：重拆对齐规则
- ADR-0006：LLM 分级触发原则（飞书同步流暂不调 LLM，留给 P3）
- [local-dev-p2.md](../local-dev-p2.md)：P2 飞书权限与启动验证