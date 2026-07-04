# 知识管理平台（kg-platform）

面向 Agent 的公司级知识中台。设计与实现依据：

- [设计文档](https://my.feishu.cn/docx/C3dsddOUdojwZRxyypPc98pHnth)（评审已通过）
- [技术设计文档（MVP）](https://my.feishu.cn/docx/F2RzduAxQoPUXax8mfucbePanLf)（本仓库结构与之对应，代码注释中的"技术设计文档 x.x"均指该文档章节）

仓库内文档见 [backend/doc/](backend/doc/README.md)：按代码模块组织的设计文档、项目规划（roadmap）与 ADR 决策记录，每篇标注对应飞书章节可溯源。

## 结构

```
backend/    Python + FastAPI：Gateway（/v1/*）+ 控制台 API（/api/*）+ 流水线 + scheduler
frontend/   React + Antd 控制台（Vite）
docker-compose.dev.yml   本地 PG + Redis（OpenViking 待 PoC 后补充）
```

## 本地启动

推荐使用脚本一键启动前后端与本地依赖：

```bash
./scripts/dev.sh
```

脚本会自动：

- 启动 Docker 中间件（PG + Redis + OpenViking）
- 缺失时从示例文件复制 `backend/.env` 与 `openviking/ov.conf`
- 执行 `uv sync` 与 Alembic 迁移
- 缺失 `node_modules` 时执行 `npm install`
- 并行启动后端 `:8000` 与前端 `:5173`

常用开关：

```bash
START_DOCKER=0 ./scripts/dev.sh      # 不启动 Docker 中间件
RUN_MIGRATIONS=0 ./scripts/dev.sh   # 跳过数据库迁移
INSTALL_DEPS=0 ./scripts/dev.sh     # 跳过前端依赖安装检查
BACKEND_PORT=8001 ./scripts/dev.sh  # 使用其他后端端口
```

手动启动方式：

```bash
# 1. 依赖中间件（PG + Redis + OpenViking，全部 Docker）
cp openviking/ov.conf.example openviking/ov.conf   # 填入模型配置，见下节
docker compose -f docker-compose.dev.yml up -d
curl http://localhost:1933/health                  # OpenViking 就绪检查

# 2. 后端
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head        # 建表（P1 全部 DDL + common 域种子数据）
uv run uvicorn app.main:app --reload --port 8000
# 另一终端（可选）：uv run python -m app.scheduler.main

# 3. 前端
cd frontend
npm install
npm run dev                        # http://localhost:5173，/api 与 /v1 代理到 :8000
```

验证：`curl http://localhost:8000/healthz`；接口文档 http://localhost:8000/docs

## OpenViking 本地部署说明

官方镜像 `ghcr.io/volcengine/openviking:latest`，HTTP 服务默认端口 1933，配置目录挂载
`./openviking → /app/.openviking`（其中 `ov.conf` 为配置、`workspace/` 为数据，均已 gitignore）。

`ov.conf` 必须配置 embedding 与 VLM 两个模型（L0/L1 摘要生成依赖，技术设计文档 九），
支持任意 OpenAI 兼容 endpoint，二选一：

- **公司模型网关**：`api_base` 填网关地址、`api_key` 填个人配额 key（推荐，与线上一致）；
- **纯本地**：跑 Ollama 后 `provider` 填 `ollama`、`api_base` 填 `http://host.docker.internal:11434/v1`，
  embedding 用 `bge-m3` 等本地模型（无网也能开发，但摘要质量与线上有差异）。

⚠️ **embedding 必须显式声明 `dimension`**（如 bge-m3 为 1024、text-embedding-3-large 为 3072）：
不声明时 OpenViking 按默认维度建向量集合，与模型实际输出不一致会导致写入报
`Dense vector dimension mismatch` 且检索崩溃；**集合维度在 workspace 初始化时冻结**，
更换 embedding 模型或修正维度后必须清空 `workspace/` 重建（PoC 实测结论，无在线迁移）。

容器内绑定 0.0.0.0 时 OpenViking 强制要求鉴权：`ov.conf` 已配 `server.auth_mode="api_key"` +
`server.root_api_key`。⚠️ **ROOT key 不能访问数据 API**（PoC 实测 403）——须先用 root key
创建租户拿**用户级 key** 配到 `KG_VIKING_API_KEY`：

```bash
curl -X POST -H "x-api-key: dev-local-root-key" -H "Content-Type: application/json" \
  -d '{"account_id":"kg","admin_user_id":"kg-backend"}' \
  http://localhost:1933/api/v1/admin/accounts   # 响应中的 user_key 即用户级 key
```

改完配置后 `docker compose -f docker-compose.dev.yml restart openviking`。
容器内 `openviking-server doctor` 可做配置自检。
注意：模型配置留占位值时服务能启动、/health 正常，但写入时 L0/L1 摘要与向量化会失败——
跑通全链路前必须填入真实可用的 embedding / VLM 模型。

## 测试

```bash
cd backend && uv run pytest
```

## 当前状态（P1 完成，2026-07-04）

P1 全链路已实现并端到端跑通：表单/Markdown 录入 → 校验流水线 → 发布 →
OpenViking 入库 → Agent `/v1/search` + `/v1/knowledge/{kid}` → 审计留存；
控制台 22 个接口 + 前端六页全部接通。进度与遗留项见 [backend/doc/roadmap.md](backend/doc/roadmap.md)。

## 本地联调（无飞书凭证时）

生产登录走飞书 OAuth；本地可用开发登录后门（默认关闭）：

```bash
# 起后端时显式开启
KG_DEV_LOGIN_ENABLED=1 KG_VIKING_API_KEY=<用户级key> uv run uvicorn app.main:app --port 8000
# 浏览器访问（经前端代理），自动建用户并种 session cookie
open "http://localhost:5173/api/auth/dev-login?user_id=dev&platform_admin=true"
```
