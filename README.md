# 知识管理平台（kg-platform）

面向 Agent 的公司级知识中台。设计与实现依据：

- [设计文档](https://my.feishu.cn/docx/C3dsddOUdojwZRxyypPc98pHnth)（评审已通过）
- [技术设计文档（MVP）](https://my.feishu.cn/docx/F2RzduAxQoPUXax8mfucbePanLf)（本仓库结构与之对应，代码注释中的"技术设计文档 x.x"均指该文档章节）

## 结构

```
backend/    Python + FastAPI：Gateway（/v1/*）+ 控制台 API（/api/*）+ 流水线 + scheduler
frontend/   React + Antd 控制台（Vite）
docker-compose.dev.yml   本地 PG + Redis（OpenViking 待 PoC 后补充）
```

## 本地启动

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

容器内绑定 0.0.0.0 时 OpenViking 强制要求鉴权：`ov.conf` 已配 `server.auth_mode="api_key"` +
`server.root_api_key`，后端通过 `KG_VIKING_API_KEY` 携带同一把 key（两处默认值一致，改动需同步）。

改完配置后 `docker compose -f docker-compose.dev.yml restart openviking`。
容器内 `openviking-server doctor` 可做配置自检。
注意：模型配置留占位值时服务能启动、/health 正常，但写入时 L0/L1 摘要与向量化会失败——
跑通全链路前必须填入真实可用的 embedding / VLM 模型。

## 测试

```bash
cd backend && uv run pytest
```

## 当前状态（脚手架）

已实现：错误 envelope 与 400/401 契约、请求 ID 中间件、状态机、kid 生成、
敏感正则、content_hash 规范化、P1 全部 DDL（alembic 0001）、限流器、健康检查。

待实现（P1，按技术文档章节）：API Key 鉴权比对（十）、OpenViking 客户端落地（九，
依赖 PoC 结论）、Markdown 解析与模板校验（8.1/8.2）、发布事务（8.4）、
控制台各接口（七）与前端页面接入、审计消费协程（十一）、scheduler 三个任务体。
