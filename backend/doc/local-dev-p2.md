# P2 本地基建与飞书权限

> **关联**：`docker-compose.dev.yml`、`backend/.env.example`、roadmap Phase 2

## Docker 服务

| 服务 | 端口 | 用途 |
|-|-|-|
| `rocketmq-namesrv` | 9876 | RocketMQ NameServer |
| `rocketmq-broker` | 10909 / 10911 / 10912 | Broker（流水线投递、飞书事件、审核卡片超时） |
| `minio` | 9000（API）/ 9001（Console） | S3 兼容对象存储，bucket `kg-assets` |
| `minio-init` | — | 一次性建 bucket（`restart: no`） |

启动：

```bash
docker compose -f docker-compose.dev.yml up -d
```

验证：

```bash
# RocketMQ NameServer
nc -z localhost 9876

# MinIO 健康
curl -sf http://localhost:9000/minio/health/live

# MinIO Console（浏览器）
open http://localhost:9001   # 账号 kgminio / kgminio123
```

环境变量见 `backend/.env.example` 中 `KG_ROCKETMQ_*` 与 `KG_OSS_*`。P1 后端进程**不连接** MQ/OSS 亦可正常运行；P2 联调时再接入客户端。

### RocketMQ Topic（P2 业务接入时创建）

| Topic | 环境变量 | 用途 |
|-|-|-|
| `kg.pipeline` | `KG_ROCKETMQ_TOPIC_PIPELINE` | 飞书同步流水线异步投递 |
| `kg.feishu.event` | `KG_ROCKETMQ_TOPIC_FEISHU_EVENT` | 飞书事件回调消费 |
| `kg.review.card` | `KG_ROCKETMQ_TOPIC_REVIEW_CARD` | 审核卡片超时 |

本地 broker 已开 `autoCreateTopicEnable=true`，首次发送时自动建 topic。

## 飞书自建应用权限

P1 控制台 OAuth 仅需用户身份；**P2 飞书同步**需在[飞书开放平台](https://open.feishu.cn/app)为同一应用追加权限并重新发布版本。

### P1 已有（登录）

| 权限 | 说明 |
|-|-|
| 获取用户 userid（`contact:user.base:readonly` 或等效） | OAuth 换 open_id / 姓名 |

回调地址：`https://<控制台域名>/api/auth/callback`（本地 dev-login 可跳过）。

### P2 追加

| 权限 scope | 用途 |
|-|-|
| `docx:document:readonly` | 读取云文档正文（docx） |
| `wiki:wiki:readonly` | 读取知识库节点与文档 |
| `drive:drive:readonly` | 列举/元数据 |
| `drive:file:download` | 下载文档内图片/附件（转存 OSS） |
| 事件订阅（文档变更） | 低延迟触发同步；须配置请求 URL 与 Encrypt Key |

**事件 + 轮询双通道**（ADR-0015）：事件仅作触发，正文仍靠 OpenAPI 拉取；轮询兜底不可省。

### 配置 checklist

1. 创建/复用自建应用，记录 `App ID` / `App Secret` → `KG_LARK_APP_ID` / `KG_LARK_APP_SECRET`
2. 权限管理 → 添加上表 scope → **创建版本并发布**
3. 事件订阅 → 订阅「文档」类变更 → 请求 URL 指向 P2 网关（本地可用 ngrok 等隧道）
4. 安全设置 → 重定向 URL 含控制台域名
5. （可选）domain 表 `feishu_folder_token` / `reviewer_user_id` 在控制台 domain 配置页维护

## PostgreSQL P2 表

迁移 `0005_p2_review_sync`：

- **`review_task`** — 审核待办（risk / manual_fill / conflict 三 tab）
- **`sync_state`** — 飞书单文档注册与同步游标

详见 [modules/storage.md](modules/storage.md) § P2 表。
